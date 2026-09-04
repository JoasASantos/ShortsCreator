"""Editing a reel you recorded yourself.

The two things worth guarding here are the ones a user notices immediately: a
suggestion the editor cannot place (a timestamp past the end of the video), and
a timeline that lost the person's own audio.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.pipeline import reels
from app.pipeline.timeline import Timeline, VideoClip


def _reel(duration: float = 30.0, words=None) -> reels.Reel:
    return reels.Reel(
        source=None, duration=duration,  # type: ignore[arg-type]
        words=words if words is not None else [
            {"word": "engenharia", "start": 0.0, "end": 0.8},
            {"word": "reversa", "start": 0.8, "end": 1.4},
            {"word": "de", "start": 1.4, "end": 1.5},
            {"word": "binários", "start": 1.5, "end": 2.3},
        ])


# ---------------------------------------------------------- the transcript

def test_the_transcript_carries_timestamps_the_model_can_anchor_to():
    """Without visible timings the model returns advice ("start stronger")
    instead of edits, and advice cannot be applied to anything."""
    words = [{"word": f"w{i}", "start": i * 1.0, "end": i * 1.0 + 0.9}
             for i in range(10)]
    text = reels._timed_transcript(words, every=3.0)  # noqa: SLF001

    assert "[00:00.0]" in text
    assert "[00:03.0]" in text
    # every word survives — a dropped word is a stretch the model cannot cut
    for word in words:
        assert word["word"] in text


def test_the_transcript_marks_minutes_past_sixty_seconds():
    words = [{"word": "tarde", "start": 75.4, "end": 76.0}]
    assert "[01:15.4]" in reels._timed_transcript(words)  # noqa: SLF001


# ---------------------------------------------------------- the suggestions

def _raw(**over) -> dict:
    base = {
        "virality": {"score": 71, "why": "abre bem", "biggest_risk": "arrasta no meio"},
        "hooks": [{"text": "Isso quebra binário", "why": "promete conflito"}],
        "cuts": [{"start": 5.0, "end": 9.0, "why": "repetição"}],
        "media": [{"start": 2.0, "end": 6.0, "what": "diagrama de pilha",
                   "image_prompt": "stack diagram", "stock_query": "code screen"}],
        "captions": [{"start": 1.0, "text": "binários", "why": "fala embolada"}],
        "title": "Engenharia reversa em 30s",
        "hashtags": ["reverseengineering", "#binário"],
    }
    base.update(over)
    return base


def test_a_suggestion_past_the_end_of_the_video_is_dropped():
    """A model asked for timestamps will occasionally return one past the end.
    The user clicks it and nothing happens, which is worse than no suggestion."""
    cleaned = reels._clean_suggestions(  # noqa: SLF001
        _raw(cuts=[{"start": 5.0, "end": 9.0, "why": "ok"},
                   {"start": 400.0, "end": 420.0, "why": "não existe"}]),
        duration=30.0)

    assert len(cleaned["cuts"]) == 1
    assert cleaned["cuts"][0]["why"] == "ok"


def test_a_span_running_past_the_end_is_clipped_not_dropped():
    cleaned = reels._clean_suggestions(  # noqa: SLF001
        _raw(cuts=[{"start": 25.0, "end": 45.0, "why": "final arrastado"}]),
        duration=30.0)
    assert cleaned["cuts"][0]["end"] == 30.0


def test_a_zero_length_span_is_dropped():
    cleaned = reels._clean_suggestions(  # noqa: SLF001
        _raw(cuts=[{"start": 4.0, "end": 4.0, "why": "nada"}]), duration=30.0)
    assert cleaned["cuts"] == []


def test_a_caption_rewrite_needs_only_a_start():
    """A caption is a point in time, not a span — requiring an end would throw
    away every usable rewrite."""
    cleaned = reels._clean_suggestions(  # noqa: SLF001
        _raw(captions=[{"start": 1.0, "text": "binários", "why": "x"}]),
        duration=30.0)
    assert len(cleaned["captions"]) == 1


def test_the_score_is_clamped_and_a_junk_score_does_not_crash():
    assert reels._clean_suggestions(_raw(  # noqa: SLF001
        virality={"score": 250, "why": "", "biggest_risk": ""}),
        30.0)["virality"]["score"] == 100
    assert reels._clean_suggestions(_raw(  # noqa: SLF001
        virality={"score": "muito alto", "why": "", "biggest_risk": ""}),
        30.0)["virality"]["score"] == 0


def test_hashtags_come_back_with_their_hash():
    cleaned = reels._clean_suggestions(_raw(), duration=30.0)  # noqa: SLF001
    assert cleaned["hashtags"] == ["#reverseengineering", "#binário"]


def test_missing_keys_in_the_llm_answer_do_not_crash_the_editor():
    """The chain falls back across models; one of them may answer thinly."""
    cleaned = reels._clean_suggestions({}, duration=30.0)  # noqa: SLF001
    assert cleaned["virality"]["score"] == 0
    assert cleaned["cuts"] == cleaned["media"] == cleaned["hooks"] == []
    assert cleaned["hashtags"] == []


def test_no_transcription_means_no_suggestion_rather_than_an_invented_one():
    with pytest.raises(RuntimeError, match="no transcription"):
        reels.suggest(_reel(words=[]))


# ------------------------------------------------------------- the timeline

def test_the_starting_timeline_keeps_the_persons_own_audio(tmp_path):
    """The whole point of this mode is that the audio is theirs. A timeline
    built without it would render a silent reel."""
    edl = reels.build_timeline(tmp_path, _reel(duration=12.0),
                               caption_style="karaoke", caption_position="centro")

    assert [c.source for c in edl.video] == ["reel_919.mp4"]
    assert [a.source for a in edl.audio] == ["reel_audio.mp3"]
    assert edl.audio[0].gain == 1.0
    assert edl.captions, "the transcribed words should already be captions"
    assert edl.duration == 12.0


def test_the_no_voice_timeline_drops_the_audio_track(tmp_path):
    """Asked for silence, it has to actually be silent — not the original audio
    at a low gain."""
    edl = reels.build_timeline(tmp_path, _reel(), caption_style="bloco",
                               caption_position="baixo", keep_audio=False)
    assert edl.audio == []
    assert edl.captions, "captions stay: the words were still said"


def test_the_watermark_settings_reach_the_timeline(tmp_path):
    edl = reels.build_timeline(
        tmp_path, _reel(), caption_style="karaoke", caption_position="centro",
        watermark="@joas", watermark_position="topo_direita",
        watermark_size="grande", watermark_opacity=0.9)

    assert edl.watermark == "@joas"
    assert edl.watermark_position == "topo_direita"
    assert edl.watermark_size == "grande"
    assert edl.watermark_opacity == 0.9


# ----------------------------------------------------------------- the source

def test_the_recording_is_not_transcribed_twice(tmp_path, monkeypatch):
    """`ingest.ingest` builds the LLM's reading material, and for a video with
    no subtitles it does that by whispering the whole file into one flat
    string. Nothing here reads that string — we transcribe again right after,
    with word timings. Going through it would burn minutes of CPU on a value
    nobody looks at."""
    from app.pipeline import ingest
    from app.schemas import JobInput

    def fail(*a, **k):
        raise AssertionError("reels must not go through ingest.ingest")

    monkeypatch.setattr(ingest, "ingest", fail)
    downloaded = tmp_path / "source.mp4"
    downloaded.write_bytes(b"fake")
    monkeypatch.setattr(ingest, "download_video",
                        lambda url, d: (downloaded, {"title": "Minha live"}))

    job = JobInput(source_type="video", source="https://youtu.be/x",
                   edit_mode=reels.MODE)
    source, title = reels.fetch_source(job, tmp_path, lambda m, level="info": None)

    assert source == downloaded
    assert title == "Minha live"


def test_an_upload_is_used_where_it_already_sits(tmp_path, monkeypatch):
    """No copy: the file is already on disk and a recording can be large."""
    from app.config import settings
    from app.schemas import JobInput

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    upload = settings.uploads_dir / "up_rec.mp4"
    upload.write_bytes(b"fake")

    job = JobInput(source_type="video", attachments=["up_rec"],
                   edit_mode=reels.MODE)
    source, _ = reels.fetch_source(job, tmp_path, lambda m, level="info": None)
    assert source == upload


def test_a_vanished_upload_says_to_send_the_file_again(tmp_path):
    from app.schemas import JobInput

    job = JobInput(source_type="video", attachments=["up_gone"],
                   edit_mode=reels.MODE)
    with pytest.raises(RuntimeError, match="Send the file"):
        reels.fetch_source(job, tmp_path, lambda m, level="info": None)


def test_a_download_that_produced_nothing_names_yt_dlp(tmp_path, monkeypatch):
    """The usual cause is an outdated yt-dlp, so the message has to point
    there instead of saying the link is bad."""
    from app.pipeline import ingest
    from app.schemas import JobInput

    monkeypatch.setattr(ingest, "download_video", lambda url, d: (None, {}))
    job = JobInput(source_type="video", source="https://youtu.be/x",
                   edit_mode=reels.MODE)
    with pytest.raises(RuntimeError, match="yt-dlp"):
        reels.fetch_source(job, tmp_path, lambda m, level="info": None)


# --------------------------------------------------------------- the routes

@pytest.fixture()
def client():
    # conftest already points DATA_DIR at a throwaway directory for the whole
    # session, so these routes write jobs and uploads there, not into anyone's
    # real data.
    from app.main import app
    return TestClient(app)


def test_a_reel_needs_exactly_one_source(client):
    both = client.post("/api/reels", json={"attachment_id": "up_1",
                                           "url": "https://youtu.be/x"})
    neither = client.post("/api/reels", json={})
    assert both.status_code == 400
    assert neither.status_code == 400
    assert "exactly one" in both.json()["detail"]


def test_a_reel_refuses_something_that_is_not_a_url(client):
    answer = client.post("/api/reels", json={"url": "youtube.com/watch?v=x"})
    assert answer.status_code == 400
    assert "http" in answer.json()["detail"]


def test_a_queued_reel_is_a_job_with_its_own_edit_mode(client, monkeypatch):
    """It rides the normal job queue on purpose: that is what gives it the
    editor, QA, the cover and publishing for free."""
    from app import db, worker

    queued: list[str] = []
    monkeypatch.setattr(worker, "enqueue", queued.append)

    answer = client.post("/api/reels", json={"url": "https://youtu.be/abc",
                                             "title": "meu reel"})
    assert answer.status_code == 200
    job_id = answer.json()["job_id"]
    assert queued == [job_id]

    row = db.get_job(job_id)
    saved = json.loads(row["input_json"])
    assert saved["edit_mode"] == reels.MODE
    assert saved["source"] == "https://youtu.be/abc"
    # a recording brings its own picture and its own voice
    assert saved["background"] == "video_fonte"
    assert saved["music"] is False
    assert saved["keep_audio"] is True


def test_a_silent_reel_can_be_asked_for(client, monkeypatch):
    """Some feeds are watched muted, so captions-only is a real delivery — and
    it has to reach the pipeline, not be quietly ignored."""
    from app import db, worker
    monkeypatch.setattr(worker, "enqueue", lambda _job_id: None)

    answer = client.post("/api/reels", json={"url": "https://youtu.be/abc",
                                             "keep_audio": False})
    saved = json.loads(db.get_job(answer.json()["job_id"])["input_json"])
    assert saved["keep_audio"] is False


def test_assist_on_a_generated_short_says_so_instead_of_guessing(client, monkeypatch):
    from app import db, worker
    monkeypatch.setattr(worker, "enqueue", lambda _job_id: None)

    job_id = db.create_job({"source_type": "tema", "source": "x"}, "short comum")
    answer = client.post(f"/api/reels/{job_id}/assist", json={})
    assert answer.status_code == 400
    assert "not a recording" in answer.json()["detail"]


def test_assist_before_the_transcription_is_ready_is_not_an_error_page(client):
    """409, with an explanation: the job is fine, it is just not there yet."""
    from app import db

    job_id = db.create_job({"source_type": "video", "edit_mode": reels.MODE}, "x")
    answer = client.post(f"/api/reels/{job_id}/assist", json={})
    assert answer.status_code == 409
    assert "not been transcribed" in answer.json()["detail"]


def test_assist_stores_what_it_found_on_the_job(client, monkeypatch):
    """Reopening the editor has to show the last suggestions instead of
    spending another LLM call to show the same thing."""
    from app import db
    from app.config import settings

    job_id = db.create_job(
        {"source_type": "video", "edit_mode": reels.MODE, "niche": "programacao"},
        "reel")
    job_dir = settings.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "reel.json").write_text(json.dumps(
        {"duration": 30.0, "language": "pt", "words": _reel().words}),
        encoding="utf-8")

    monkeypatch.setattr(reels, "suggest",
                        lambda *a, **k: reels._clean_suggestions(_raw(), 30.0))  # noqa: SLF001

    answer = client.post(f"/api/reels/{job_id}/assist",
                         json={"instruction": "mais agressivo"})
    assert answer.status_code == 200
    assert answer.json()["virality"]["score"] == 71

    stored = json.loads(db.get_job(job_id)["result_json"])["assist"]
    assert stored["instruction"] == "mais agressivo"


def test_media_over_the_video_needs_a_real_upload(client):
    from app import db

    job_id = db.create_job({"source_type": "video", "edit_mode": reels.MODE}, "r")
    answer = client.post(f"/api/reels/{job_id}/media", json={
        "attachment_id": "up_missing", "start": 1.0, "end": 3.0})
    assert answer.status_code == 404


def test_media_that_ends_before_it_starts_is_refused(client):
    from app import db

    job_id = db.create_job({"source_type": "video", "edit_mode": reels.MODE}, "r")
    answer = client.post(f"/api/reels/{job_id}/media", json={
        "attachment_id": "up_1", "start": 3.0, "end": 1.0})
    assert answer.status_code == 400
    assert "after it starts" in answer.json()["detail"]


def test_media_is_copied_into_the_job_and_placed_on_the_timeline(client):
    """Copied, not referenced: the render runs with the job directory as its
    working directory, so an overlay pointing outside it breaks as soon as the
    upload is cleaned up."""
    from app import db
    from app.config import settings
    from app.pipeline import timeline as timeline_mod

    job_id = db.create_job({"source_type": "video", "edit_mode": reels.MODE}, "r")
    job_dir = settings.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    timeline_mod.save(job_dir, Timeline(
        duration=20.0,
        video=[VideoClip(id="v1", source="reel_919.mp4", in_point=0,
                         out_point=20, start=0)]))

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    (settings.uploads_dir / "up_pic.png").write_bytes(b"not really a png")

    answer = client.post(f"/api/reels/{job_id}/media", json={
        "attachment_id": "up_pic", "start": 2.0, "end": 6.0,
        "x": 0.5, "y": 0.3, "width": 0.5})
    assert answer.status_code == 200

    body = answer.json()
    assert len(body["media"]) == 1
    placed = body["media"][0]
    assert placed["kind"] == "image"
    assert (job_dir / placed["source"]).exists(), "the file was not copied in"
    # the whole timeline comes back, because normalize() may have clipped it
    assert body["duration"] == 20.0


def test_media_dragged_past_the_end_comes_back_clipped(client):
    from app import db
    from app.config import settings
    from app.pipeline import timeline as timeline_mod

    job_id = db.create_job({"source_type": "video", "edit_mode": reels.MODE}, "r")
    job_dir = settings.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    timeline_mod.save(job_dir, Timeline(
        duration=10.0,
        video=[VideoClip(id="v1", source="reel_919.mp4", in_point=0,
                         out_point=10, start=0)]))
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    (settings.uploads_dir / "up_clip.mp4").write_bytes(b"fake")

    answer = client.post(f"/api/reels/{job_id}/media", json={
        "attachment_id": "up_clip", "start": 8.0, "end": 60.0})
    placed = answer.json()["media"][0]
    assert placed["kind"] == "video"
    assert placed["end"] == 10.0
