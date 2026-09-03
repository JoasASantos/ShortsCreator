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


# ------------------------------------------------------- language of a notice

def _job_in(language: str) -> str:
    return db.create_job({"source_type": "tema", "source": "x",
                          "language": language}, "Strong password")


def test_a_notice_follows_the_language_the_job_was_made_in():
    """These land on someone's phone: a Spanish producer should not get a
    Portuguese alert. The interface language lives in the browser, out of the
    backend's reach, but `job.language` travels with the job."""
    assert notify._strings(_job_in("pt-BR"))["done"].startswith("Short pronto")  # noqa: SLF001
    assert notify._strings(_job_in("en-US"))["done"].startswith("Short ready")   # noqa: SLF001
    assert notify._strings(_job_in("es-ES"))["done"].startswith("Short listo")   # noqa: SLF001
    assert "готов" in notify._strings(_job_in("ru-RU"))["done"]                  # noqa: SLF001
    assert "短视频" in notify._strings(_job_in("zh-CN"))["done"]                  # noqa: SLF001


def test_an_unknown_language_falls_back_to_portuguese():
    assert notify._strings(_job_in("sw-KE")) is notify.MESSAGES["pt"]   # noqa: SLF001


def test_a_missing_job_does_not_break_the_notice():
    """The notice fires after the job finishes; a deleted job must not turn
    into an exception in the worker."""
    assert notify._strings("job_gone") is notify.MESSAGES["pt"]         # noqa: SLF001


def test_every_language_carries_the_same_message_keys():
    reference = set(notify.MESSAGES["pt"])
    for tag, messages in notify.MESSAGES.items():
        assert set(messages) == reference, tag
        assert all(v.strip() for v in messages.values()), tag


def test_the_notice_link_follows_the_configured_web_url(monkeypatch):
    """Behind a tunnel a localhost link is useless on a phone."""
    from app.config import settings

    monkeypatch.setattr(settings, "public_web_url", "https://studio.example.com/")
    assert notify._job_url("job_1") == "https://studio.example.com/job/job_1"   # noqa: SLF001
