"""Higgsfield client — AI video generation for the short's background.

API: https://docs.higgsfield.ai/docs — base https://api.higgsfield.ai,
auth `Authorization: Key {key_id}:{key_secret}`. The flow is always the same:
POST to the chosen model -> {request_id, status_url} -> poll
GET /requests/{request_id}/status until completed/failed -> download
`video.url`.

The models exposed here are the ones that take text->video in native 9:16
(aspect_ratio) or are vertical by default; that covers the essentials without
replicating Higgsfield's entire catalog (~50 models).
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

BASE = "https://api.higgsfield.ai"
TIMEOUT = 60.0
POLL_INTERVAL = 4.0
POLL_TIMEOUT = 480.0

# short id -> (path, aspect-ratio field, 9:16 value)
MODELS = {
    "seedance-lite": ("/bytedance/seedance/v1/lite/text-to-video", "aspect_ratio", "9:16"),
    "seedance-pro": ("/bytedance/seedance/v1/pro/fast/text-to-video", "aspect_ratio", "9:16"),
    "hailuo-standard": ("/minimax/hailuo-02/standard/text-to-video", None, None),
    "hailuo-pro": ("/minimax/hailuo-02/pro/text-to-video", None, None),
    "kling-2.5-pro": ("/kling-video/v2.5-turbo/pro/text-to-video", None, None),
    "sora-2": ("/sora-2/text-to-video", None, None),
    "wan-2.5": ("/wan-25-preview/text-to-video", None, None),
}
DEFAULT_MODEL = "seedance-lite"


def _headers(creds: dict) -> dict:
    key_id = creds.get("key_id", "")
    key_secret = creds.get("key_secret", "")
    return {"Authorization": f"Key {key_id}:{key_secret}",
            "Content-Type": "application/json"}


def verify(creds: dict) -> str:
    """The cheapest possible call, just to prove the key pair is good: it
    fires a short generation and cancels it right after."""
    path, ratio_field, ratio_value = MODELS[DEFAULT_MODEL]
    body = {"prompt": "connection test, static shot of a city"}
    if ratio_field:
        body[ratio_field] = ratio_value
    r = httpx.post(f"{BASE}{path}", headers=_headers(creds), json=body,
                   timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError("Key/secret rejected by Higgsfield")
    r.raise_for_status()
    data = r.json()
    cancel_url = data.get("cancel_url")
    if cancel_url:
        try:
            httpx.post(cancel_url, headers=_headers(creds), timeout=10)
        except httpx.HTTPError:
            pass
    return "Higgsfield responding — test generation accepted and canceled"


def generate_clip(prompt: str, duration: float, out_path: Path, creds: dict,
                  model: str = DEFAULT_MODEL, log=lambda m: None) -> Path:
    if model not in MODELS:
        model = DEFAULT_MODEL
    path, ratio_field, ratio_value = MODELS[model]
    body: dict = {"prompt": prompt}
    if ratio_field:
        body[ratio_field] = ratio_value
    log(f"higgsfield: requesting clip ({model}) — {prompt[:60]}")
    r = httpx.post(f"{BASE}{path}", headers=_headers(creds), json=body,
                   timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError("Key/secret rejected by Higgsfield")
    r.raise_for_status()
    data = r.json()
    status_url = data.get("status_url") or f"{BASE}/requests/{data['request_id']}/status"

    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        s = httpx.get(status_url, headers=_headers(creds), timeout=TIMEOUT)
        s.raise_for_status()
        info = s.json()
        state = info.get("status")
        if state == "completed":
            video = info.get("video") or {}
            url = video.get("url") if isinstance(video, dict) else None
            if not url:
                raise RuntimeError("Higgsfield completed but returned no video URL")
            with httpx.stream("GET", url, timeout=TIMEOUT) as resp:
                resp.raise_for_status()
                with out_path.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
            log("higgsfield: clip downloaded")
            return out_path
        if state in ("failed", "nsfw", "canceled"):
            raise RuntimeError(f"Higgsfield: generation ended in '{state}' "
                              f"({info.get('error') or 'no detail'})")
        time.sleep(POLL_INTERVAL)
    raise RuntimeError("Higgsfield: timed out waiting for the generation to finish")
