"""Fitting whatever YouTube hands over into a 9:16 frame.

A source arrives in any shape: a cinema trailer is 2.39:1 delivered inside a
16:9 file with the bars baked into the picture, an archive clip is 4:3 with
bars down the sides, a phone recording is already vertical. The bars are part
of the image, not of the container, so nothing downstream can tell them from
content — they have to come off first.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.pipeline import qa, render
from app.schemas import JobInput, QAIssue, QAReport

from conftest import needs_ffmpeg


def _clip(path: Path, size: str, pad: str = "", seconds: float = 4.0) -> Path:
    """A test pattern, optionally padded with real black bars."""
    vf = ["-vf", pad] if pad else []
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={size}:rate=30:duration={seconds}",
         *vf, "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True)
    return path


def _usable_area(video: Path) -> tuple[int, int]:
    """What cropdetect considers picture — the same measurement QA makes."""
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video),
         "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"],
        capture_output=True, text=True)
    import re
    matches = re.findall(r"crop=(\d+):(\d+):", probe.stderr)
    if not matches:
        return (0, 0)
    return int(matches[-1][0]), int(matches[-1][1])


# ------------------------------------------------------- detecting the bars

@needs_ffmpeg
def test_a_cinema_trailer_has_its_baked_bars_found(tmp_path):
    """2.39:1 inside 16:9 — the shape every film trailer arrives in."""
    source = _clip(tmp_path / "cine.mp4", "1920x804",
                   pad="pad=1920:1080:0:138:black")
    assert render.content_crop(source) == "crop=1920:804:0:138,"


@needs_ffmpeg
def test_a_four_by_three_clip_has_its_side_bars_found(tmp_path):
    source = _clip(tmp_path / "pillar.mp4", "1440x1080",
                   pad="pad=1920:1080:240:0:black")
    assert render.content_crop(source) == "crop=1440:1080:240:0,"


@needs_ffmpeg
@pytest.mark.parametrize("size", ["1920x1080", "1080x1920", "1080x1080"])
def test_clean_footage_is_left_alone(size, tmp_path):
    """The common case has to cost nothing: no crop, no drift between renders."""
    source = _clip(tmp_path / f"clean_{size}.mp4", size)
    assert render.content_crop(source) == ""


@needs_ffmpeg
def test_an_all_black_source_is_not_cropped_to_nothing(tmp_path):
    """cropdetect loses its footing on a black frame. Acting on that reading
    would crop the video down to a sliver, or to zero."""
    source = tmp_path / "black.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=3",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(source)], check=True, capture_output=True)
    assert render.content_crop(source) == ""


# --------------------------------------------------------- what comes out

@needs_ffmpeg
def test_a_trailers_bars_do_not_reach_the_short(tmp_path):
    """The whole point: a letterboxed source used to produce a letterboxed
    short, which QA then failed and the autofix 'fixed' by throwing the footage
    away."""
    source = _clip(tmp_path / "cine.mp4", "1920x804",
                   pad="pad=1920:1080:0:138:black")
    out = render.background_from_video(source, 3.0, tmp_path / "bg.mp4",
                                       fill="preencher")

    width, height = _usable_area(out)
    assert (width, height) == (1080, 1920), (
        f"the frame should be picture edge to edge, got {width}x{height}")


@needs_ffmpeg
def test_filling_covers_the_frame_where_fitting_cannot(tmp_path):
    """Landscape footage: zooming to cover leaves nothing to fill, so no bar
    is possible whatever the footage looks like."""
    source = _clip(tmp_path / "wide.mp4", "1920x1080")
    out = render.background_from_video(source, 3.0, tmp_path / "fill.mp4",
                                       fill="preencher")
    assert _usable_area(out) == (1080, 1920)


@needs_ffmpeg
def test_the_output_is_always_the_house_format(tmp_path):
    """Whatever went in, what comes out is 1080x1920."""
    for name, size in (("wide", "1920x1080"), ("tall", "1080x1920"),
                       ("square", "1080x1080")):
        source = _clip(tmp_path / f"{name}.mp4", size)
        out = render.background_from_video(source, 2.0, tmp_path / f"{name}_bg.mp4")
        assert render._dimensions(out) == (1080, 1920)  # noqa: SLF001


# ------------------------------------------------------------- the autofix

def _bars_report() -> QAReport:
    """What QA hands the self-adjust loop when it finds letterboxing."""
    return QAReport(
        passed=False, score=75,
        issues=[QAIssue(check="barras_pretas", severity="erro",
                        message="Usable area 1080x1048 is smaller than the frame",
                        fix="Reframe the background.")],
        metrics={})


def test_black_bars_reframe_before_the_footage_is_dropped():
    """Regression: this used to jump straight to a gradient, so a job that
    downloaded a trailer delivered a short with none of it — and reported
    success."""
    job = JobInput(source_type="video", source="https://youtu.be/x",
                   background="video_fonte")
    fix = qa.suggest_fix(_bars_report(), job)

    assert fix is not None
    action, updated, stage = fix
    assert updated.background_fill == "preencher"
    assert updated.background == "video_fonte", "the footage must be kept"
    assert stage == "fundo"
    assert "reframing" in action.lower()


def test_the_gradient_is_the_last_resort_not_the_first():
    """Once reframing has been tried and the bars are still there, the source
    really is unusable — and the action says the footage was dropped."""
    job = JobInput(source_type="video", source="https://youtu.be/x",
                   background="video_fonte", background_fill="preencher")
    fix = qa.suggest_fix(_bars_report(), job)

    assert fix is not None
    action, updated, _ = fix
    assert updated.background == "gradiente"
    assert "too dark" in action or "gradient" in action


def test_there_is_nothing_left_to_try_after_the_gradient():
    """A fix that changes nothing would loop the autofix until it runs out of
    attempts."""
    job = JobInput(source_type="video", source="https://youtu.be/x",
                   background="gradiente", background_fill="preencher")
    assert qa.suggest_fix(_bars_report(), job) is None
