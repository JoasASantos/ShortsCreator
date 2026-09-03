"""Upload to YouTube Shorts via the YouTube Data API v3 (resumable upload)."""
from __future__ import annotations

import json
from pathlib import Path

from ... import db
from ...config import settings

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          # average retention per video — the basis of the scriptwriter's learning
          "https://www.googleapis.com/auth/yt-analytics.readonly"]


def auth_url_flow(redirect_uri: str):
    from google_auth_oauthlib.flow import Flow

    secrets = Path(settings.youtube_client_secrets)
    if not secrets.exists():
        raise RuntimeError(
            f"YouTube client_secret not found at {secrets}. "
            "Download it from the Google Cloud Console (OAuth 2.0 Client ID, "
            "Web type)."
        )
    flow = Flow.from_client_secrets_file(str(secrets), scopes=SCOPES,
                                         redirect_uri=redirect_uri)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent",
                                    include_granted_scopes="true")
    return url, flow


def exchange_code(code: str, redirect_uri: str) -> dict:
    _, flow = auth_url_flow(redirect_uri)
    flow.fetch_token(code=code)
    creds = flow.credentials
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def _credentials(data: dict):
    from google.oauth2.credentials import Credentials
    return Credentials(**data)


def channel_name(credentials: dict) -> str:
    from googleapiclient.discovery import build

    service = build("youtube", "v3", credentials=_credentials(credentials))
    response = service.channels().list(part="snippet", mine=True).execute()
    items = response.get("items", [])
    return items[0]["snippet"]["title"] if items else "YouTube channel"


def upload(video: Path, payload: dict, credentials: dict, account_id: str) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = _credentials(credentials)
    service = build("youtube", "v3", credentials=creds)

    title = (payload.get("title") or "Short")[:100]
    description = payload.get("description", "")
    hashtags = payload.get("tags", [])
    if hashtags:
        description = f"{description}\n\n" + " ".join(
            h if h.startswith("#") else f"#{h}" for h in hashtags
        )
    # #Shorts in the title/description reinforces classification as a Short
    if "#shorts" not in description.lower():
        description = f"{description}\n#Shorts".strip()

    status: dict = {"privacyStatus": payload.get("privacy", "private"),
                    "selfDeclaredMadeForKids": False}
    if payload.get("publish_at"):
        status["privacyStatus"] = "private"
        status["publishAt"] = payload["publish_at"]

    body = {
        "snippet": {
            "title": title,
            "description": description[:5000],
            "tags": [h.lstrip("#") for h in hashtags][:15],
            "categoryId": payload.get("category_id", "28"),
        },
        "status": status,
    }

    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024,
                            resumable=True, mimetype="video/mp4")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    if creds.token != credentials.get("token"):
        credentials["token"] = creds.token
        _persist_token(account_id, credentials)

    video_id = response["id"]
    result = {"platform": "youtube", "video_id": video_id,
              "url": f"https://youtube.com/shorts/{video_id}",
              "status": response.get("status", {})}

    # A custom thumbnail requires a phone-verified channel; without one the API
    # returns 403 and the Short keeps the automatic frame — not a reason to fail.
    cover = payload.get("cover_path")
    if cover and Path(cover).exists():
        try:
            service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(cover, mimetype="image/jpeg"),
            ).execute()
            result["cover"] = "uploaded"
        except Exception as exc:  # noqa: BLE001
            result["cover"] = f"not applied: {str(exc)[:160]}"
    return result


def _persist_token(account_id: str, credentials: dict) -> None:
    with db._lock, db.connect() as conn:  # noqa: SLF001 — one-off update
        conn.execute("UPDATE accounts SET credentials_json=? WHERE id=?",
                     (json.dumps(credentials), account_id))
