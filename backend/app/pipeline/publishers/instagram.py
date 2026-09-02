"""Publicação de Reels pela Instagram Graph API.

Fluxo em três passos, como a Meta exige:
  1. POST /{ig_user_id}/media  (media_type=REELS, video_url pública) -> container
  2. GET  /{container}?fields=status_code até FINISHED (a Meta baixa e transcodifica)
  3. POST /{ig_user_id}/media_publish (creation_id) -> id do post

A API não aceita upload direto: ela BAIXA o vídeo. Por isso PUBLIC_API_URL
precisa ser alcançável da internet — em máquina local, um túnel (ngrok,
cloudflared) apontando pra porta 8000 resolve.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from ...config import settings

GRAPH = "https://graph.facebook.com/v21.0"
TIMEOUT = 60.0


def verify(creds: dict) -> str:
    r = httpx.get(f"{GRAPH}/{creds.get('ig_user_id', '')}",
                  params={"fields": "username,followers_count",
                          "access_token": creds.get("access_token", "")},
                  timeout=TIMEOUT)
    if r.status_code in (400, 401, 403):
        raise RuntimeError(_error(r))
    r.raise_for_status()
    data = r.json()
    return f"@{data.get('username', '?')} · {data.get('followers_count', '?')} seguidores"


def display_name(creds: dict) -> str:
    try:
        r = httpx.get(f"{GRAPH}/{creds['ig_user_id']}",
                      params={"fields": "username", "access_token": creds["access_token"]},
                      timeout=TIMEOUT)
        return "@" + r.json().get("username", "instagram")
    except Exception:  # noqa: BLE001
        return "Conta Instagram"


def public_video_url(job_id: str) -> str:
    base = settings.public_api_url.rstrip("/")
    if "localhost" in base or "127.0.0.1" in base:
        raise RuntimeError(
            "Instagram precisa baixar o vídeo por uma URL pública. Defina "
            "PUBLIC_API_URL no .env com um endereço alcançável da internet "
            "(ex.: túnel ngrok/cloudflared para a porta 8000)."
        )
    return f"{base}/api/outputs/{job_id}.mp4"


def upload(video: Path, payload: dict, credentials: dict, account_id: str) -> dict:
    token = credentials.get("access_token")
    ig_user = credentials.get("ig_user_id")
    if not token or not ig_user:
        raise RuntimeError("Conta Instagram sem access_token/ig_user_id")

    video_url = public_video_url(payload["job_id"])
    caption = payload.get("description") or payload.get("title") or ""
    hashtags = payload.get("tags", [])
    if hashtags:
        caption = f"{caption}\n\n" + " ".join(
            h if h.startswith("#") else f"#{h}" for h in hashtags)

    body = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption[:2200],
        "share_to_feed": "true",
        "access_token": token,
    }
    cover_url = payload.get("cover_url")
    if cover_url:
        body["cover_url"] = cover_url

    created = httpx.post(f"{GRAPH}/{ig_user}/media", data=body, timeout=TIMEOUT)
    if created.status_code >= 400:
        raise RuntimeError(f"Instagram recusou o container: {_error(created)}")
    container = created.json()["id"]

    _wait_container(container, token)

    published = httpx.post(f"{GRAPH}/{ig_user}/media_publish",
                           data={"creation_id": container, "access_token": token},
                           timeout=TIMEOUT)
    if published.status_code >= 400:
        raise RuntimeError(f"Instagram recusou a publicação: {_error(published)}")
    media_id = published.json()["id"]

    permalink = ""
    try:
        info = httpx.get(f"{GRAPH}/{media_id}",
                         params={"fields": "permalink", "access_token": token},
                         timeout=TIMEOUT)
        permalink = info.json().get("permalink", "")
    except Exception:  # noqa: BLE001
        pass

    return {"platform": "instagram", "video_id": media_id, "url": permalink,
            "container_id": container}


def _wait_container(container: str, token: str, tries: int = 30) -> None:
    """A Meta transcodifica assincronamente; publicar antes de FINISHED dá erro 9007."""
    for _ in range(tries):
        r = httpx.get(f"{GRAPH}/{container}",
                      params={"fields": "status_code,status", "access_token": token},
                      timeout=TIMEOUT)
        data = r.json()
        status = data.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Instagram falhou ao processar o vídeo: "
                               f"{data.get('status', '')[:300]}")
        time.sleep(6)
    raise RuntimeError("Instagram não terminou de processar o vídeo em 3 minutos")


def insights(media_id: str, token: str) -> dict:
    """Métricas de um Reel. Alguns campos exigem que o vídeo tenha alguns dias."""
    r = httpx.get(f"{GRAPH}/{media_id}/insights",
                  params={"metric": "plays,likes,comments,shares,saved,ig_reels_avg_watch_time",
                          "access_token": token}, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(_error(r))
    out: dict = {}
    for item in r.json().get("data", []):
        values = item.get("values") or [{}]
        out[item["name"]] = values[0].get("value", 0)
    return {
        "views": out.get("plays", 0),
        "likes": out.get("likes", 0),
        "comments": out.get("comments", 0),
        "shares": out.get("shares", 0),
        # ig_reels_avg_watch_time vem em milissegundos
        "avg_view_seconds": (out.get("ig_reels_avg_watch_time") or 0) / 1000 or None,
    }


def _error(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
        return f"{err.get('message', r.text[:200])} (código {err.get('code', r.status_code)})"
    except Exception:  # noqa: BLE001
        return r.text[:300]
