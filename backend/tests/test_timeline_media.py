"""Media overlays (picture-in-picture) on the timeline.

The point of the feature is dropping a screenshot, diagram or stock clip on top
of your own recording. Geometry is stored as fractions of the frame, so these
tests render real video and sample pixels: a filter graph that composites the
wrong region, or nothing at all, still exits 0.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.pipeline import timeline_render
from app.pipeline.timeline import MediaOverlay, Timeline, VideoClip

from conftest import needs_ffmpeg

W, H = 1080, 1920


def _solid(path: Path, colour: str, size: str, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s={size}:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True)
    return path


def _still(path: Path, colour: str, size: str) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s={size}:d=1",
         "-frames:v", "1", str(path)], check=True, capture_output=True)
    return path


def _pixel(video: Path, at: float, x: int, y: int) -> tuple[int, int, int]:
    """The colour at one point of one frame, averaged over a 4x4 patch so a
    single compression artefact cannot flip the result."""
    frame = video.parent / f"probe_{at}_{x}_{y}.png"
    subprocess.run(["ffmpeg", "-y", "-ss", f"{at}", "-i", str(video),
                    "-frames:v", "1", str(frame)], check=True, capture_output=True)
    out = subprocess.run(
        ["ffmpeg", "-i", str(frame), "-vf", f"crop=4:4:{x}:{y},scale=1:1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True)
    return tuple(out.stdout[:3])  # type: ignore[return-value]


def _is(colour: tuple[int, int, int], r: int, g: int, b: int, slack: int = 12) -> bool:
    return all(abs(a - b_) <= slack for a, b_ in zip(colour, (r, g, b)))


# ------------------------------------------------------------------ geometry

@needs_ffmpeg
def test_an_overlay_lands_where_its_fractions_say(tmp_path):
    """x/y are the overlay's centre and width is a fraction of the frame. A
    square at x=0.5, width=0.5 must span 270..810 horizontally — the edges are
    what the user drags, so they have to be exact."""
    _solid(tmp_path / "bg.mp4", "green", f"{W}x{H}", 3)
    _still(tmp_path / "pip.png", "red", "400x400")

    timeline = Timeline(
        duration=3.0,
        video=[VideoClip(id="v1", source="bg.mp4", in_point=0, out_point=3, start=0)],
        media=[MediaOverlay(id="m1", source="pip.png", start=1.0, end=2.0,
                            x=0.5, y=0.25, width=0.5)],
    )
    out = timeline_render.render_timeline(tmp_path, timeline, tmp_path / "r.mp4")

    assert _is(_pixel(out, 1.5, 538, 476), 255, 0, 0), "the overlay was not drawn"
    # a square scaled to 540 wide is 540 tall, centred at (540, 480)
    assert _is(_pixel(out, 1.5, 290, 476), 255, 0, 0), "left edge fell short of 270"
    assert _is(_pixel(out, 1.5, 250, 476), 0, 128, 0), "it spilled past its left edge"
    assert _is(_pixel(out, 1.5, 538, 760), 0, 128, 0), "it spilled past its bottom edge"


@needs_ffmpeg
def test_an_overlay_only_shows_inside_its_window(tmp_path):
    """Regression: the cut input's frames start at its own zero while `enable`
    is gated on the timeline clock, so without a pad the input has already run
    out by the time its window opens and nothing is ever drawn."""
    _solid(tmp_path / "bg.mp4", "green", f"{W}x{H}", 3)
    _still(tmp_path / "pip.png", "red", "400x400")

    timeline = Timeline(
        duration=3.0,
        video=[VideoClip(id="v1", source="bg.mp4", in_point=0, out_point=3, start=0)],
        media=[MediaOverlay(id="m1", source="pip.png", start=1.0, end=2.0,
                            x=0.5, y=0.25, width=0.5)],
    )
    out = timeline_render.render_timeline(tmp_path, timeline, tmp_path / "r.mp4")

    assert _is(_pixel(out, 0.4, 538, 476), 0, 128, 0), "drawn before its start"
    assert _is(_pixel(out, 1.5, 538, 476), 255, 0, 0), "missing inside the window"
    assert _is(_pixel(out, 2.6, 538, 476), 0, 128, 0), "still drawn after its end"


@needs_ffmpeg
def test_a_video_overlay_keeps_its_aspect_ratio(tmp_path):
    """Only the width is set; the height follows the source. A 2:1 clip at
    width=0.9 must come out 972x486, not stretched to a square."""
    _solid(tmp_path / "bg.mp4", "black", f"{W}x{H}", 3)
    _solid(tmp_path / "clip.mp4", "blue", "600x300", 5)

    timeline = Timeline(
        duration=3.0,
        video=[VideoClip(id="v1", source="bg.mp4", in_point=0, out_point=3, start=0)],
        media=[MediaOverlay(id="m1", source="clip.mp4", kind="video", in_point=2.0,
                            start=0.5, end=1.5, x=0.5, y=0.6, width=0.9)],
    )
    out = timeline_render.render_timeline(tmp_path, timeline, tmp_path / "r.mp4")

    # 486 tall, centred at y=1152, so it spans 909..1395
    assert _is(_pixel(out, 1.0, 538, 1150), 0, 0, 255), "the clip was not drawn"
    assert _is(_pixel(out, 1.0, 538, 920), 0, 0, 255), "shorter than 2:1 implies"
    assert _is(_pixel(out, 1.0, 538, 880), 0, 0, 0), "taller than 2:1 implies"


@needs_ffmpeg
def test_opacity_blends_with_what_is_underneath(tmp_path):
    """A half-transparent white over black has to read as mid grey; at full
    opacity the filter chain skips the alpha step entirely."""
    _solid(tmp_path / "bg.mp4", "black", f"{W}x{H}", 3)
    _still(tmp_path / "white.png", "white", "200x200")

    timeline = Timeline(
        duration=3.0,
        video=[VideoClip(id="v1", source="bg.mp4", in_point=0, out_point=3, start=0)],
        media=[MediaOverlay(id="m1", source="white.png", start=0.5, end=2.5,
                            x=0.25, y=0.2, width=0.2, opacity=0.5)],
    )
    out = timeline_render.render_timeline(tmp_path, timeline, tmp_path / "r.mp4")

    grey = _pixel(out, 1.5, 268, 382)
    assert _is(grey, 128, 128, 128, slack=18), f"expected mid grey, got {grey}"


# ------------------------------------------------------------ the data model

def test_an_overlay_cannot_stretch_the_short():
    """Media sits *on top* of the video. If an overlay dragged past the end
    extended the duration, the short would gain black frames."""
    timeline = Timeline(
        duration=0.0,
        video=[VideoClip(id="v1", source="a.mp4", in_point=0, out_point=4, start=0)],
        media=[MediaOverlay(id="m1", source="p.png", start=3.0, end=90.0)],
    ).normalize()

    assert timeline.duration == 4.0
    assert timeline.media[0].end == 4.0, "the overlay should be clipped to the end"


def test_an_overlay_dragged_off_screen_is_pulled_back():
    timeline = Timeline(
        duration=0.0,
        video=[VideoClip(id="v1", source="a.mp4", in_point=0, out_point=4, start=0)],
        media=[MediaOverlay(id="m1", source="p.png", start=0, end=2,
                            x=1.8, y=-0.4, width=3.0, opacity=0.0)],
    ).normalize()

    item = timeline.media[0]
    assert (item.x, item.y) == (1.0, 0.0)
    assert item.width == 1.0
    assert item.opacity > 0, "a fully transparent overlay is invisible, not an overlay"


def test_an_overlay_with_no_duration_is_dropped():
    timeline = Timeline(
        duration=0.0,
        video=[VideoClip(id="v1", source="a.mp4", in_point=0, out_point=4, start=0)],
        media=[MediaOverlay(id="m1", source="p.png", start=2.0, end=2.0)],
    ).normalize()
    assert timeline.media == []


def test_media_survives_a_round_trip_through_json():
    """The editor reads and writes this JSON; a field lost in from_dict would
    silently reset an overlay the user had positioned."""
    original = Timeline(
        duration=4.0,
        video=[VideoClip(id="v1", source="a.mp4", in_point=0, out_point=4, start=0)],
        media=[MediaOverlay(id="m1", source="p.mp4", kind="video", start=1.0,
                            end=3.0, x=0.3, y=0.7, width=0.45, opacity=0.8,
                            in_point=12.5, mute=False)],
    )
    again = Timeline.from_dict(original.to_dict())
    assert again.media == original.media
