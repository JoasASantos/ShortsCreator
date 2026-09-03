"""End-of-pipeline and publication notices — Telegram, Discord or webhook.

All optional: with no credential configured, `send` becomes a no-op. Each
destination is a connector from the Accounts screen (category "notificacao"),
with a fallback in .env. Network errors here must never bring down the job
that has already finished.

The notice texts themselves stay in Portuguese: they are messages to the end
user, not developer-facing strings.
"""
from __future__ import annotations

import threading

import httpx

from .. import db
from . import connectors

TIMEOUT = 12.0


def _targets() -> list[tuple[str, dict]]:
    out = []
    for connector_id in ("telegram", "discord", "webhook"):
        try:
            creds = connectors.credentials(connector_id)
        except KeyError:
            continue
        if connectors.is_configured(connector_id):
            out.append((connector_id, creds))
    return out


def is_configured() -> bool:
    return bool(_targets())


def send(title: str, body: str = "", url: str = "", level: str = "info") -> None:
    """Fires in the background; the worker thread moves on without waiting on
    the network."""
    targets = _targets()
    if not targets:
        return
    threading.Thread(target=_deliver, args=(targets, title, body, url, level),
                     daemon=True).start()


def _deliver(targets, title: str, body: str, url: str, level: str) -> None:
    icon = {"info": "✅", "warn": "⚠️", "error": "❌"}.get(level, "•")
    text = f"{icon} {title}"
    if body:
        text += f"\n{body}"
    if url:
        text += f"\n{url}"

    for connector_id, creds in targets:
        try:
            if connector_id == "telegram":
                httpx.post(
                    f"https://api.telegram.org/bot{creds['bot_token']}/sendMessage",
                    json={"chat_id": creds["chat_id"], "text": text,
                          "disable_web_page_preview": False},
                    timeout=TIMEOUT,
                )
            elif connector_id == "discord":
                httpx.post(creds["webhook_url"], json={"content": text[:1900]},
                           timeout=TIMEOUT)
            elif connector_id == "webhook":
                httpx.post(creds["url"], json={"title": title, "body": body,
                                               "url": url, "level": level},
                           timeout=TIMEOUT)
        except Exception:  # noqa: BLE001 — a notice is a courtesy, not a requirement
            continue


# ------------------------------------------------------------- shortcuts

def job_done(job_id: str, title: str, duration: float, qa_score: int, passed: bool) -> None:
    send(
        f"Short pronto: {title}",
        f"{duration:.0f}s · QA {qa_score}/100 · {'aprovado' if passed else 'reprovado'}",
        _job_url(job_id),
        "info" if passed else "warn",
    )


def job_failed(job_id: str, title: str, error: str) -> None:
    send(f"Short falhou: {title or job_id}", error[:300], _job_url(job_id), "error")


def published(job_id: str, platform: str, url: str) -> None:
    job = db.get_job(job_id)
    title = (job or {}).get("title") or job_id
    send(f"Publicado no {platform}: {title}", "", url or _job_url(job_id))


def publish_failed(job_id: str, platform: str, error: str) -> None:
    job = db.get_job(job_id)
    title = (job or {}).get("title") or job_id
    send(f"Falha ao publicar no {platform}: {title}", error[:300],
         _job_url(job_id), "error")


def _job_url(job_id: str) -> str:
    return f"http://localhost:3000/job/{job_id}"


# ------------------------------------------------------------- checks

def check_telegram(creds: dict) -> str:
    """Telegram answers 404 (not 401) to an invalid token — left unhandled, the
    user saw a raw HTTPStatusError with the token inside the URL."""
    r = httpx.get(f"https://api.telegram.org/bot{creds.get('bot_token', '')}/getMe",
                  timeout=TIMEOUT)
    if r.status_code in (401, 404):
        raise RuntimeError("Bot token rejected by Telegram. Check the value "
                           "@BotFather gave you.")
    if r.status_code >= 400:
        raise RuntimeError(f"Telegram answered {r.status_code}")
    name = (r.json().get("result") or {}).get("username", "?")
    if not str(creds.get("chat_id", "")).strip():
        raise RuntimeError(f"Bot @{name} is valid, but the chat_id is missing "
                           "(@userinfobot)")
    return f"Bot @{name} is valid — send it /start before the first notice"


def check_discord(creds: dict) -> str:
    url = creds.get("webhook_url", "")
    if not url.startswith("https://"):
        raise RuntimeError("Invalid webhook URL")
    r = httpx.get(url, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError("Discord webhook rejected — the URL may have been "
                           "revoked in the channel")
    return f"Webhook for channel #{r.json().get('name', '?')} is responding"


def check_webhook(creds: dict) -> str:
    url = creds.get("url", "")
    if not url.startswith("http"):
        raise RuntimeError("Invalid URL")
    try:
        r = httpx.post(url, json={"title": "ShortsCreator", "body": "webhook test",
                                  "level": "info"}, timeout=TIMEOUT)
    except httpx.RequestError as exc:
        raise RuntimeError(f"Could not reach the URL: {type(exc).__name__}")
    if r.status_code >= 400:
        raise RuntimeError(f"The URL answered {r.status_code}")
    return f"Webhook answered {r.status_code}"
