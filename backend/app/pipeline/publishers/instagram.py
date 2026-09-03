"""Reels publishing through the Instagram Graph API.

A three-step flow, as Meta requires:
  1. POST /{ig_user_id}/media  (media_type=REELS, public video_url) -> container
  2. GET  /{container}?fields=status_code until FINISHED (Meta downloads and
     transcodes it)
  3. POST /{ig_user_id}/media_publish (creation_id) -> post id

The API accepts no direct upload: it DOWNLOADS the video. That is why
PUBLIC_API_URL has to be reachable from the internet — on a local machine, a
tunnel (ngrok, cloudflared) pointing at port 8000 does the job.
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
    return f"@{data.get('username', '?')} · {data.get('followers_count', '?')} followers"


def profile_name(creds: dict) -> str:
    """Profile name. Raises if the token is no good — the caller uses that to
    decide whether the publishable account should exist at all."""
    r = httpx.get(f"{GRAPH}/{creds.get('ig_user_id', '')}",
                  params={"fields": "username",
                          "access_token": creds.get("access_token", "")},
                  timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(_error(r))
    username = r.json().get("username")
    if not username:
        raise RuntimeError("The Graph API returned no username for the account")
    return f"@{username}"


def public_video_url(job_id: str) -> str:
    base = settings.public_api_url.rstrip("/")
    if "localhost" in base or "127.0.0.1" in base:
        raise RuntimeError(
            "Instagram needs to download the video from a public URL. Set "
            "PUBLIC_API_URL in .env to an address reachable from the internet "
            "(e.g. an ngrok/cloudflared tunnel to port 8000)."
        )
    return f"{base}/api/outputs/{job_id}.mp4"


def upload(video: Path, payload: dict, credentials: dict, account_id: str) -> dict:
    token = credentials.get("access_token")
    ig_user = credentials.get("ig_user_id")
    if not token or not ig_user:
        raise RuntimeError("Instagram account without an access_token/ig_user_id")

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
        raise RuntimeError(f"Instagram rejected the container: {_error(created)}")
    container = created.json()["id"]

    _wait_container(container, token)

    published = httpx.post(f"{GRAPH}/{ig_user}/media_publish",
                           data={"creation_id": container, "access_token": token},
                           timeout=TIMEOUT)
    if published.status_code >= 400:
        raise RuntimeError(f"Instagram rejected the publication: {_error(published)}")
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
    """Meta transcodes asynchronously; publishing before FINISHED gives error 9007."""
    for _ in range(tries):
        r = httpx.get(f"{GRAPH}/{container}",
                      params={"fields": "status_code,status", "access_token": token},
                      timeout=TIMEOUT)
        data = r.json()
        status = data.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Instagram failed to process the video: "
                               f"{data.get('status', '')[:300]}")
        time.sleep(6)
    raise RuntimeError("Instagram did not finish processing the video in 3 minutes")


def insights(media_id: str, token: str) -> dict:
    """Metrics for a Reel. Some fields require the video to be a few days old."""
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
        # ig_reels_avg_watch_time comes in milliseconds
        "avg_view_seconds": (out.get("ig_reels_avg_watch_time") or 0) / 1000 or None,
    }


def _error(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
        return f"{err.get('message', r.text[:200])} (code {err.get('code', r.status_code)})"
    except Exception:  # noqa: BLE001
        return r.text[:300]
