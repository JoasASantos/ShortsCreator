"""Dissolves and camera movement — the difference between an edit and a slideshow.

Two guarantees, and the second is the one that would break silently: a
crossfade must not move anything. The subtitles and the narration are placed on
the timeline's clock, so a transition that shortens the video track slides the
picture away from the voice — and only near the end, where nobody looks.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.pipeline import formats, timeline_render
from app.pipeline.timeline import Timeline, VideoClip

from conftest import needs_ffmpeg

W, H = 1920, 1080


def _solid(path: Path, colour: str, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s={W}x{H}:d={seconds}",
         "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True)
    return path


def _still(path: Path, colour: str) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s={W}x{H}:d=1",
         "-frames:v", "1", str(path)], check=True, capture_output=True)
    return path


def _duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True)
    return float(out.stdout.strip())


def _pixel(video: Path, at: float) -> tuple[int, int, int]:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(video), "-frames:v", "1",
         "-vf", "crop=8:8:960:540,scale=1:1", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], check=True, capture_output=True)
    return tuple(out.stdout[:3])  # type: ignore[return-value]


def _clip(source: str, start: float, length: float, kind: str = "video",
          mute: bool = True) -> VideoClip:
    return VideoClip(id=f"v_{source}_{start}", source=source, in_point=0.0,
                     out_point=length, start=start, kind=kind, mute=mute)


def _timeline(clips: list[VideoClip]) -> Timeline:
    return Timeline(duration=max(c.start + c.duration for c in clips),
                    video=clips, audio=[], captions=[], media=[],
                    format="horizontal").normalize()


# ------------------------------------------------ which boundaries dissolve

def test_two_silent_shots_dissolve_into_each_other():
    timeline = _timeline([_clip("a.mp4", 0.0, 4.0), _clip("b.mp4", 4.0, 4.0)])
    assert timeline_render._crossfades(timeline) == {0}  # noqa: SLF001


def test_a_quote_cuts_hard_on_both_sides():
    """An interview cut is unmuted because its own audio is on the timeline.
    Dissolving over someone's sentence smears the words."""
    timeline = _timeline([_clip("a.mp4", 0.0, 4.0),
                          _clip("quote.mp4", 4.0, 4.0, mute=False),
                          _clip("c.mp4", 8.0, 4.0)])
    assert timeline_render._crossfades(timeline) == set()  # noqa: SLF001


def test_a_shot_too_short_for_the_dissolve_is_left_alone():
    """A 0.4s dissolve on a 0.5s shot is the whole shot."""
    timeline = _timeline([_clip("a.mp4", 0.0, 4.0), _clip("b.mp4", 4.0, 0.5),
                          _clip("c.mp4", 4.5, 4.0)])
    assert timeline_render._crossfades(timeline) == set()  # noqa: SLF001


def test_a_hole_in_the_timeline_is_not_a_transition():
    """Dissolving into black is a fade-out, not a transition — and the hole
    is a bug to report, not to soften."""
    clips = [_clip("a.mp4", 0.0, 4.0), _clip("b.mp4", 6.0, 4.0)]
    assert timeline_render._crossfades(_timeline(clips)) == set()  # noqa: SLF001


# --------------------------------------------------- the clock does not move

@needs_ffmpeg
def test_a_dissolve_does_not_shift_the_timeline(tmp_path):
    """The guarantee that matters. If xfade shortened the track, every later
    subtitle and every narration line would drift — and the drift grows with
    each transition, so it only becomes visible near the end."""
    red, green, blue = (_solid(tmp_path / f"{name}.mp4", name, 6.0)
                        for name in ("red", "green", "blue"))
    timeline = _timeline([_clip(red.name, 0.0, 4.0), _clip(green.name, 4.0, 4.0),
                          _clip(blue.name, 8.0, 4.0)])

    track = timeline_render._build_video_track(tmp_path, timeline, lambda *a: None)  # noqa: SLF001

    assert _duration(track) == pytest.approx(12.0, abs=0.15)
    # and the shots are still where the timeline says they are
    assert _pixel(track, 1.0)[0] > 150, "red at 1s"
    assert _pixel(track, 6.0)[1] > 100, "green at 6s"
    assert _pixel(track, 10.0)[2] > 100, "blue at 10s"


@needs_ffmpeg
def test_the_dissolve_actually_mixes_the_two_shots(tmp_path):
    """Halfway through the transition the frame is neither shot."""
    red = _solid(tmp_path / "red.mp4", "red", 6.0)
    green = _solid(tmp_path / "green.mp4", "green", 6.0)
    timeline = _timeline([_clip(red.name, 0.0, 4.0), _clip(green.name, 4.0, 4.0)])

    track = timeline_render._build_video_track(tmp_path, timeline, lambda *a: None)  # noqa: SLF001

    middle = _pixel(track, 4.0 + timeline_render.TRANSITION / 2)
    assert middle[0] > 40 and middle[1] > 40, f"expected a mix, got {middle}"


@needs_ffmpeg
def test_hard_cuts_still_produce_the_exact_same_length(tmp_path):
    """Nothing dissolving must keep the cheap concat path."""
    red = _solid(tmp_path / "red.mp4", "red", 6.0)
    quote = _solid(tmp_path / "quote.mp4", "green", 6.0)
    timeline = _timeline([_clip(red.name, 0.0, 4.0),
                          _clip(quote.name, 4.0, 4.0, mute=False)])

    track = timeline_render._build_video_track(tmp_path, timeline, lambda *a: None)  # noqa: SLF001
    assert _duration(track) == pytest.approx(8.0, abs=0.15)


# --------------------------------------------------------------- Ken Burns

def test_a_still_gets_a_move_and_they_alternate():
    fmt = formats.HORIZONTAL
    push = timeline_render._ken_burns(0, 6.0, fmt)   # noqa: SLF001
    pull = timeline_render._ken_burns(1, 6.0, fmt)   # noqa: SLF001

    assert "zoompan" in push and "zoompan" in pull
    assert push != pull, "consecutive stills must not perform the same move"
    # fed from a larger frame so the crop has real pixels to move over
    assert f"scale={fmt.width * 3 // 2}:{fmt.height * 3 // 2}" in push


@needs_ffmpeg
def test_a_still_on_the_timeline_is_not_a_frozen_frame(tmp_path):
    """A photograph on screen for six seconds reads as a broken player."""
    picture = tmp_path / "shot.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={W}x{H}:duration=1:rate=1", "-frames:v", "1",
         str(picture)], check=True, capture_output=True)
    timeline = _timeline([_clip(picture.name, 0.0, 5.0, kind="image")])

    track = timeline_render._build_video_track(tmp_path, timeline, lambda *a: None)  # noqa: SLF001

    early, late = _pixel(track, 0.4), _pixel(track, 4.4)
    assert early != late, "the still never moved"
    assert _duration(track) == pytest.approx(5.0, abs=0.2)


@needs_ffmpeg
def test_a_still_too_short_to_move_is_still_rendered(tmp_path):
    still = _still(tmp_path / "flash.png", "blue")
    timeline = _timeline([_clip(still.name, 0.0, 0.8, kind="image")])

    track = timeline_render._build_video_track(tmp_path, timeline, lambda *a: None)  # noqa: SLF001
    assert _duration(track) == pytest.approx(0.8, abs=0.15)
    assert _pixel(track, 0.4)[2] > 100


@needs_ffmpeg
def test_a_chain_that_mixes_cuts_and_dissolves_renders(tmp_path):
    """Regression, and it cost a seven-minute render: concat hands on a
    1/1000000 timebase and xfade hands on 1/15360, so the first dissolve after
    a hard cut died with "First input link main timebase do not match the
    corresponding second input link". Two clips never showed it — the chain
    has to mix the two filters for the mismatch to exist."""
    sources = {name: _solid(tmp_path / f"{name}.mp4", colour, 6.0)
               for name, colour in (("a", "red"), ("b", "green"),
                                    ("c", "blue"), ("d", "yellow"),
                                    ("e", "white"))}
    clips = [
        _clip(sources["a"].name, 0.0, 4.0),                  # dissolve into b
        _clip(sources["b"].name, 4.0, 4.0),                  # hard cut: quote
        _clip(sources["c"].name, 8.0, 4.0, mute=False),      # the quote
        _clip(sources["d"].name, 12.0, 4.0),                 # dissolve into e
        _clip(sources["e"].name, 16.0, 4.0),
    ]
    timeline = _timeline(clips)
    assert timeline_render._crossfades(timeline) == {0, 3}  # noqa: SLF001

    track = timeline_render._build_video_track(tmp_path, timeline, lambda *a: None)  # noqa: SLF001

    assert _duration(track) == pytest.approx(20.0, abs=0.2)
    # and every shot is still in its slot after two dissolves and two cuts
    assert _pixel(track, 1.0)[0] > 150, "red at 1s"
    assert _pixel(track, 10.0)[2] > 100, "the quote at 10s"
    assert min(_pixel(track, 18.0)) > 150, "white at 18s"


# ------------------------ a part that cannot be read back -------------------

@needs_ffmpeg
def test_a_truncated_part_is_rendered_again_instead_of_failing_the_join(tmp_path,
                                                                       monkeypatch):
    """An MP4's moov atom is written last, so an interrupted write leaves a file
    that exists, has a plausible size, and cannot be opened. ffmpeg then failed
    at the join, after every other part was rendered, naming one file out of
    forty — it cost two renders of the same seven-minute film."""
    source = _solid(tmp_path / "red.mp4", "red", 4.0)
    timeline = _timeline([_clip(source.name, 0.0, 2.0)])
    real_run = timeline_render.render._run
    calls = {"n": 0}

    def truncate_the_first(cmd, **kwargs):
        calls["n"] += 1
        real_run(cmd, **kwargs)
        if calls["n"] == 1:
            # what an interrupted write leaves behind
            Path(cmd[-1]).write_bytes(b"\x00" * 40_000)

    monkeypatch.setattr(timeline_render.render, "_run", truncate_the_first)
    track = timeline_render._build_video_track(tmp_path, timeline, lambda *a: None)  # noqa: SLF001

    assert calls["n"] >= 2, "the bad part was rendered again"
    assert _duration(track) == pytest.approx(2.0, abs=0.15)


@needs_ffmpeg
def test_a_part_that_never_comes_out_readable_names_itself(tmp_path, monkeypatch):
    source = _solid(tmp_path / "red.mp4", "red", 4.0)
    timeline = _timeline([_clip(source.name, 0.0, 2.0)])

    monkeypatch.setattr(timeline_render.render, "_run",
                        lambda cmd, **k: Path(cmd[-1]).write_bytes(b"\x00" * 40_000))

    with pytest.raises(RuntimeError, match="part_000.mp4"):
        timeline_render._build_video_track(tmp_path, timeline, lambda *a: None)  # noqa: SLF001
