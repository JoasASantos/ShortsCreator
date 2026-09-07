"""The frame a video is composed into.

One format existed for a long time and its numbers lived as constants in four
files. The tests here guard two promises made when a second one arrived: the
vertical short renders exactly as before, and a 16:9 timeline is drawn, judged
and captioned as 16:9 all the way through — not as a short that happens to be
sideways.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app.pipeline import captions, formats, overlays, qa, render, timeline_render
from app.pipeline.timeline import CaptionCue, Timeline, VideoClip

from conftest import needs_ffmpeg


def _clip(path: Path, size: str, seconds: float = 3.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={size}:rate=30:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(path)], check=True, capture_output=True)
    return path


def _dims(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(video)],
        capture_output=True, text=True).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


# ---------------------------------------------------------------- the values

def test_the_vertical_short_is_the_default_everywhere():
    """Nothing that predates formats names one, and all of it has to keep
    producing the same file."""
    assert formats.get(None) is formats.VERTICAL
    assert formats.get("") is formats.VERTICAL
    assert Timeline(duration=1.0).fmt is formats.VERTICAL
    assert Timeline.from_dict({"duration": 1.0}).fmt is formats.VERTICAL


def test_the_vertical_numbers_are_the_ones_the_modules_used_to_carry():
    """These were module constants in captions.py and qa.py. Changing them here
    changes every short's caption placement — so it is asserted, not assumed."""
    v = formats.VERTICAL
    assert (v.width, v.height) == (1080, 1920)
    assert (v.safe_top, v.safe_bottom, v.side_margin) == (200, 340, 110)
    assert v.caption_font_size == 92
    assert v.caption_margins == {"baixo": 340, "centro": 780, "topo": 1280}
    assert (v.min_seconds, v.max_seconds) == (15, 90)


def test_a_format_resolves_by_name_or_by_aspect():
    assert formats.get("horizontal") is formats.HORIZONTAL
    assert formats.get("16:9") is formats.HORIZONTAL
    assert formats.get("9:16") is formats.VERTICAL
    assert formats.get("1:1") is formats.SQUARE


def test_an_unknown_format_falls_back_rather_than_raising():
    """A timeline written by an older version carries no format; a typo in a
    newer one must not make it unrenderable."""
    assert formats.get("panoramico") is formats.VERTICAL


def test_a_documentary_has_no_ceiling_only_a_floor():
    """Shorts are capped because the feeds cut them off. A documentary is half
    an hour long by design."""
    assert formats.HORIZONTAL.max_seconds is None
    assert formats.HORIZONTAL.min_seconds >= 60


def test_the_format_survives_a_round_trip_through_json():
    saved = Timeline(duration=5.0, format="horizontal").to_dict()
    assert saved["format"] == "horizontal"
    assert Timeline.from_dict(saved).fmt is formats.HORIZONTAL


# --------------------------------------------------------------- the filters

def test_the_old_filter_names_still_mean_the_vertical_short():
    """Callers that predate formats import these constants."""
    assert render.FIT_919 == render.fit_filter(formats.VERTICAL)
    assert render.FILL_919 == render.fill_filter(formats.VERTICAL)
    assert "1080:1920" in render.FIT_919


def test_a_horizontal_filter_targets_a_horizontal_frame():
    assert "1920:1080" in render.fit_filter(formats.HORIZONTAL)
    assert "1920:1080" in render.fill_filter(formats.HORIZONTAL)
    assert "1080:1920" not in render.fit_filter(formats.HORIZONTAL)


# --------------------------------------------------------------- the render

@needs_ffmpeg
def test_a_horizontal_timeline_renders_a_horizontal_file(tmp_path):
    """The point of the whole change: a 16:9 timeline comes out 1920x1080,
    with a 16:9 source neither cropped to a phone frame nor padded."""
    _clip(tmp_path / "src.mp4", "1920x1080")
    tl = Timeline(duration=3.0, format="horizontal",
                  video=[VideoClip(id="v", source="src.mp4", in_point=0,
                                   out_point=3, start=0)])
    out = timeline_render.render_timeline(tmp_path, tl, tmp_path / "out.mp4")
    assert _dims(out) == (1920, 1080)


@needs_ffmpeg
def test_the_vertical_timeline_still_renders_a_vertical_file(tmp_path):
    _clip(tmp_path / "src.mp4", "1920x1080")
    tl = Timeline(duration=3.0,
                  video=[VideoClip(id="v", source="src.mp4", in_point=0,
                                   out_point=3, start=0)])
    out = timeline_render.render_timeline(tmp_path, tl, tmp_path / "out.mp4")
    assert _dims(out) == (1080, 1920)


@needs_ffmpeg
def test_gaps_are_filled_in_the_timelines_own_frame(tmp_path):
    """A gap used to be a 1080x1920 black clip. Concatenated into a 16:9 track
    it would be a frame of the wrong size mid-film."""
    _clip(tmp_path / "src.mp4", "1920x1080")
    tl = Timeline(duration=4.0, format="horizontal",
                  video=[VideoClip(id="v", source="src.mp4", in_point=0,
                                   out_point=2, start=2)])   # 2s gap first
    out = timeline_render.render_timeline(tmp_path, tl, tmp_path / "out.mp4")
    assert _dims(out) == (1920, 1080)


# --------------------------------------------------------------- the captions

def test_the_subtitle_canvas_matches_the_frame(tmp_path):
    """PlayRes tells libass what the coordinates mean. A 16:9 file with a
    1080x1920 PlayRes puts every caption in the wrong place."""
    words = [{"word": "golpe", "start": 0.0, "end": 0.5}]
    vertical = captions.build_ass(words, tmp_path / "v.ass").read_text()
    horizontal = captions.build_ass(words, tmp_path / "h.ass",
                                    fmt=formats.HORIZONTAL).read_text()
    assert "PlayResX: 1080" in vertical and "PlayResY: 1920" in vertical
    assert "PlayResX: 1920" in horizontal and "PlayResY: 1080" in horizontal


def test_subtitles_on_a_horizontal_frame_read_as_subtitles():
    """92px is a hook on a phone and a billboard on a television frame."""
    assert formats.HORIZONTAL.caption_font_size < formats.VERTICAL.caption_font_size
    assert formats.HORIZONTAL.caption_max_chars > formats.VERTICAL.caption_max_chars


def test_a_vertical_ass_file_is_byte_for_byte_what_it_was(tmp_path):
    """The default path must not have moved a single margin."""
    words = [{"word": "Ninguém", "start": 0.1, "end": 0.5},
             {"word": "avisou", "start": 0.5, "end": 1.0}]
    text = captions.build_ass(words, tmp_path / "v.ass").read_text()
    legend = next(l for l in text.splitlines() if l.startswith("Style: Legenda"))
    fields = legend.split(":", 1)[1].split(",")
    assert fields[2].strip() == "92"                          # font size
    assert [f.strip() for f in fields[19:22]] == ["110", "110", "780"]  # margins


@needs_ffmpeg
def test_caption_pngs_are_placed_inside_the_frame_they_are_for(tmp_path):
    """The PNG fallback (FFmpeg builds without libass) computes y from the
    frame height. On a 16:9 frame a caption placed for 1920px tall would sit
    below the bottom edge."""
    words = [{"word": "golpe", "start": 0.0, "end": 0.5},
             {"word": "digital", "start": 0.5, "end": 1.0}]
    items = overlays.render_captions(words, tmp_path / "ov", position="baixo",
                                     fmt=formats.HORIZONTAL)
    assert items
    for item in items:
        assert 0 <= item.y < formats.HORIZONTAL.height, f"y={item.y} is off-frame"
        assert item.y > formats.HORIZONTAL.height // 2, "a bottom caption sits low"


# ---------------------------------------------------------------- the audit

@needs_ffmpeg
def test_qa_judges_a_file_by_the_frame_it_was_meant_to_be(tmp_path):
    """The same 1920x1080 file: correct as a documentary, wrong as a short.
    Both verdicts have to come from the same function."""
    video = _clip(tmp_path / "doc.mp4", "1920x1080", seconds=3.0)

    as_doc = qa.audit(video, None, fmt=formats.HORIZONTAL)
    assert not {i.check for i in as_doc.issues} & {"resolucao", "proporcao"}

    as_short = qa.audit(video, None)
    assert {"resolucao", "proporcao"} <= {i.check for i in as_short.issues}


@needs_ffmpeg
def test_a_long_documentary_is_not_failed_for_being_long(tmp_path):
    """90 seconds is a short's ceiling. Judged as a documentary, length is not
    a defect — only the target is."""
    video = _clip(tmp_path / "doc.mp4", "1920x1080", seconds=3.0)
    report = qa.audit(video, None, fmt=formats.HORIZONTAL)
    assert "duracao_maxima" not in {i.check for i in report.issues}


@needs_ffmpeg
def test_an_aspect_failure_reports_instead_of_raising(tmp_path):
    """Regression: `fmt` was both the Format parameter and ffprobe's container
    section, so the mismatch branch read `.aspect` off a dict and blew up
    where it should have produced an issue."""
    video = _clip(tmp_path / "wide.mp4", "1920x1080", seconds=3.0)
    report = qa.audit(video, None)   # judged as a short, on purpose
    proportion = [i for i in report.issues if i.check == "proporcao"]
    assert proportion and "9:16" in proportion[0].message
