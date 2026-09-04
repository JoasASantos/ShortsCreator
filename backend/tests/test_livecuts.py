"""Livestream cuts: windowing, dead air, cross-window dedup and the routes.

No network and no big file here: the LLM call (`clipper.pick_clips`) and the
ffmpeg silence probe are both monkeypatched, and the transcript is a list of
fake whisper segments.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import db, worker
from app.main import app
from app.pipeline import clipper, livecuts

client = TestClient(app)

SIX_HOURS = 6 * 3600.0


def segments(total: float, every: float = 30.0, text: str = "fala do stream") -> list[dict]:
    """A transcript with one segment every `every` seconds, end to end."""
    out: list[dict] = []
    start = 0.0
    while start < total:
        out.append({"start": start, "end": min(start + every, total), "text": text})
        start += every
    return out


def stamps_of(transcript: str) -> list[float]:
    """The absolute seconds behind the [MM:SS] marks of a transcript slice."""
    return [int(m) * 60 + int(s)
            for m, s in re.findall(r"\[(\d+):(\d+)\]", transcript)]


def fake_picker(monkeypatch, clip_for=None, fails_on=()):
    """Stands in for the LLM: returns one clip per call, at the start of the
    slice it was handed, and records what each call actually received."""
    calls: list[dict] = []

    def fake(transcript, total_duration, count, target_seconds, log=lambda m: None):
        seen = stamps_of(transcript)
        index = len(calls) + 1
        calls.append({"count": count, "total": total_duration,
                      "first": seen[0], "last": seen[-1], "transcript": transcript})
        if index in fails_on:
            raise RuntimeError("the model answered garbage")
        if clip_for is not None:
            return clip_for(index, seen)
        start = float(seen[0])
        return [{"inicio": start, "fim": start + target_seconds,
                 "titulo": f"corte {index}", "motivo": "", "assunto": ""}]

    monkeypatch.setattr(clipper, "pick_clips", fake)
    return calls


# ------------------------------- windowing -------------------------------

def test_windows_cover_the_whole_live():
    """The bug being fixed: a single prompt only ever reaches the first hour of
    a 6h live. The windows have to leave no stretch unlooked-at."""
    windows = livecuts.plan_windows(SIX_HOURS, 18)

    assert windows[0].start == 0.0
    assert windows[-1].end == pytest.approx(SIX_HOURS)
    for previous, current in zip(windows, windows[1:]):
        assert current.start == pytest.approx(previous.end)  # no gaps
    assert sum(w.count for w in windows) == 18
    assert len(windows) == 9  # 6h in ~40min windows


def test_every_window_gets_a_share_proportional_to_its_length():
    windows = livecuts.plan_windows(SIX_HOURS, 18)
    assert [w.count for w in windows] == [2] * 9


def test_windows_never_outnumber_the_cuts_asked_for():
    """A window is one LLM call: calling it to pick zero clips is a wasted
    minute, and asking three windows out of nine would hand every cut to the
    first ones — the same 'it all comes from the start' bug."""
    windows = livecuts.plan_windows(SIX_HOURS, 3)

    assert len(windows) == 3
    assert [w.count for w in windows] == [1, 1, 1]
    assert windows[-1].end == pytest.approx(SIX_HOURS)


def test_a_short_live_is_a_single_window(monkeypatch):
    """A 25min stream is not a live problem: one window, one call, the whole
    transcript — exactly the long-video flow."""
    total = 25 * 60.0
    calls = fake_picker(monkeypatch)

    livecuts.collect_clips(segments(total), total, 3, 45)

    assert len(calls) == 1
    assert calls[0]["count"] == 3
    assert calls[0]["first"] == 0
    assert calls[0]["last"] >= total - 60   # nothing was left out


def test_each_window_gets_its_own_slice_of_the_transcript(monkeypatch):
    calls = fake_picker(monkeypatch)

    livecuts.collect_clips(segments(SIX_HOURS), SIX_HOURS, 9, 45)

    assert len(calls) == 9
    # each call starts where the previous one stopped, so the cuts can only
    # come from all over the live
    assert calls[0]["first"] == 0
    assert calls[-1]["last"] >= SIX_HOURS - 60
    for previous, current in zip(calls, calls[1:]):
        assert current["first"] > previous["first"]


def test_window_is_split_when_its_transcript_would_be_truncated():
    """Windows are cut in seconds, the prompt cap is in characters: a fast
    talker overflows a 40min window. Splitting is what stops the truncation
    from coming back one window at a time."""
    dense = segments(livecuts.WINDOW_SECONDS, every=10.0, text="x" * 400)
    windows = livecuts.plan_windows(livecuts.WINDOW_SECONDS, 4, dense)

    assert len(windows) > 1
    for window in windows:
        text = livecuts.window_transcript(dense, window.start, window.end)
        assert len(text) <= clipper.TRANSCRIPT_CHAR_LIMIT


def test_window_transcript_keeps_absolute_timestamps():
    """Window-relative stamps would make every clip land in the first minutes."""
    text = livecuts.window_transcript(segments(SIX_HOURS), 7200.0, 7300.0)
    assert stamps_of(text)[0] >= 7200


# ------------------------------- dead air -------------------------------

def test_detect_silences_reads_the_ffmpeg_report(monkeypatch):
    report = (
        "[silencedetect @ 0x1] silence_start: 12.5\n"
        "[silencedetect @ 0x1] silence_end: 20.0 | silence_duration: 7.5\n"
        "[silencedetect @ 0x1] silence_start: 100.0\n"
        "[silencedetect @ 0x1] silence_end: 102.0 | silence_duration: 2.0\n"
        "[silencedetect @ 0x1] silence_start: 300.0\n"
    )
    monkeypatch.setattr(livecuts.subprocess, "run",
                        lambda *a, **k: type("P", (), {"stderr": report})())

    blocks = livecuts.detect_silences("live.mp4", total_duration=360.0)

    # the 2s block is a pause between sentences, not dead air; the last one has
    # no silence_end because the live ends in silence
    assert blocks == [(12.5, 20.0), (300.0, 360.0)]


def test_detect_silences_without_ffmpeg_just_does_not_filter(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("ffmpeg not found")

    monkeypatch.setattr(livecuts.subprocess, "run", boom)
    assert livecuts.detect_silences("live.mp4") == []


def test_dead_air_ratio_measures_only_the_overlap():
    silences = [(0.0, 10.0), (100.0, 130.0)]
    assert livecuts.dead_air_ratio(0.0, 40.0, silences) == pytest.approx(0.25)
    assert livecuts.dead_air_ratio(100.0, 130.0, silences) == pytest.approx(1.0)
    assert livecuts.dead_air_ratio(50.0, 90.0, silences) == 0.0


def test_a_stretch_that_is_mostly_silence_is_discarded(monkeypatch):
    """"Let me get some water" should not compete with a real moment."""
    total = 2 * livecuts.WINDOW_SECONDS
    fake_picker(monkeypatch)
    silences = [(livecuts.WINDOW_SECONDS, total)]   # the whole second window

    clips = livecuts.collect_clips(segments(total), total, 2, 45,
                                   silences=silences)

    assert len(clips) == 1
    assert clips[0]["inicio"] < livecuts.WINDOW_SECONDS


def test_a_stretch_with_some_silence_is_kept(monkeypatch):
    """The threshold is a majority on purpose — a pause inside a good moment is
    not dead air."""
    total = 2 * livecuts.WINDOW_SECONDS
    fake_picker(monkeypatch)
    # 15s of the second window's 45s clip, which starts at WINDOW_SECONDS
    silences = [(livecuts.WINDOW_SECONDS, livecuts.WINDOW_SECONDS + 15.0)]

    clips = livecuts.collect_clips(segments(total), total, 2, 45,
                                   silences=silences)

    assert len(clips) == 2


# ------------------------------- dedup -------------------------------

def test_the_same_moment_picked_by_two_windows_is_kept_once(monkeypatch):
    """`pick_clips` only rules out overlaps inside one call; neighbouring
    windows share a boundary and can both pick the moment that straddles it."""
    total = 2 * livecuts.WINDOW_SECONDS
    edge = livecuts.WINDOW_SECONDS

    def clip_for(index, seen):
        start = edge - 20.0 if index == 1 else edge
        return [{"inicio": start, "fim": start + 45.0,
                 "titulo": f"corte {index}", "motivo": "", "assunto": ""}]

    fake_picker(monkeypatch, clip_for=clip_for)
    clips = livecuts.collect_clips(segments(total), total, 2, 45)

    assert len(clips) == 1
    assert clips[0]["titulo"] == "corte 1"      # the first one wins


def test_cuts_that_do_not_touch_each_other_all_survive(monkeypatch):
    total = 2 * livecuts.WINDOW_SECONDS
    fake_picker(monkeypatch)

    clips = livecuts.collect_clips(segments(total), total, 2, 45)

    assert len(clips) == 2
    assert clips[0]["inicio"] < clips[1]["inicio"]   # ordered in time


def test_a_timestamp_outside_the_window_is_dropped(monkeypatch):
    """The window's transcript only holds the window's lines, so a start
    outside it is a moment the model made up."""
    total = 2 * livecuts.WINDOW_SECONDS

    def clip_for(index, seen):
        return [{"inicio": 10.0 if index == 2 else float(seen[0]),
                 "fim": (10.0 if index == 2 else float(seen[0])) + 45.0,
                 "titulo": f"corte {index}", "motivo": "", "assunto": ""}]

    fake_picker(monkeypatch, clip_for=clip_for)
    clips = livecuts.collect_clips(segments(total), total, 2, 45)

    assert [c["titulo"] for c in clips] == ["corte 1"]


# ------------------------------- resilience -------------------------------

def test_one_failed_window_does_not_lose_the_others(monkeypatch):
    total = 3 * livecuts.WINDOW_SECONDS
    fake_picker(monkeypatch, fails_on=(2,))

    clips = livecuts.collect_clips(segments(total), total, 3, 45)

    assert [c["titulo"] for c in clips] == ["corte 1", "corte 3"]


def test_every_window_failing_is_reported(monkeypatch):
    total = 2 * livecuts.WINDOW_SECONDS
    fake_picker(monkeypatch, fails_on=(1, 2))

    with pytest.raises(RuntimeError, match="windows"):
        livecuts.collect_clips(segments(total), total, 2, 45)


def test_a_window_where_nobody_speaks_is_not_sent_to_the_llm(monkeypatch):
    """A live with 40min of dead air at the end has no transcript there — no
    reason to pay for a prompt about it."""
    total = 2 * livecuts.WINDOW_SECONDS
    calls = fake_picker(monkeypatch)
    speech = segments(livecuts.WINDOW_SECONDS)     # only the first window talks

    clips = livecuts.collect_clips(speech, total, 2, 45)

    assert len(calls) == 1
    assert len(clips) == 1


# ------------------------------- analysis -------------------------------

def test_analyze_plan_uses_the_attachment_and_stores_the_cuts(monkeypatch):
    plan_id = db.create_clip_plan("upl_live", 4, 45, {"mode": livecuts.MODE})
    total = 2 * livecuts.WINDOW_SECONDS

    monkeypatch.setattr(livecuts.uploads_router, "resolve", lambda i: f"/tmp/{i}.mp4")
    monkeypatch.setattr(livecuts.highlights, "probe_duration", lambda p: total)
    monkeypatch.setattr(livecuts.ingest, "whisper_segments",
                        lambda p, log=None: segments(total))
    monkeypatch.setattr(livecuts, "detect_silences", lambda *a, **k: [])
    fake_picker(monkeypatch)

    livecuts.analyze_plan(plan_id)

    row = db.get_clip_plan(plan_id)
    assert row["status"] == "ready"
    assert len(json.loads(row["clips_json"])) == 2


def test_analyze_plan_downloads_a_url_and_registers_it_as_an_upload(monkeypatch, tmp_path):
    """Downstream (spawn_jobs, the render route) only knows attachment_id, so a
    link has to become a normal upload before anything else runs."""
    plan_id = db.create_clip_plan("", 2, 45,
                                  {"mode": livecuts.MODE,
                                   "url": "https://twitch.tv/videos/1"})
    total = livecuts.WINDOW_SECONDS
    downloaded = tmp_path / "source.mp4"
    downloaded.write_bytes(b"fake live")

    monkeypatch.setattr(livecuts.ingest, "download_video",
                        lambda url, out_dir: (downloaded, {}))
    monkeypatch.setattr(livecuts.highlights, "probe_duration", lambda p: total)
    monkeypatch.setattr(livecuts.ingest, "whisper_segments",
                        lambda p, log=None: segments(total))
    monkeypatch.setattr(livecuts, "detect_silences", lambda *a, **k: [])
    fake_picker(monkeypatch)

    livecuts.analyze_plan(plan_id)

    row = db.get_clip_plan(plan_id)
    assert row["attachment_id"].startswith("upl_")
    assert row["status"] == "ready"
    assert livecuts.uploads_router.resolve(row["attachment_id"]).exists()


def test_the_clip_queue_routes_a_live_plan_to_the_windowed_analysis(monkeypatch):
    from app.pipeline import clipper_jobs

    seen: list[str] = []
    monkeypatch.setattr(livecuts, "analyze_plan", lambda p: seen.append("live"))
    monkeypatch.setattr(clipper_jobs, "analyze_plan", lambda p: seen.append("clip"))

    live = db.create_clip_plan("upl_a", 2, 45, {"mode": livecuts.MODE})
    plain = db.create_clip_plan("upl_b", 2, 45, {"niche": "generico"})
    worker._analyze_plan(live)      # noqa: SLF001
    worker._analyze_plan(plain)     # noqa: SLF001

    assert seen == ["live", "clip"]


# ------------------------------- routes -------------------------------

@pytest.fixture
def no_queue(monkeypatch):
    """The plan is queued but not analysed: the route is what is under test."""
    queued: list[str] = []
    monkeypatch.setattr(worker, "enqueue_clip_plan", lambda p: queued.append(p))
    return queued


def test_creating_a_live_plan_from_a_url(no_queue):
    r = client.post("/api/livecuts", json={"url": "https://twitch.tv/videos/1",
                                           "count": 20})
    assert r.status_code == 200
    plan_id = r.json()["plan_id"]
    assert no_queue == [plan_id]

    row = db.get_clip_plan(plan_id)
    options = json.loads(row["options_json"])
    assert row["requested"] == 20
    assert row["attachment_id"] == ""          # filled in after the download
    assert options["mode"] == livecuts.MODE
    assert options["url"] == "https://twitch.tv/videos/1"
    assert options["window_seconds"] == livecuts.WINDOW_SECONDS


def test_creating_a_live_plan_from_an_upload(no_queue):
    r = client.post("/api/livecuts", json={"attachment_id": "upl_x",
                                           "window_minutes": 20})
    assert r.status_code == 200
    row = db.get_clip_plan(r.json()["plan_id"])
    assert row["attachment_id"] == "upl_x"
    assert json.loads(row["options_json"])["window_seconds"] == 1200


def test_a_live_plan_needs_exactly_one_source(no_queue):
    assert client.post("/api/livecuts", json={}).status_code == 400
    assert client.post("/api/livecuts", json={
        "attachment_id": "upl_x", "url": "https://twitch.tv/videos/1"}).status_code == 400
    assert client.post("/api/livecuts", json={"url": "twitch.tv/videos/1"}).status_code == 400
    assert no_queue == []


def test_a_live_plan_beyond_the_limits_returns_400(no_queue):
    assert client.post("/api/livecuts", json={"attachment_id": "u", "count": 99}).status_code == 400
    assert client.post("/api/livecuts", json={"attachment_id": "u", "count": 0}).status_code == 400
    assert client.post("/api/livecuts",
                       json={"attachment_id": "u", "window_minutes": 5}).status_code == 400


def test_getting_a_live_plan_serializes_the_json_columns():
    plan_id = db.create_clip_plan("upl_x", 2, 45, {"mode": livecuts.MODE})
    db.update_clip_plan(plan_id, status="ready", clips_json=json.dumps(
        [{"inicio": 10.0, "fim": 55.0, "titulo": "Corte", "motivo": "", "assunto": ""}]))

    body = client.get(f"/api/livecuts/{plan_id}").json()

    assert body["status"] == "ready"
    assert body["options"]["mode"] == livecuts.MODE
    assert body["clips"][0]["titulo"] == "Corte"
    assert "clips_json" not in body


def test_a_plain_clip_plan_is_not_a_live_plan():
    plan_id = db.create_clip_plan("upl_x", 3, 45, {"niche": "generico"})
    assert client.get(f"/api/livecuts/{plan_id}").status_code == 404
    assert client.get("/api/livecuts/plan_inexistente").status_code == 404
