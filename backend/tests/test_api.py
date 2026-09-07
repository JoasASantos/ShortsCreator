"""HTTP routes: input validation, serialization and the publishing rules."""
from __future__ import annotations

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.schemas import JobInput

from conftest import needs_ffmpeg

client = TestClient(app)


def _job(**overrides) -> str:
    payload = JobInput(source_type="tema", source="tema de teste", **overrides)
    return db.create_job(payload.model_dump(), "Short de teste")


def _finish(job_id: str, passed: bool = True, score: int = 92) -> None:
    db.update_job(
        job_id, status="done", stage="qa", progress=1.0,
        result_json=json.dumps({"title": "Short de teste", "description": "",
                                "hashtags": [], "duration": 42.0,
                                "script": {"segments": [
                                    {"kind": "hook", "text": "Ninguém avisou disso"},
                                    {"kind": "cta", "text": "Segue pra mais."}]}}),
        qa_json=json.dumps({"passed": passed, "score": score, "issues": [], "metrics": {}}),
    )


def test_health_exposes_the_model_chain():
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert isinstance(body["llm_chain"], list)
    if body["llm_provider"] == "chain":
        assert body["llm_chain"], "a configured chain should list its links"
        assert {"provider", "model", "ready"} <= body["llm_chain"][0].keys()


def test_creating_job_without_source_returns_400():
    r = client.post("/api/jobs", json={"source_type": "tema", "source": "  "})
    assert r.status_code == 400


def test_creating_image_job_without_attachment_returns_400():
    r = client.post("/api/jobs", json={"source_type": "imagem", "source": ""})
    assert r.status_code == 400
    assert "image" in r.json()["detail"].lower()


def test_listing_jobs_serializes_json_and_metrics():
    job_id = _job()
    _finish(job_id)
    rows = client.get("/api/jobs").json()
    row = next(r for r in rows if r["id"] == job_id)
    assert isinstance(row["input"], dict) and "input_json" not in row
    assert row["result"]["duration"] == 42.0
    assert row["qa"]["passed"] is True
    assert row["metrics"] is None       # nothing published yet


def test_job_detail_brings_llm_calls_and_resume_point():
    job_id = _job()
    db.log_llm_call(job_id, "roteiro", "claude_cli", "claude-fable-5-1", 12.5, True)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert len(body["llm_calls"]) == 1
    assert body["llm_calls"][0]["model"] == "claude-fable-5-1"
    assert body["resumable_from"] is None    # no artifacts on disk


def test_unknown_job_returns_404():
    assert client.get("/api/jobs/job_nao_existe").status_code == 404


def test_file_outside_the_allowlist_returns_404():
    job_id = _job()
    r = client.get(f"/api/jobs/{job_id}/file/../../../etc/passwd")
    assert r.status_code == 404


def test_retry_with_invalid_stage_returns_400():
    job_id = _job()
    _finish(job_id)
    assert client.post(f"/api/jobs/{job_id}/retry?from=inventada").status_code == 400


def test_publishing_unfinished_job_returns_400():
    job_id = _job()
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    r = client.post("/api/publish", json={
        "job_id": job_id, "account_id": account_id, "platform": "youtube"})
    assert r.status_code == 400


def test_publishing_with_a_fatal_qa_issue_is_blocked():
    job_id = _job()
    db.update_job(job_id, status="done",
                  result_json=json.dumps({"title": "x", "hashtags": []}),
                  qa_json=json.dumps({"passed": False, "score": 20, "metrics": {},
                                      "issues": [{"check": "proporcao", "severity": "fatal",
                                                  "message": "não é 9:16", "fix": ""}]}))
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    r = client.post("/api/publish", json={
        "job_id": job_id, "account_id": account_id, "platform": "youtube"})
    assert r.status_code == 400
    assert "fatal" in r.json()["detail"].lower()


def test_publishing_creates_a_pending_schedule():
    job_id = _job()
    _finish(job_id)
    account_id = db.create_account("tiktok", "Perfil", {"access_token": "x"})
    r = client.post("/api/publish", json={
        "job_id": job_id, "account_id": account_id, "platform": "tiktok",
        "title": "Título", "privacy": "private"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert db.get_schedule(body["schedule_id"])["platform"] == "tiktok"


@pytest.mark.parametrize("platform", ["youtube", "tiktok", "instagram", "linkedin"])
def test_publishing_accepts_all_four_platforms(platform):
    job_id = _job()
    _finish(job_id)
    account_id = db.create_account(platform, "Conta", {"access_token": "x"})
    r = client.post("/api/publish", json={
        "job_id": job_id, "account_id": account_id, "platform": platform})
    assert r.status_code == 200, r.text


def test_publishing_to_an_unknown_platform_returns_422():
    job_id = _job()
    _finish(job_id)
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    r = client.post("/api/publish", json={
        "job_id": job_id, "account_id": account_id, "platform": "orkut"})
    assert r.status_code == 422


def test_empty_metrics_have_a_zeroed_summary():
    body = client.get("/api/metrics").json()
    assert body["summary"]["published"] == 0
    assert body["items"] == []


def test_metrics_are_aggregated_per_job():
    job_id = _job()
    _finish(job_id)
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    sched = db.create_schedule(job_id, account_id, "youtube", "2026-01-01T00:00:00+00:00", {})
    db.upsert_metrics(sched, job_id, "youtube", "vid123", "https://y/1",
                      {"views": 12000, "likes": 800, "comments": 40,
                       "avg_view_pct": 61.5})
    body = client.get("/api/metrics").json()
    assert body["summary"]["views"] == 12000
    assert body["summary"]["avg_retention"] == 61.5
    assert body["items"][0]["video_id"] == "vid123"

    row = next(r for r in client.get("/api/jobs").json() if r["id"] == job_id)
    assert row["metrics"]["views"] == 12000


def test_upsert_metrics_updates_instead_of_duplicating():
    job_id = _job()
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    sched = db.create_schedule(job_id, account_id, "youtube", "2026-01-01T00:00:00+00:00", {})
    db.upsert_metrics(sched, job_id, "youtube", "vid", "", {"views": 10})
    db.upsert_metrics(sched, job_id, "youtube", "vid", "", {"views": 999})
    rows = db.metrics_for_job(job_id)
    assert len(rows) == 1 and rows[0]["views"] == 999


def test_clip_plan_beyond_the_limit_returns_400():
    assert client.post("/api/clips", json={"attachment_id": "upl_x", "count": 99}).status_code == 400


def test_rendering_a_plan_that_is_not_ready_returns_400():
    plan_id = db.create_clip_plan("upl_x", 3, 45, {"niche": "tecnologia"})
    r = client.post(f"/api/clips/{plan_id}/render", json={"selected": [0]})
    assert r.status_code == 400


def test_outputs_rejects_names_outside_the_expected_pattern():
    assert client.get("/api/outputs/../../etc/passwd").status_code == 404
    assert client.get("/api/outputs/qualquer.txt").status_code == 404


def test_connectors_list_notifiers_and_publishers():
    body = client.get("/api/connectors").json()
    by_id = {c["id"]: c for c in body}
    assert {"telegram", "discord", "webhook"} <= by_id.keys()
    assert by_id["telegram"]["category"] == "notificacao"
    # Instagram and LinkedIn left "planejado" behind and now really publish
    assert by_id["instagram"]["status"] != "planejado"
    assert by_id["linkedin"]["status"] != "planejado"


def test_trends_responds_with_the_expected_structure(monkeypatch):
    from app.pipeline import trends

    monkeypatch.setattr(trends, "fetch", lambda niche, geo: [
        {"source": "Google Trends", "title": "assunto quente", "snippet": "",
         "url": "https://x", "heat": 90, "heat_label": "50k+ buscas"}])
    body = client.get("/api/trends?niche=tecnologia").json()
    assert body["niche"] == "tecnologia"
    assert body["items"][0]["title"] == "assunto quente"


# ----------------------------------------------------- editing the minimum

def test_changing_the_watermark_does_not_re_run_the_scriptwriter():
    """This used to spend an LLM call and, since the model is not
    deterministic, could hand back a different script than the approved one."""
    from app.routers.jobs import _earliest_stage

    assert _earliest_stage(["watermark"]) == "legendas"


def test_a_new_voice_resumes_from_the_voice_stage():
    from app.routers.jobs import _earliest_stage

    assert _earliest_stage(["voice_id"]) == "voz"


def test_a_change_set_redoes_from_its_earliest_stage():
    from app.routers.jobs import _earliest_stage

    # background is earlier than captions, so both are redone
    assert _earliest_stage(["watermark", "background"]) == "fundo"
    assert _earliest_stage(["voice_id", "music_volume"]) == "voz"


def test_music_only_changes_still_rebuild_captions():
    """Resuming straight at `render` would leave the subtitle file unloaded and
    burn a video with no captions."""
    from app.routers.jobs import FIELD_STAGE, STAGE_ORDER

    assert FIELD_STAGE["music_volume"] == "legendas"
    assert "render" not in STAGE_ORDER


def test_unknown_fields_ask_for_no_resume():
    from app.routers.jobs import _earliest_stage

    assert _earliest_stage(["title"]) is None
    assert _earliest_stage([]) is None


@needs_ffmpeg
def test_editing_the_watermark_applies_it_and_asks_to_resume(tmp_path):
    """End to end through the route: the field lands on the job and the
    response says which stage is being redone."""
    from app.config import settings
    from app.pipeline.script import ShortScript

    job_id = _job()
    _finish(job_id)
    job_dir = settings.job_dir(job_id)
    # Artifacts that make a resume possible — real media, because the resume
    # only trusts a file that decodes. A `b"fake"` stand-in is exactly the
    # half-written artifact a restart leaves behind, and `resumable_stage`
    # exists to refuse it: handing one over here would answer "voz" and the
    # test would be asserting the bug.
    (job_dir / "script.json").write_text(ShortScript(
        title="t", description="", segments=[{"kind": "hook", "text": "x"}],
    ).model_dump_json(), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "libmp3lame", str(job_dir / "narration.mp3")],
                   check=True, capture_output=True)
    (job_dir / "narration.json").write_text(
        json.dumps({"duration": 5.0, "words": []}), encoding="utf-8")
    background = job_dir / "background.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(background)], check=True, capture_output=True)
    (job_dir / "background.json").write_text(
        json.dumps({"path": str(background)}), encoding="utf-8")

    body = client.post(f"/api/jobs/{job_id}/edit",
                       json={"watermark": "@mychannel"}).json()

    assert body["applied"] == ["watermark"]
    assert body["resumed_from"] == "legendas"
    assert client.get(f"/api/jobs/{job_id}").json()["input"]["watermark"] == "@mychannel"
    # the marker the orchestrator consumes on the next run
    assert (job_dir / "resume.json").exists()
