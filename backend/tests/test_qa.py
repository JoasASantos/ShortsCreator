"""Format QA — audited against the real file, with ffprobe/ffmpeg."""
from __future__ import annotations

import subprocess

from app.pipeline import captions, qa
from app.schemas import JobInput, QAIssue, QAReport

from conftest import needs_ffmpeg


@needs_ffmpeg
def test_valid_9x16_video_passes(sample_video):
    report = qa.audit(sample_video)
    fatal = [i for i in report.issues if i.severity == "fatal"]
    assert not fatal, f"rejected a valid 1080x1920: {[i.message for i in fatal]}"
    assert report.metrics["width"] == 1080
    assert report.metrics["height"] == 1920
    assert abs(report.metrics["aspect_ratio"] - 9 / 16) < 0.001
    assert report.score > 0


@needs_ffmpeg
def test_16x9_video_fails_with_a_fatal_issue(tmp_path):
    horizontal = tmp_path / "16x9.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-t", "2", str(horizontal)],
        check=True, capture_output=True,
    )
    report = qa.audit(horizontal)
    assert not report.passed
    checks = {i.check for i in report.issues if i.severity == "fatal"}
    assert "proporcao" in checks


@needs_ffmpeg
def test_score_penalizes_proportionally(sample_video, tmp_path):
    """A valid video scores high; a square one (wrong aspect ratio) plummets."""
    square = tmp_path / "square.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=600x600:rate=30:duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-t", "2", str(square)],
        check=True, capture_output=True,
    )
    assert qa.audit(sample_video).score > qa.audit(square).score


def test_a_missing_file_fails_without_blowing_up(tmp_path):
    report = qa.audit(tmp_path / "does_not_exist.mp4")
    assert not report.passed
    assert report.score == 0
    assert report.issues[0].severity == "fatal"


def test_ass_with_too_low_a_margin_raises_an_issue(tmp_path, words):
    """A caption inside the app's UI band: QA has to call it out."""
    ass = tmp_path / "invalid.ass"
    content = captions.build_ass(words, ass, position="baixo").read_text("utf-8")
    # force an invalid margin on the Legenda style (below 60% of the safe area)
    ass.write_text(content.replace(
        f",{captions.POSITION_MARGIN_V['baixo']},1\n", ",60,1\n", 1), encoding="utf-8")

    checks = {i.check for i in qa._ass_safe_area(ass)}  # noqa: SLF001
    assert "safe_area_inferior" in checks


def test_ass_in_the_default_positions_raises_no_issue(tmp_path, words):
    for position in ("baixo", "centro", "topo"):
        ass = captions.build_ass(words, tmp_path / f"{position}.ass", position=position)
        checks = {i.check for i in qa._ass_safe_area(ass)}  # noqa: SLF001
        assert "safe_area_inferior" not in checks, f"position '{position}' failed the audit"
        assert "safe_area_lateral" not in checks
        assert "legenda_vazia" not in checks


def test_a_missing_ass_yields_an_issue_instead_of_blowing_up(tmp_path):
    checks = {i.check for i in qa._ass_safe_area(tmp_path / "does_not_exist.ass")}  # noqa: SLF001
    assert "legenda_arquivo" in checks


def test_an_ass_with_no_dialogue_is_fatal(tmp_path):
    ass = tmp_path / "no_events.ass"
    ass.write_text("[Script Info]\n[Events]\n", encoding="utf-8")
    issues = qa._ass_safe_area(ass)  # noqa: SLF001
    assert any(i.check == "legenda_vazia" and i.severity == "fatal" for i in issues)


def test_union_seconds_merges_overlapping_intervals():
    assert qa._union_seconds([(0, 2), (1, 3)]) == 3.0        # noqa: SLF001
    assert qa._union_seconds([(0, 1), (2, 3)]) == 2.0        # noqa: SLF001
    assert qa._union_seconds([]) == 0.0                      # noqa: SLF001


def _report(*checks: str) -> QAReport:
    return QAReport(passed=False, score=60, metrics={}, issues=[
        QAIssue(check=c, severity="erro", message=c, fix="") for c in checks])


def test_suggest_fix_lengthens_the_script_when_too_short():
    job = JobInput(source_type="tema", source="x", duration=30)
    fix = qa.suggest_fix(_report("duracao_minima"), job)
    assert fix is not None
    action, updated, stage = fix
    assert stage == "script"
    assert updated.duration > job.duration


def test_suggest_fix_shortens_the_script_when_too_long():
    job = JobInput(source_type="tema", source="x", duration=80)
    fix = qa.suggest_fix(_report("duracao_maxima"), job)
    assert fix is not None
    _, updated, stage = fix
    assert stage == "script"
    assert updated.duration < job.duration


def test_suggest_fix_does_not_insist_when_already_at_the_limit():
    """Already at the maximum allowed: stretching further would not fix it —
    better to stop the self-adjust loop and surface the problem."""
    from app.config import settings

    job = JobInput(source_type="tema", source="x", duration=settings.max_short_seconds)
    assert qa.suggest_fix(_report("duracao_minima"), job) is None


def test_suggest_fix_moves_the_caption_to_the_center():
    job = JobInput(source_type="tema", source="x", caption_position="baixo")
    fix = qa.suggest_fix(_report("safe_area_inferior"), job)
    assert fix is not None
    _, updated, stage = fix
    assert updated.caption_position == "centro"
    assert stage == "legendas"


def test_suggest_fix_does_not_reapply_when_the_caption_is_already_centered():
    job = JobInput(source_type="tema", source="x", caption_position="centro")
    assert qa.suggest_fix(_report("safe_area_inferior"), job) is None


def test_suggest_fix_for_loudness_lowers_the_music_track():
    job = JobInput(source_type="tema", source="x", music=True, music_volume=0.12)
    fix = qa.suggest_fix(_report("loudness"), job)
    assert fix is not None
    _, updated, stage = fix
    assert updated.music_volume < 0.12
    assert stage == "render"


def test_suggest_fix_for_black_bars_reframes_before_dropping_the_footage():
    """This used to swap straight to a gradient, which passes the audit by
    throwing away the video the user asked for. Reframing to cover the frame
    cannot leave a bar and keeps the footage; the gradient is what is left
    when even that fails. The escalation is covered in test_framing.py."""
    job = JobInput(source_type="video", source="x", background="video_fonte")
    fix = qa.suggest_fix(_report("barras_pretas"), job)
    assert fix is not None
    _, updated, stage = fix
    assert updated.background_fill == "preencher"
    assert updated.background == "video_fonte"
    assert stage == "fundo"


def test_suggest_fix_ignores_warnings():
    """A warning does not block publishing; it must not trigger a re-render."""
    report = QAReport(passed=True, score=95, metrics={}, issues=[
        QAIssue(check="duracao_minima", severity="aviso", message="", fix="")])
    job = JobInput(source_type="tema", source="x", duration=30)
    assert qa.suggest_fix(report, job) is None


def test_suggest_fix_returns_none_for_an_unknown_issue():
    job = JobInput(source_type="tema", source="x")
    report = QAReport(passed=False, score=50, metrics={}, issues=[
        QAIssue(check="coisa_inedita", severity="erro", message="?", fix="")])
    assert qa.suggest_fix(report, job) is None


def test_parse_fps_handles_fractions_and_garbage():
    assert qa._parse_fps("30/1") == 30.0        # noqa: SLF001
    assert abs(qa._parse_fps("30000/1001") - 29.97) < 0.01   # noqa: SLF001
    assert qa._parse_fps("0/0") == 0.0          # noqa: SLF001
    assert qa._parse_fps("lixo") == 0.0         # noqa: SLF001


# --------------------- where the problem is, not just how much --------------

def _audit_with(monkeypatch, tmp_path, duration: float):
    """Audit a fabricated 16:9 film of `duration`.

    Rendering seven real minutes to test a timestamp would cost more than the
    feature: what these check is the reporting, so the probe is faked and the
    detectors are patched per test. The file still exists and passes the size
    floor, because `audit` refuses to read anything else.
    """
    from app.pipeline import formats

    video = tmp_path / "film.mp4"
    video.write_bytes(b"0" * 40_000)
    monkeypatch.setattr(qa, "_ffprobe", lambda v: {
        "format": {"duration": str(duration), "size": "40000", "bit_rate": "800000"},
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080,
             "codec_name": "h264", "pix_fmt": "yuv420p", "r_frame_rate": "30/1",
             "sample_aspect_ratio": "1:1"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"},
        ],
    })
    monkeypatch.setattr(qa, "_loudness", lambda v: {
        "integrated_lufs": -14.5, "loudness_range": 8.5, "true_peak_dbfs": -4.3})
    monkeypatch.setattr(qa, "_faststart", lambda v: True)
    # `_silence`, `_black_frames` and `_black_bars` stay for the test to set:
    # patching them here would override what it just asked for.
    return qa.audit(video, fmt=formats.HORIZONTAL)


def test_black_screen_is_reported_with_the_timestamp(monkeypatch, tmp_path):
    """"5.1s of black screen" in a seven-minute film sends someone scrubbing.
    "at 6:33" is the shot to fix."""
    monkeypatch.setattr(qa, "_black_frames", lambda video: [(393.0, 398.1)])
    monkeypatch.setattr(qa, "_black_bars", lambda video, duration: None)
    monkeypatch.setattr(qa, "_silence", lambda video: [])

    report = _audit_with(monkeypatch, tmp_path, duration=437.0)
    black = next(i for i in report.issues if i.check == "tela_preta")
    assert "6:33" in black.message
    assert report.metrics["black_at"] == ["6:33-6:38"]


def test_a_stretch_with_nobody_speaking_is_named(monkeypatch, tmp_path):
    """Counting 34 silence blocks and naming none reported a pause between
    sentences exactly like a block whose voice never arrived."""
    monkeypatch.setattr(qa, "_silence", lambda video: [
        (12.0, 12.4), (100.0, 105.5), (200.0, 200.3)])
    monkeypatch.setattr(qa, "_black_frames", lambda video: [])
    monkeypatch.setattr(qa, "_black_bars", lambda video, duration: None)

    report = _audit_with(monkeypatch, tmp_path, duration=437.0)
    hole = next(i for i in report.issues if i.check == "silencio_no_meio")
    assert "1:40-1:45" in hole.message
    assert "5.5s" in hole.message
    # a 0.4s pause between two sentences is not a hole
    assert "0:12" not in hole.message


def test_ordinary_pauses_do_not_raise_a_silence_issue(monkeypatch, tmp_path):
    monkeypatch.setattr(qa, "_silence", lambda video: [(5.0, 5.6), (9.0, 9.8)])
    monkeypatch.setattr(qa, "_black_frames", lambda video: [])
    monkeypatch.setattr(qa, "_black_bars", lambda video, duration: None)

    report = _audit_with(monkeypatch, tmp_path, duration=60.0)
    assert not [i for i in report.issues if i.check == "silencio_no_meio"]
