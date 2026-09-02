"""Avisos de fim de pipeline e de publicação — Telegram, Discord ou webhook.

Tudo opcional: sem credencial configurada, `send` vira no-op. Cada destino é
um conector da tela de Contas (categoria "notificacao"), com fallback no .env.
Erros de rede aqui nunca podem derrubar o job que já terminou.
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
    """Dispara em background; a thread do worker segue sem esperar rede."""
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
        except Exception:  # noqa: BLE001 — aviso é cortesia, não requisito
            continue


# ------------------------------------------------------------- atalhos

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


# ------------------------------------------------------------- checagens

def check_telegram(creds: dict) -> str:
    """O Telegram responde 404 (não 401) a token inválido — sem tratar, o
    usuário via um HTTPStatusError cru com o token dentro da URL."""
    r = httpx.get(f"https://api.telegram.org/bot{creds.get('bot_token', '')}/getMe",
                  timeout=TIMEOUT)
    if r.status_code in (401, 404):
        raise RuntimeError("Token do bot recusado pelo Telegram. Confira o valor "
                           "que o @BotFather devolveu.")
    if r.status_code >= 400:
        raise RuntimeError(f"Telegram respondeu {r.status_code}")
    name = (r.json().get("result") or {}).get("username", "?")
    if not str(creds.get("chat_id", "")).strip():
        raise RuntimeError(f"Bot @{name} válido, mas falta o chat_id (@userinfobot)")
    return f"Bot @{name} válido — mande /start para ele antes do primeiro aviso"


def check_discord(creds: dict) -> str:
    url = creds.get("webhook_url", "")
    if not url.startswith("https://"):
        raise RuntimeError("URL do webhook inválida")
    r = httpx.get(url, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError("Webhook do Discord recusado — a URL pode ter sido "
                           "revogada no canal")
    return f"Webhook do canal #{r.json().get('name', '?')} respondendo"


def check_webhook(creds: dict) -> str:
    url = creds.get("url", "")
    if not url.startswith("http"):
        raise RuntimeError("URL inválida")
    try:
        r = httpx.post(url, json={"title": "ShortsCreator", "body": "teste de webhook",
                                  "level": "info"}, timeout=TIMEOUT)
    except httpx.RequestError as exc:
        raise RuntimeError(f"Não foi possível alcançar a URL: {type(exc).__name__}")
    if r.status_code >= 400:
        raise RuntimeError(f"A URL respondeu {r.status_code}")
    return f"Webhook respondeu {r.status_code}"
