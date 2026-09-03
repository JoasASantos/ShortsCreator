"""Publishers: an account only exists with a token that answers, and every
error is readable."""
from __future__ import annotations

import json

import httpx
import pytest

from app import db
from app.config import settings
from app.pipeline import connectors
from app.pipeline.publishers import PLATFORM_LABEL, dispatch, instagram, linkedin


def test_every_platform_has_a_label():
    assert set(PLATFORM_LABEL) == {"youtube", "tiktok", "instagram", "linkedin"}


# --------------------------------------- an account only if the token is real

def test_instagram_with_an_invalid_token_does_not_create_a_publishable_account(monkeypatch):
    """This used to become an account with a generic name that only failed at
    publish time."""
    monkeypatch.setattr(instagram.httpx, "get", lambda *a, **k: httpx.Response(
        400, json={"error": {"message": "Invalid OAuth access token", "code": 190}}))

    connectors.save("instagram", {"access_token": "bad", "ig_user_id": "1"})
    assert connectors.is_configured("instagram") is True
    assert [a for a in db.list_accounts() if a["platform"] == "instagram"] == []


def test_instagram_with_a_valid_token_creates_the_account_with_its_handle(monkeypatch):
    monkeypatch.setattr(instagram.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"username": "meucanal"}))

    connectors.save("instagram", {"access_token": "good", "ig_user_id": "1"})
    accounts = [a for a in db.list_accounts() if a["platform"] == "instagram"]
    assert len(accounts) == 1
    assert accounts[0]["display_name"] == "@meucanal"


def test_saving_again_updates_instead_of_duplicating(monkeypatch):
    monkeypatch.setattr(instagram.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"username": "meucanal"}))
    connectors.save("instagram", {"access_token": "good", "ig_user_id": "1"})
    connectors.save("instagram", {"access_token": "another", "ig_user_id": "1"})
    assert len([a for a in db.list_accounts() if a["platform"] == "instagram"]) == 1


def test_clearing_the_connector_removes_the_account(monkeypatch):
    monkeypatch.setattr(instagram.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"username": "x"}))
    connectors.save("instagram", {"access_token": "good", "ig_user_id": "1"})
    connectors.clear("instagram")
    assert [a for a in db.list_accounts() if a["platform"] == "instagram"] == []


def test_linkedin_without_profile_read_access_uses_the_urn(monkeypatch):
    """A token holding only w_member_social cannot read the profile — the URN
    entered by hand identifies the account and is enough to publish."""
    monkeypatch.setattr(linkedin.httpx, "get",
                        lambda *a, **k: httpx.Response(403, json={}))
    name = linkedin.profile_name({"access_token": "write-only",
                                  "author_urn": "urn:li:person:ABC"})
    assert name == "urn:li:person:ABC"


def test_linkedin_without_a_urn_and_without_read_access_fails_explaining_why(monkeypatch):
    monkeypatch.setattr(linkedin.httpx, "get",
                        lambda *a, **k: httpx.Response(401, json={}))
    with pytest.raises(RuntimeError, match="author_urn"):
        linkedin.profile_name({"access_token": "bad", "author_urn": ""})


def test_linkedin_with_openid_uses_the_real_name(monkeypatch):
    monkeypatch.setattr(linkedin.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"name": "Maria Silva", "sub": "abc"}))
    assert linkedin.profile_name({"access_token": "full"}) == "Maria Silva"


# ------------------------------------------------------------- Instagram URL

def test_instagram_requires_a_public_url(monkeypatch):
    """The Graph API downloads the video: localhost will not do, and the error
    has to say so instead of blowing up inside Meta."""
    monkeypatch.setattr(settings, "public_api_url", "http://localhost:8000")
    with pytest.raises(RuntimeError, match="public URL"):
        instagram.public_video_url("job_x")


def test_instagram_accepts_a_tunnel_url(monkeypatch):
    monkeypatch.setattr(settings, "public_api_url", "https://abc.ngrok.app/")
    assert instagram.public_video_url("job_x") == \
        "https://abc.ngrok.app/api/outputs/job_x.mp4"


# ------------------------------------------------------------------ dispatch

def _finished_job() -> str:
    job_id = db.create_job({"source_type": "tema", "source": "x"}, "Short")
    db.update_job(job_id, status="done", result_json=json.dumps({"title": "Short"}))
    (settings.outputs_dir / f"{job_id}.mp4").write_bytes(b"fake")
    return job_id


def test_dispatch_rejects_an_unknown_platform():
    job_id = _finished_job()
    account_id = db.create_account("orkut", "Perfil", {"token": "x"})
    sched = db.create_schedule(job_id, account_id, "orkut", "2026-01-01T00:00:00+00:00", {})
    with pytest.raises(RuntimeError, match="Unsupported platform"):
        dispatch(db.get_schedule(sched))


def test_dispatch_rejects_an_unfinished_job():
    job_id = db.create_job({"source_type": "tema", "source": "x"}, "Short")
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    sched = db.create_schedule(job_id, account_id, "youtube", "2026-01-01T00:00:00+00:00", {})
    with pytest.raises(RuntimeError, match="Job is not finished"):
        dispatch(db.get_schedule(sched))


def test_dispatch_rejects_a_missing_video():
    job_id = db.create_job({"source_type": "tema", "source": "x"}, "Short")
    db.update_job(job_id, status="done", result_json="{}")
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    sched = db.create_schedule(job_id, account_id, "youtube", "2026-01-01T00:00:00+00:00", {})
    with pytest.raises(RuntimeError, match="Video file is missing"):
        dispatch(db.get_schedule(sched))


def test_dispatch_passes_the_cover_and_its_timestamp_to_the_publisher():
    """TikTok uses the very same cover timestamp as its cover frame."""
    job_id = _finished_job()
    job_dir = settings.job_dir(job_id)
    (job_dir / "cover.jpg").write_bytes(b"fake")
    (job_dir / "cover.json").write_text(json.dumps({"at": 4.2}), encoding="utf-8")
    account_id = db.create_account("tiktok", "Perfil", {"access_token": "x"})
    sched = db.create_schedule(job_id, account_id, "tiktok",
                              "2026-01-01T00:00:00+00:00", {"title": "T"})

    received: dict = {}

    from app.pipeline.publishers import tiktok

    def fake_upload(video, payload, credentials, account_id):
        received.update(payload)
        return {"platform": "tiktok", "publish_id": "1"}

    original = tiktok.upload
    try:
        tiktok.upload = fake_upload
        dispatch(db.get_schedule(sched))
    finally:
        tiktok.upload = original

    assert received["cover_at"] == 4.2
    assert received["cover_path"].endswith("cover.jpg")
    assert received["job_id"] == job_id
