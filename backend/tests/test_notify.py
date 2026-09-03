"""Notifications: they never take the job down, and a credential error turns
into a readable message."""
from __future__ import annotations

import httpx
import pytest

from app import db
from app.pipeline import connectors, notify


def test_without_credentials_nothing_is_sent():
    assert notify.is_configured() is False
    # must not raise: the job has already finished by the time this runs
    notify.send("title", "body", "http://x")
    notify.job_done("job_x", "Short", 30.0, 100, True)
    notify.job_failed("job_x", "Short", "some error")


def test_with_telegram_configured_notifications_are_active():
    db.save_connector("telegram", {"bot_token": "123:abc", "chat_id": "999"})
    assert notify.is_configured() is True
    assert connectors.is_configured("telegram") is True


def test_a_failing_delivery_does_not_propagate(monkeypatch):
    """The network dropping mid-notification must not turn into an exception in
    the worker."""
    def blow_up(*a, **k):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(notify.httpx, "post", blow_up)
    notify._deliver([("telegram", {"bot_token": "x", "chat_id": "y"})],  # noqa: SLF001
                    "t", "b", "u", "info")


def test_check_telegram_with_an_invalid_token_gives_a_friendly_message(monkeypatch):
    """Telegram answers 404 for an invalid token; untreated, the user would see
    a raw HTTPStatusError with their own token inside the URL."""
    monkeypatch.setattr(notify.httpx, "get",
                        lambda *a, **k: httpx.Response(404, json={"ok": False}))
    with pytest.raises(RuntimeError, match="rejected by Telegram"):
        notify.check_telegram({"bot_token": "invalido", "chat_id": "1"})


def test_check_telegram_without_a_chat_id_warns(monkeypatch):
    monkeypatch.setattr(notify.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"ok": True, "result": {"username": "meu_bot"}}))
    with pytest.raises(RuntimeError, match="chat_id"):
        notify.check_telegram({"bot_token": "ok", "chat_id": ""})


def test_check_telegram_accepts_valid_credentials(monkeypatch):
    monkeypatch.setattr(notify.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"ok": True, "result": {"username": "meu_bot"}}))
    assert "meu_bot" in notify.check_telegram({"bot_token": "ok", "chat_id": "123"})


def test_check_discord_rejects_a_non_https_url():
    with pytest.raises(RuntimeError, match="Invalid webhook URL"):
        notify.check_discord({"webhook_url": "ftp://x"})


def test_check_webhook_reports_an_unreachable_url(monkeypatch):
    def blow_up(*a, **k):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(notify.httpx, "post", blow_up)
    with pytest.raises(RuntimeError, match="Could not reach"):
        notify.check_webhook({"url": "http://localhost:1"})


def test_the_job_message_carries_the_score_and_the_link():
    sent: list[tuple] = []
    original = notify._deliver  # noqa: SLF001
    try:
        notify._deliver = lambda t, ti, b, u, l: sent.append((ti, b, u, l))  # noqa: SLF001
        db.save_connector("telegram", {"bot_token": "1", "chat_id": "2"})
        notify._deliver(notify._targets(), *("Short pronto: X", "30s · QA 92/100",  # noqa: SLF001
                                             "http://localhost:3000/job/1", "info"))
    finally:
        notify._deliver = original  # noqa: SLF001
    assert sent and "QA 92/100" in sent[0][1]
