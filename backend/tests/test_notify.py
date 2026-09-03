"""Avisos: nunca derrubam o job, e erro de credencial vira mensagem legível."""
from __future__ import annotations

import httpx
import pytest

from app import db
from app.pipeline import connectors, notify


def test_sem_credencial_nao_envia_nada():
    assert notify.is_configured() is False
    # não pode levantar: o job já terminou quando isso roda
    notify.send("título", "corpo", "http://x")
    notify.job_done("job_x", "Short", 30.0, 100, True)
    notify.job_failed("job_x", "Short", "erro qualquer")


def test_com_telegram_configurado_fica_ativo():
    db.save_connector("telegram", {"bot_token": "123:abc", "chat_id": "999"})
    assert notify.is_configured() is True
    assert connectors.is_configured("telegram") is True


def test_entrega_falhando_nao_propaga(monkeypatch):
    """Rede caindo no meio de um aviso não pode virar exceção no worker."""
    def explode(*a, **k):
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(notify.httpx, "post", explode)
    notify._deliver([("telegram", {"bot_token": "x", "chat_id": "y"})],  # noqa: SLF001
                    "t", "b", "u", "info")


def test_check_telegram_token_invalido_da_mensagem_amigavel(monkeypatch):
    """O Telegram devolve 404 para token inválido; sem tratar, o usuário via um
    HTTPStatusError cru com o próprio token dentro da URL."""
    monkeypatch.setattr(notify.httpx, "get",
                        lambda *a, **k: httpx.Response(404, json={"ok": False}))
    with pytest.raises(RuntimeError, match="recusado pelo Telegram"):
        notify.check_telegram({"bot_token": "invalido", "chat_id": "1"})


def test_check_telegram_sem_chat_id_avisa(monkeypatch):
    monkeypatch.setattr(notify.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"ok": True, "result": {"username": "meu_bot"}}))
    with pytest.raises(RuntimeError, match="chat_id"):
        notify.check_telegram({"bot_token": "ok", "chat_id": ""})


def test_check_telegram_valido(monkeypatch):
    monkeypatch.setattr(notify.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"ok": True, "result": {"username": "meu_bot"}}))
    assert "meu_bot" in notify.check_telegram({"bot_token": "ok", "chat_id": "123"})


def test_check_discord_recusa_url_nao_https():
    with pytest.raises(RuntimeError, match="inválida"):
        notify.check_discord({"webhook_url": "ftp://x"})


def test_check_webhook_url_inalcancavel(monkeypatch):
    def explode(*a, **k):
        raise httpx.ConnectTimeout("tempo esgotado")

    monkeypatch.setattr(notify.httpx, "post", explode)
    with pytest.raises(RuntimeError, match="alcançar"):
        notify.check_webhook({"url": "http://localhost:1"})


def test_mensagem_do_job_traz_score_e_link():
    enviados: list[tuple] = []
    original = notify._deliver  # noqa: SLF001
    try:
        notify._deliver = lambda t, ti, b, u, l: enviados.append((ti, b, u, l))  # noqa: SLF001
        db.save_connector("telegram", {"bot_token": "1", "chat_id": "2"})
        notify._deliver(notify._targets(), *("Short pronto: X", "30s · QA 92/100",  # noqa: SLF001
                                             "http://localhost:3000/job/1", "info"))
    finally:
        notify._deliver = original  # noqa: SLF001
    assert enviados and "QA 92/100" in enviados[0][1]
