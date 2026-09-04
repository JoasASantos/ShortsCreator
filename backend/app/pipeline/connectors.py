"""Catalog of external connectors + credential custody.

A connector is any third-party service ShortsCreator knows how to call:
publishing (YouTube, TikTok...), AI video generation (Higgsfield), talking
avatar (HeyGen), voice (Fish Audio, ElevenLabs) and b-roll stock banks
(Pexels, Pixabay).

Two credential sources, in this order:
  1. the `connector_credentials` table in the database — what the Accounts
     screen writes;
  2. an environment variable from .env — keeps working for anyone who had
     already configured it there before this screen existed.

Whoever consumes a credential must call `credentials(<id>)` instead of reading
settings directly, so that this precedence is respected.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .. import db
from ..config import settings

TIMEOUT = 25.0


@dataclass
class Field:
    key: str
    label: str
    env: str = ""          # equivalent environment variable (fallback)
    secret: bool = True
    hint: str = ""


@dataclass
class Connector:
    id: str
    name: str
    # publicacao: posts the finished short | video: generates moving imagery
    # avatar: talking presenter | voz: TTS | broll: stock footage bank
    # notificacao: warns when the job finishes or the publication goes out
    category: str
    auth: str              # "oauth" | "api_key"
    detail: str
    docs: str
    status: str = "pronto"  # pronto | beta | planejado
    fields: list[Field] = field(default_factory=list)
    requirement: str = ""
    # Function that makes a real, cheap call just to prove the credential is
    # good. Returns a short piece of text to show on screen.
    check: Callable[[dict], str] | None = None


# ------------------------------------------------------------------- checks

def _get(url: str, headers: dict, params: dict | None = None) -> httpx.Response:
    return httpx.get(url, headers=headers, params=params, timeout=TIMEOUT,
                     follow_redirects=True)


def _check_higgsfield(creds: dict) -> str:
    from .generators import higgsfield

    return higgsfield.verify(creds)


def _check_heygen(creds: dict) -> str:
    from .generators import heygen

    return heygen.verify(creds)


def _check_elevenlabs(creds: dict) -> str:
    key = creds.get("api_key", "")
    r = _get("https://api.elevenlabs.io/v1/user", {"xi-api-key": key})
    if r.status_code == 401:
        raise RuntimeError("Key rejected by ElevenLabs (401)")
    r.raise_for_status()
    tier = (r.json().get("subscription") or {}).get("tier", "?")
    return f"Valid ElevenLabs account ({tier} plan)"


def _check_fishaudio(creds: dict) -> str:
    key = creds.get("api_key", "")
    r = _get("https://api.fish.audio/model",
             {"Authorization": f"Bearer {key}"}, {"page_size": 1})
    if r.status_code in (401, 403):
        raise RuntimeError("Key rejected by Fish Audio")
    r.raise_for_status()
    total = r.json().get("total", "?")
    return f"Fish Audio responding ({total} voices in the catalog)"


def _check_pexels(creds: dict) -> str:
    r = _get("https://api.pexels.com/videos/search",
             {"Authorization": creds.get("api_key", "")},
             {"query": "city", "per_page": 1})
    if r.status_code == 401:
        raise RuntimeError("Key rejected by Pexels (401)")
    r.raise_for_status()
    return "Pexels responding — video b-roll enabled"


def _check_pixabay(creds: dict) -> str:
    r = _get("https://pixabay.com/api/videos/", {},
             {"key": creds.get("api_key", ""), "q": "city", "per_page": 3})
    if r.status_code in (400, 401):
        raise RuntimeError("Key rejected by Pixabay")
    r.raise_for_status()
    return "Pixabay responding — video b-roll enabled"


def _check_coverr(creds: dict) -> str:
    r = _get("https://api.coverr.co/videos",
             {}, {"api_key": creds.get("api_key", ""), "page_size": 1})
    if r.status_code in (401, 403):
        raise RuntimeError("Key rejected by Coverr")
    r.raise_for_status()
    return "Coverr responding — cinematic b-roll available"


def _check_tiktok_app(creds: dict) -> str:
    if not creds.get("client_key") or not creds.get("client_secret"):
        raise RuntimeError("Missing client_key/client_secret")
    return "TikTok app configured — connect the account with the Connect button"


def _check_instagram(creds: dict) -> str:
    from .publishers import instagram

    return instagram.verify(creds)


def _check_linkedin(creds: dict) -> str:
    from .publishers import linkedin

    return linkedin.verify(creds)


def _check_telegram(creds: dict) -> str:
    from . import notify

    return notify.check_telegram(creds)


def _check_discord(creds: dict) -> str:
    from . import notify

    return notify.check_discord(creds)


def _check_webhook(creds: dict) -> str:
    from . import notify

    return notify.check_webhook(creds)


# ------------------------------------------------------------------- catalog

CATALOG: list[Connector] = [
    Connector(
        id="youtube", name="YouTube Shorts", category="publicacao", auth="oauth",
        detail="Resumable upload through the Data API v3. Supports native "
               "scheduling (publishAt) and per-video privacy.",
        requirement="OAuth client_secret at data/secrets/youtube_client_secret.json",
        docs="https://developers.google.com/youtube/v3/guides/uploading_a_video",
    ),
    Connector(
        id="tiktok", name="TikTok", category="publicacao", auth="oauth",
        detail="Content Posting API v2. An unaudited account lands in the "
               "app's draft inbox; once the audit is approved, it publishes "
               "straight away.",
        requirement="TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET",
        docs="https://developers.tiktok.com/doc/content-posting-api-get-started",
        fields=[Field("client_key", "Client key", "TIKTOK_CLIENT_KEY", secret=False),
                Field("client_secret", "Client secret", "TIKTOK_CLIENT_SECRET")],
        check=_check_tiktok_app,
    ),
    Connector(
        id="higgsfield", name="Higgsfield", category="video", auth="api_key",
        detail="Generates the short's background with AI (Sora 2, Veo 3.1, "
               "Kling 2.5, Seedance, Hailuo) already in 9:16. Powers the "
               "'ia_video' background mode.",
        requirement="Key pair created at cloud.higgsfield.ai",
        docs="https://docs.higgsfield.ai/docs",
        fields=[Field("key_id", "API key id", "HIGGSFIELD_KEY_ID", secret=False),
                Field("key_secret", "API key secret", "HIGGSFIELD_KEY_SECRET")],
        check=_check_higgsfield,
    ),
    Connector(
        id="heygen", name="HeyGen", category="avatar", auth="api_key",
        detail="Talking presenter built from the script. The generated clip "
               "comes in as an attachment and can become the short's source "
               "video.",
        requirement="API key from the HeyGen account (Settings > API)",
        docs="https://docs.heygen.com/reference/create-an-avatar-video-v2",
        fields=[Field("api_key", "API key", "HEYGEN_API_KEY")],
        check=_check_heygen,
    ),
    Connector(
        id="fishaudio", name="Fish Audio", category="voz", auth="api_key",
        detail="Character and narration voices from the Fish catalog. Already "
               "the provider behind the voices installed on the Voices screen.",
        requirement="FISHAUDIO_API_KEY",
        docs="https://docs.fish.audio/overview/capabilities",
        fields=[Field("api_key", "API key", "FISHAUDIO_API_KEY")],
        check=_check_fishaudio,
    ),
    Connector(
        id="elevenlabs", name="ElevenLabs", category="voz", auth="api_key",
        detail="Alternative TTS with per-character timestamps — the provider "
               "with the most accurate karaoke captions after edge-tts.",
        requirement="ELEVENLABS_API_KEY",
        docs="https://elevenlabs.io/docs/api-reference",
        fields=[Field("api_key", "API key", "ELEVENLABS_API_KEY")],
        check=_check_elevenlabs,
    ),
    Connector(
        id="pexels", name="Pexels", category="broll", auth="api_key",
        detail="Free video bank used for the automatic background (the 'broll' "
               "background) when the job brings no media of its own.",
        requirement="PEXELS_API_KEY",
        docs="https://www.pexels.com/api/documentation/",
        fields=[Field("api_key", "API key", "PEXELS_API_KEY")],
        check=_check_pexels,
    ),
    Connector(
        id="pixabay", name="Pixabay", category="broll", auth="api_key",
        detail="Second b-roll bank. Steps in as a fallback when Pexels has no "
               "result for the segment's query.",
        requirement="PIXABAY_API_KEY",
        docs="https://pixabay.com/api/docs/",
        fields=[Field("api_key", "API key", "PIXABAY_API_KEY")],
        check=_check_pixabay,
    ),
    Connector(
        id="coverr", name="Coverr", category="broll", auth="api_key",
        detail="Third b-roll bank, mostly cinematic loopable footage — good "
               "filler when the other two return literal stock imagery.",
        requirement="COVERR_API_KEY (free key at coverr.co)",
        docs="https://api.coverr.co/docs",
        fields=[Field("api_key", "API key", "COVERR_API_KEY")],
        check=_check_coverr,
    ),
    Connector(
        id="instagram", name="Instagram Reels", category="publicacao",
        auth="api_key",
        detail="Reels publishing through the Graph API (container + publish). "
               "Requires a professional account linked to a Facebook page, and "
               "the API has to download the MP4 from a public URL — "
               "PUBLIC_API_URL must be reachable from the internet (ngrok, "
               "cloudflared...).",
        requirement="Long-lived token + IG User ID",
        docs="https://developers.facebook.com/docs/instagram-api/guides/content-publishing",
        fields=[Field("access_token", "Access token", "INSTAGRAM_ACCESS_TOKEN"),
                Field("ig_user_id", "IG user id", "INSTAGRAM_USER_ID", secret=False)],
        check=_check_instagram,
    ),
    Connector(
        id="linkedin", name="LinkedIn", category="publicacao", auth="api_key",
        detail="Native video post through the Posts API. Useful for the "
               "technology and security niches, where reach is better there.",
        requirement="Access token with w_member_social (plus openid/profile to "
                    "discover the URN on its own)",
        docs="https://learn.microsoft.com/linkedin/marketing/community-management/shares/videos-api",
        fields=[Field("access_token", "Access token", "LINKEDIN_ACCESS_TOKEN"),
                Field("author_urn", "Author URN", "LINKEDIN_AUTHOR_URN", secret=False,
                      hint="urn:li:person:xxxx — empty = discovered from the token")],
        check=_check_linkedin,
    ),
    Connector(
        id="telegram", name="Telegram", category="notificacao", auth="api_key",
        detail="Notice when a short finishes, fails or gets published. Create a "
               "bot with @BotFather, send it /start and grab your chat_id from "
               "@userinfobot.",
        requirement="Bot token + chat_id",
        docs="https://core.telegram.org/bots/api#sendmessage",
        fields=[Field("bot_token", "Bot token", "TELEGRAM_BOT_TOKEN"),
                Field("chat_id", "Chat id", "TELEGRAM_CHAT_ID", secret=False)],
        check=_check_telegram,
    ),
    Connector(
        id="discord", name="Discord", category="notificacao", auth="api_key",
        detail="The same notices, in a Discord channel through an integration "
               "webhook.",
        requirement="Channel webhook URL",
        docs="https://discord.com/developers/docs/resources/webhook#execute-webhook",
        fields=[Field("webhook_url", "Webhook URL", "DISCORD_WEBHOOK_URL")],
        check=_check_discord,
    ),
    Connector(
        id="webhook", name="Generic webhook", category="notificacao", auth="api_key",
        detail="POST JSON {title, body, url, level} to any URL — n8n, Zapier, "
               "Make or your own service.",
        requirement="A URL that accepts POST",
        docs="https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/",
        fields=[Field("url", "URL", "NOTIFY_WEBHOOK_URL", secret=False)],
        check=_check_webhook,
    ),
    # Below: planned, not implemented yet. They are listed on purpose — the
    # key can be stored now and the usage lands later.
    Connector(
        id="runway", name="Runway Gen-4", category="video", auth="api_key",
        status="planejado",
        detail="Alternative video generator to Higgsfield, with fine-grained "
               "camera control. Routes through the same 'ia_video' background "
               "mode.",
        requirement="RUNWAY_API_KEY",
        docs="https://docs.dev.runwayml.com",
        fields=[Field("api_key", "API key", "RUNWAY_API_KEY")],
    ),
    Connector(
        id="did", name="D-ID", category="avatar", auth="api_key",
        status="planejado",
        detail="Talking avatar from a single photo — a cheaper alternative to "
               "HeyGen when the face is a picture of you.",
        requirement="DID_API_KEY",
        docs="https://docs.d-id.com",
        fields=[Field("api_key", "API key", "DID_API_KEY")],
    ),
]

BY_ID = {c.id: c for c in CATALOG}


def get(connector_id: str) -> Connector:
    if connector_id not in BY_ID:
        raise KeyError(f"Unknown connector: {connector_id}")
    return BY_ID[connector_id]


# --------------------------------------------------------------- credentials

def credentials(connector_id: str) -> dict:
    """Effective credential: what is in the database beats what is in .env."""
    connector = get(connector_id)
    saved = db.get_connector(connector_id) or {}
    out: dict[str, str] = {}
    for f in connector.fields:
        value = saved.get(f.key) or (os.getenv(f.env, "") if f.env else "")
        if value:
            out[f.key] = value
    return out


def source(connector_id: str) -> str:
    """Where the credential came from — the screen shows this to avoid
    confusion when .env and the database disagree."""
    connector = get(connector_id)
    saved = db.get_connector(connector_id) or {}
    if any(saved.get(f.key) for f in connector.fields):
        return "painel"
    if any(f.env and os.getenv(f.env) for f in connector.fields):
        return "env"
    return ""


def is_configured(connector_id: str) -> bool:
    connector = get(connector_id)
    if connector.auth == "oauth" and not connector.fields:
        return bool(_oauth_ready(connector_id))
    creds = credentials(connector_id)
    return all(f.key in creds for f in connector.fields)


def _oauth_ready(connector_id: str) -> bool:
    if connector_id == "youtube":
        from pathlib import Path

        return Path(settings.youtube_client_secrets).exists()
    return False


def describe(connector_id: str) -> dict:
    c = get(connector_id)
    accounts = [a for a in db.list_accounts() if a["platform"] == c.id]
    return {
        "id": c.id, "name": c.name, "category": c.category, "auth": c.auth,
        "detail": c.detail, "requirement": c.requirement, "docs": c.docs,
        "status": c.status, "configured": is_configured(c.id),
        "source": source(c.id), "testable": c.check is not None,
        "accounts": len(accounts),
        "fields": [
            {"key": f.key, "label": f.label, "secret": f.secret, "env": f.env,
             "hint": f.hint, "filled": bool(credentials(c.id).get(f.key))}
            for f in c.fields
        ],
    }


def describe_all() -> list[dict]:
    return [describe(c.id) for c in CATALOG]


def save(connector_id: str, values: dict) -> dict:
    """Stores only the declared fields. An empty value clears the field (falling
    back to .env)."""
    connector = get(connector_id)
    saved = dict(db.get_connector(connector_id) or {})
    for f in connector.fields:
        if f.key not in values:
            continue
        value = (values[f.key] or "").strip()
        if value:
            saved[f.key] = value
        else:
            saved.pop(f.key, None)
    db.save_connector(connector_id, saved)
    _sync_token_account(connector_id)
    return describe(connector_id)


def clear(connector_id: str) -> dict:
    get(connector_id)
    db.delete_connector(connector_id)
    if connector_id in TOKEN_PUBLISHERS:
        db.delete_accounts_for_platform(connector_id)
    return describe(connector_id)


# Publishers whose "account" is the token saved in the connector itself — they
# have no OAuth redirect flow. On save, they immediately become a publishable
# account.
TOKEN_PUBLISHERS = {"instagram", "linkedin"}


def _sync_token_account(connector_id: str) -> None:
    """Creates/updates the publishable account — but only if the token answers.

    Fetching the profile name validates the credential for free. Without this,
    a wrong token turned into an account with a generic name that only failed
    at publish time; now the connector stays saved, the account does not show
    up, and "Test connection" explains why.
    """
    if connector_id not in TOKEN_PUBLISHERS or not is_configured(connector_id):
        return
    creds = credentials(connector_id)
    if connector_id == "instagram":
        from .publishers import instagram

        module = instagram
    else:
        from .publishers import linkedin

        module = linkedin

    try:
        name = module.profile_name(creds)
    except Exception:  # noqa: BLE001 — invalid token or service down
        db.delete_accounts_for_platform(connector_id)
        return
    db.upsert_account_for_platform(connector_id, name, creds)


def test(connector_id: str) -> str:
    connector = get(connector_id)
    if connector.check is None:
        raise RuntimeError(f"{connector.name} has no automated test yet")
    creds = credentials(connector_id)
    missing = [f.label for f in connector.fields if not creds.get(f.key)]
    if missing:
        raise RuntimeError(f"Missing: {', '.join(missing)}")
    return connector.check(creds)
