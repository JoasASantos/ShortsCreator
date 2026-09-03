"""Native video post on LinkedIn through the Posts API (versioned).

  1. POST /rest/videos?action=initializeUpload -> uploadUrl + video URN
  2. PUT the whole file to the uploadUrl (the ETag comes back in the header)
  3. POST /rest/videos?action=finalizeUpload with the ETag
  4. POST /rest/posts with content.media.id = the video's URN

Requires a token with `w_member_social`. If `author_urn` is not supplied, it
is discovered through /v2/userinfo (which requires `openid` and `profile`).
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

API = "https://api.linkedin.com"
VERSION = "202409"
TIMEOUT = 90.0


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def resolve_author(creds: dict) -> str:
    urn = (creds.get("author_urn") or "").strip()
    if urn:
        return urn
    r = httpx.get(f"{API}/v2/userinfo",
                  headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
                  timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(
            "No author_urn, and the token lacks the openid/profile scope to "
            f"discover it on its own ({r.status_code}). Supply the URN under "
            "Accounts.")
    return f"urn:li:person:{r.json()['sub']}"


def verify(creds: dict) -> str:
    urn = resolve_author(creds)
    try:
        r = httpx.get(f"{API}/v2/userinfo",
                      headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
                      timeout=TIMEOUT)
        name = r.json().get("name", "")
    except Exception:  # noqa: BLE001
        name = ""
    return f"Token valid for {name or urn}"


def profile_name(creds: dict) -> str:
    """Author name. Raises if the token is no good.

    A token holding only `w_member_social` cannot read the profile; in that
    case the hand-supplied URN already identifies the account and serves as
    the name.
    """
    r = httpx.get(f"{API}/v2/userinfo",
                  headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
                  timeout=TIMEOUT)
    if r.status_code == 200:
        name = r.json().get("name")
        if name:
            return name
    urn = (creds.get("author_urn") or "").strip()
    if urn:
        return urn
    raise RuntimeError(
        f"Token rejected by LinkedIn ({r.status_code}) and no author_urn to "
        "identify the account. Supply the URN under Accounts.")


def upload(video: Path, payload: dict, credentials: dict, account_id: str) -> dict:
    token = credentials.get("access_token")
    if not token:
        raise RuntimeError("LinkedIn account without an access_token")
    author = resolve_author(credentials)
    headers = _headers(token)
    size = video.stat().st_size

    init = httpx.post(
        f"{API}/rest/videos?action=initializeUpload", headers=headers,
        json={"initializeUploadRequest": {
            "owner": author, "fileSizeBytes": size,
            "uploadCaptions": False, "uploadThumbnail": False}},
        timeout=TIMEOUT,
    )
    if init.status_code >= 400:
        raise RuntimeError(f"LinkedIn rejected the upload: {init.text[:300]}")
    value = init.json()["value"]
    video_urn = value["video"]
    instructions = value["uploadInstructions"]

    etags = []
    with video.open("rb") as fh:
        for part in instructions:
            first, last = part["firstByte"], part["lastByte"]
            fh.seek(first)
            chunk = fh.read(last - first + 1)
            put = httpx.put(part["uploadUrl"], content=chunk,
                            headers={"Content-Type": "application/octet-stream"},
                            timeout=900)
            put.raise_for_status()
            etags.append(put.headers.get("etag", ""))

    fin = httpx.post(
        f"{API}/rest/videos?action=finalizeUpload", headers=headers,
        json={"finalizeUploadRequest": {
            "video": video_urn, "uploadToken": value.get("uploadToken", ""),
            "uploadedPartIds": etags}},
        timeout=TIMEOUT,
    )
    if fin.status_code >= 400:
        raise RuntimeError(f"LinkedIn did not finalize the upload: {fin.text[:300]}")

    _wait_available(video_urn, headers)

    title = payload.get("title") or "Short"
    text = payload.get("description") or title
    hashtags = payload.get("tags", [])
    if hashtags:
        text = f"{text}\n\n" + " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)

    visibility = "PUBLIC" if payload.get("privacy", "private") == "public" else "CONNECTIONS"
    post = httpx.post(
        f"{API}/rest/posts", headers=headers,
        json={
            "author": author,
            "commentary": text[:3000],
            "visibility": visibility,
            "distribution": {"feedDistribution": "MAIN_FEED",
                             "targetEntities": [], "thirdPartyDistributionChannels": []},
            "content": {"media": {"title": title[:200], "id": video_urn}},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        },
        timeout=TIMEOUT,
    )
    if post.status_code >= 400:
        raise RuntimeError(f"LinkedIn rejected the post: {post.text[:300]}")
    post_urn = post.headers.get("x-restli-id", "")
    return {"platform": "linkedin", "video_id": post_urn or video_urn,
            "url": f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else "",
            "video_urn": video_urn}


def _wait_available(video_urn: str, headers: dict, tries: int = 30) -> None:
    for _ in range(tries):
        r = httpx.get(f"{API}/rest/videos/{httpx.QueryParams({'u': video_urn})['u']}",
                      headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            status = r.json().get("status")
            if status == "AVAILABLE":
                return
            if status == "PROCESSING_FAILED":
                raise RuntimeError("LinkedIn failed to process the video")
        time.sleep(5)
    raise RuntimeError("LinkedIn did not finish processing the video in time")
