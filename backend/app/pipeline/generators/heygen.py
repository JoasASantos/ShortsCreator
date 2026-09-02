"""Cliente HeyGen — avatar falante a partir do roteiro do short.

API: https://docs.heygen.com — base https://api.heygen.com, auth via
header `X-Api-Key`. Fluxo: POST /v2/video/generate -> video_id -> poll em
GET /v1/video_status.get?video_id=... até completed -> baixa video_url.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

BASE = "https://api.heygen.com"
TIMEOUT = 60.0
POLL_INTERVAL = 5.0
POLL_TIMEOUT = 600.0


def _headers(creds: dict) -> dict:
    return {"X-Api-Key": creds.get("api_key", ""), "Content-Type": "application/json"}


def verify(creds: dict) -> str:
    r = httpx.get(f"{BASE}/v2/avatars", headers=_headers(creds), timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError("Chave recusada pelo HeyGen")
    r.raise_for_status()
    avatars = (r.json().get("data") or {}).get("avatars", [])
    return f"HeyGen respondendo — {len(avatars)} avatares disponíveis"


def list_avatars(creds: dict) -> list[dict]:
    r = httpx.get(f"{BASE}/v2/avatars", headers=_headers(creds), timeout=TIMEOUT)
    r.raise_for_status()
    return (r.json().get("data") or {}).get("avatars", [])


def list_voices(creds: dict) -> list[dict]:
    r = httpx.get(f"{BASE}/v2/voices", headers=_headers(creds), timeout=TIMEOUT)
    r.raise_for_status()
    return (r.json().get("data") or {}).get("voices", [])


def generate_avatar_video(script_text: str, avatar_id: str, voice_id: str,
                          out_path: Path, creds: dict, aspect: str = "9:16",
                          log=lambda m: None) -> Path:
    width, height = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    body = {
        "video_inputs": [{
            "character": {"type": "avatar", "avatar_id": avatar_id,
                         "avatar_style": "normal"},
            "voice": {"type": "text", "input_text": script_text,
                     "voice_id": voice_id},
            "background": {"type": "color", "value": "#000000"},
        }],
        "dimension": {"width": width, "height": height},
    }
    log("heygen: pedindo vídeo de avatar")
    r = httpx.post(f"{BASE}/v2/video/generate", headers=_headers(creds),
                   json=body, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError("Chave recusada pelo HeyGen")
    r.raise_for_status()
    payload = r.json().get("data") or {}
    video_id = payload.get("video_id")
    if not video_id:
        raise RuntimeError(f"HeyGen não retornou video_id: {r.json()}")

    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        s = httpx.get(f"{BASE}/v1/video_status.get",
                      headers=_headers(creds), params={"video_id": video_id},
                      timeout=TIMEOUT)
        s.raise_for_status()
        info = s.json().get("data") or {}
        status = info.get("status")
        if status == "completed":
            url = info.get("video_url")
            if not url:
                raise RuntimeError("HeyGen concluiu mas não trouxe video_url")
            with httpx.stream("GET", url, timeout=TIMEOUT) as resp:
                resp.raise_for_status()
                with out_path.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
            log("heygen: vídeo baixado")
            return out_path
        if status == "failed":
            raise RuntimeError(f"HeyGen: geração falhou — {info.get('error')}")
        time.sleep(POLL_INTERVAL)
    raise RuntimeError("HeyGen: tempo limite esperando a geração terminar")
