"""End-of-pipeline and publication notices — Telegram, Discord or webhook.

All optional: with no credential configured, `send` becomes a no-op. Each
destination is a connector from the Accounts screen (category "notificacao"),
with a fallback in .env. Network errors here must never bring down the job
that has already finished.

The notice texts themselves stay in Portuguese: they are messages to the end
user, not developer-facing strings.
"""
from __future__ import annotations

import json
import threading

import httpx

from .. import db
from ..config import settings
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


# ---------------------------------------------------------------- messages
#
# These reach a person on their phone, so they follow the language the job was
# made in — someone producing in Spanish should not get a Portuguese alert.
# The interface language lives in the browser, out of reach here, but
# `job.language` travels with the job and is the same choice.
MESSAGES = {
    "pt": {
        "done": "Short pronto: {title}",
        "done_body": "{duration}s · QA {score}/100 · {verdict}",
        "approved": "aprovado",
        "rejected": "reprovado",
        "failed": "Short falhou: {title}",
        "published": "Publicado no {platform}: {title}",
        "publish_failed": "Falha ao publicar no {platform}: {title}",
    },
    "en": {
        "done": "Short ready: {title}",
        "done_body": "{duration}s · QA {score}/100 · {verdict}",
        "approved": "passed",
        "rejected": "failed",
        "failed": "Short failed: {title}",
        "published": "Published to {platform}: {title}",
        "publish_failed": "Could not publish to {platform}: {title}",
    },
    "es": {
        "done": "Short listo: {title}",
        "done_body": "{duration}s · QA {score}/100 · {verdict}",
        "approved": "aprobado",
        "rejected": "rechazado",
        "failed": "El short falló: {title}",
        "published": "Publicado en {platform}: {title}",
        "publish_failed": "Fallo al publicar en {platform}: {title}",
    },
    "ru": {
        "done": "Short готов: {title}",
        "done_body": "{duration}с · QA {score}/100 · {verdict}",
        "approved": "пройдено",
        "rejected": "не пройдено",
        "failed": "Short не собрался: {title}",
        "published": "Опубликовано в {platform}: {title}",
        "publish_failed": "Не удалось опубликовать в {platform}: {title}",
    },
    "zh": {
        "done": "短视频已完成：{title}",
        "done_body": "{duration}秒 · 质检 {score}/100 · {verdict}",
        "approved": "通过",
        "rejected": "未通过",
        "failed": "短视频失败：{title}",
        "published": "已发布到 {platform}：{title}",
        "publish_failed": "发布到 {platform} 失败：{title}",
    },
}


def _strings(job_id: str) -> dict:
    """Message set for the language this job was made in."""
    job = db.get_job(job_id)
    tag = "pt"
    if job:
        try:
            tag = (json.loads(job["input_json"]).get("language") or "pt")
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return MESSAGES.get(tag.split("-")[0].lower(), MESSAGES["pt"])


# ------------------------------------------------------------- shortcuts

def job_done(job_id: str, title: str, duration: float, qa_score: int, passed: bool) -> None:
    s = _strings(job_id)
    send(
        s["done"].format(title=title),
        s["done_body"].format(duration=f"{duration:.0f}", score=qa_score,
                              verdict=s["approved"] if passed else s["rejected"]),
        _job_url(job_id),
        "info" if passed else "warn",
    )


def job_failed(job_id: str, title: str, error: str) -> None:
    s = _strings(job_id)
    send(s["failed"].format(title=title or job_id), error[:300],
         _job_url(job_id), "error")


def published(job_id: str, platform: str, url: str) -> None:
    job = db.get_job(job_id)
    title = (job or {}).get("title") or job_id
    s = _strings(job_id)
    send(s["published"].format(platform=platform, title=title), "",
         url or _job_url(job_id))


def publish_failed(job_id: str, platform: str, error: str) -> None:
    job = db.get_job(job_id)
    title = (job or {}).get("title") or job_id
    s = _strings(job_id)
    send(s["publish_failed"].format(platform=platform, title=title),
         error[:300], _job_url(job_id), "error")


def _job_url(job_id: str) -> str:
    """Link back to the job. The interface is not always on localhost — when
    PUBLIC_API_URL points at a tunnel, the notice should be clickable from a
    phone too."""
    base = settings.public_web_url.rstrip("/")
    return f"{base}/job/{job_id}"


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
