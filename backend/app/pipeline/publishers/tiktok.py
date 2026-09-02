"""Upload para TikTok via Content Posting API v2 (FILE_UPLOAD direto)."""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from ...config import settings

AUTH_BASE = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
SCOPES = "user.info.basic,video.upload,video.publish"


def auth_url(state: str) -> str:
    params = {
        "client_key": settings.tiktok_client_key,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": settings.tiktok_redirect_uri,
        "state": state,
    }
    query = "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())
    return f"{AUTH_BASE}?{query}"


def exchange_code(code: str) -> dict:
    resp = httpx.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.tiktok_redirect_uri,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def refresh(refresh_token: str) -> dict:
    resp = httpx.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def creator_info(access_token: str) -> dict:
    resp = httpx.post(
        "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
        headers={"Authorization": f"Bearer {access_token}",
                 "Content-Type": "application/json; charset=UTF-8"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


def upload(video: Path, payload: dict, credentials: dict, account_id: str) -> dict:
    access_token = credentials.get("access_token")
    if not access_token:
        raise RuntimeError("Conta TikTok sem access_token")

    size = video.stat().st_size
    headers = {"Authorization": f"Bearer {access_token}",
               "Content-Type": "application/json; charset=UTF-8"}

    # Contas em modo sandbox/unaudited só conseguem enviar para o inbox de rascunhos.
    direct_post = bool(payload.get("direct_post", False))
    endpoint = ("https://open.tiktokapis.com/v2/post/publish/video/init/"
                if direct_post else
                "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/")

    body: dict = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,
            "total_chunk_count": 1,
        }
    }
    if direct_post:
        body["post_info"] = {
            "title": (payload.get("title") or "")[:2200],
            "privacy_level": payload.get("privacy_level", "SELF_ONLY"),
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000,
        }

    init = httpx.post(endpoint, headers=headers, json=body, timeout=90)
    init.raise_for_status()
    data = init.json().get("data", {})
    upload_url = data.get("upload_url")
    publish_id = data.get("publish_id")
    if not upload_url:
        raise RuntimeError(f"TikTok não devolveu upload_url: {init.text[:300]}")

    with video.open("rb") as fh:
        put = httpx.put(
            upload_url,
            headers={"Content-Type": "video/mp4",
                     "Content-Length": str(size),
                     "Content-Range": f"bytes 0-{size - 1}/{size}"},
            content=fh.read(),
            timeout=900,
        )
    put.raise_for_status()

    status = _wait_status(publish_id, access_token)
    return {"platform": "tiktok", "publish_id": publish_id,
            "direct_post": direct_post, "status": status}


def _wait_status(publish_id: str, access_token: str, tries: int = 10) -> dict:
    for _ in range(tries):
        time.sleep(6)
        resp = httpx.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json; charset=UTF-8"},
            json={"publish_id": publish_id},
            timeout=60,
        )
        if resp.status_code != 200:
            continue
        data = resp.json().get("data", {})
        if data.get("status") in ("PUBLISH_COMPLETE", "FAILED", "SEND_TO_USER_INBOX"):
            return data
    return {"status": "PROCESSING"}
