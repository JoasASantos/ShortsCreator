"""HeyGen client — talking avatar built from the short's script.

API: https://docs.heygen.com — base https://api.heygen.com, auth via the
`X-Api-Key` header. Flow: POST /v2/video/generate -> video_id -> poll
GET /v1/video_status.get?video_id=... until completed -> download video_url.
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


def _listing(payload: dict, key: str) -> list[dict]:
    """The items out of a listing response, whichever shape it arrives in.

    The v2 endpoints wrap them (`data.avatars`, `data.voices`); the v3 ones
    that replace them return a flat `data[]`. Reading both means the picker
    keeps working across that switch instead of quietly going empty — v2 is
    documented as operational only until 2026-11-01.
    """
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [item for item in (data.get(key) or []) if isinstance(item, dict)]
    return []


def verify(creds: dict) -> str:
    r = httpx.get(f"{BASE}/v2/avatars", headers=_headers(creds), timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError("Key rejected by HeyGen")
    r.raise_for_status()
    avatars = _listing(r.json(), "avatars")
    return f"HeyGen responding — {len(avatars)} avatars available"


def list_avatars(creds: dict) -> list[dict]:
    r = httpx.get(f"{BASE}/v2/avatars", headers=_headers(creds), timeout=TIMEOUT)
    r.raise_for_status()
    return _listing(r.json(), "avatars")


def list_voices(creds: dict) -> list[dict]:
    r = httpx.get(f"{BASE}/v2/voices", headers=_headers(creds), timeout=TIMEOUT)
    r.raise_for_status()
    return _listing(r.json(), "voices")


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
    log("heygen: requesting avatar video")
    r = httpx.post(f"{BASE}/v2/video/generate", headers=_headers(creds),
                   json=body, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError("Key rejected by HeyGen")
    r.raise_for_status()
    payload = r.json().get("data") or {}
    video_id = payload.get("video_id")
    if not video_id:
        raise RuntimeError(f"HeyGen returned no video_id: {r.json()}")

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
                raise RuntimeError("HeyGen completed but returned no video_url")
            with httpx.stream("GET", url, timeout=TIMEOUT) as resp:
                resp.raise_for_status()
                with out_path.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
            log("heygen: video downloaded")
            return out_path
        if status == "failed":
            # A failure carries `failure_message`/`failure_code` on the status
            # payload; `error` was only ever the older name. Reading all three
            # keeps the reason from rendering as "None", which is the one
            # outcome nobody can act on.
            reason = (info.get("failure_message") or info.get("failure_code")
                      or info.get("error") or "no reason given")
            raise RuntimeError(f"HeyGen: generation failed — {reason}")
        time.sleep(POLL_INTERVAL)
    raise RuntimeError("HeyGen: timed out waiting for the generation to finish")
