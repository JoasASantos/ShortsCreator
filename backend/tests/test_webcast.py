"""Filming a real page instead of transcribing it.

The bug this closes was visible in the finished short: a video about a
repository showed its README's raw markup scrolling past — `<p align="center">`
and all — because the "scroll" background rendered the page's *text* into a
tall image. What the page looks like to someone who opens it never appeared.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.pipeline import formats, ingest, orchestrator, webcast
from app.pipeline.ingest import SourceMaterial
from app.schemas import JobInput, ScriptSegment, ShortScript

needs_browser = pytest.mark.skipif(not webcast.available()[0],
                                   reason=webcast.available()[1])

# The viewport meta matters: without it a mobile browser lays the page out at
# 980 CSS px, which is what an old site really does look like on a phone. With
# it, the page is a column — which is the case worth testing here.
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { margin: 0; font-family: system-ui; background: #0b1020; color: #eee; }
  h1 { color: #ffd400; padding: 24px; margin: 0; }
  section { height: 600px; padding: 24px; font-size: 20px; }
  .a { background: #11224a; } .b { background: #3a1030; } .c { background: #103a22; }
</style></head><body>
<h1>RTK &mdash; Rust Token Killer</h1>
<section class="a">Primeira dobra da p&aacute;gina, com texto de verdade.</section>
<section class="b">Segunda dobra, outra cor, para o scroll ser vis&iacute;vel.</section>
<section class="c">Terceira dobra.</section>
<section class="a">Quarta dobra.</section>
<section class="b">Quinta dobra.</section>
<section class="c">Sexta dobra, o fim da p&aacute;gina.</section>
</body></html>"""


def _page(tmp_path: Path) -> str:
    path = tmp_path / "page.html"
    path.write_text(PAGE, encoding="utf-8")
    return path.as_uri()


def _duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True)
    return float(out.stdout.strip())


def _pixel(video: Path, at: float) -> tuple[int, int, int]:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(video), "-frames:v", "1",
         "-vf", "crop=40:40:520:900,scale=1:1", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], check=True, capture_output=True)
    return tuple(out.stdout[:3])  # type: ignore[return-value]


# ------------------------------------------------------------- availability

def test_a_missing_browser_is_a_state_with_a_way_out(monkeypatch):
    """Every caller has a background to fall back to, so this must never be an
    exception with a traceback."""
    monkeypatch.setattr(webcast, "_browser_channel", lambda: None)
    monkeypatch.setattr(webcast, "_bundled_browser_installed", lambda: False)

    ok, reason = webcast.available()
    assert ok is False
    assert "playwright install" in reason or "Chrome" in reason


def test_recording_without_a_browser_raises_the_typed_error(monkeypatch, tmp_path):
    monkeypatch.setattr(webcast, "available", lambda: (False, "no browser here"))
    with pytest.raises(webcast.RecorderUnavailable, match="no browser here"):
        webcast.record_scroll("https://example.test", 5.0, tmp_path / "bg.mp4")


# ------------------------------------------------------- what gets recorded

@needs_browser
def test_the_page_itself_is_recorded_and_it_scrolls(tmp_path):
    """The whole point: the rendered page, moving, at the frame's size."""
    out = tmp_path / "bg.mp4"
    webcast.record_scroll(_page(tmp_path), 4.0, out, fmt=formats.VERTICAL)

    assert out.exists()
    assert _duration(out) == pytest.approx(4.0, abs=0.6)
    # the frame is the short's frame
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=s=x:p=0", str(out)],
        check=True, capture_output=True, text=True).stdout.strip()
    assert probe == "1080x1920"
    # and the picture changes as the page moves under the camera
    assert _pixel(out, 0.5) != _pixel(out, 3.5), "the page never moved"


@needs_browser
def test_a_page_shorter_than_the_frame_is_held_not_cut_to_black(tmp_path):
    """Nothing to scroll is a normal page, not a failure."""
    short_page = tmp_path / "tiny.html"
    short_page.write_text(
        "<meta name='viewport' content='width=device-width'>"
        "<body style='background:#204080;margin:0'>oi</body>", encoding="utf-8")
    out = tmp_path / "bg.mp4"
    webcast.record_scroll(short_page.as_uri(), 3.0, out, fmt=formats.VERTICAL)

    assert _duration(out) >= 2.9
    assert _pixel(out, 2.8)[2] > 60, "the end of the clip went black"


@needs_browser
def test_a_page_that_does_not_open_says_which_one(tmp_path):
    with pytest.raises(RuntimeError, match="Could not open"):
        webcast.record_scroll("file:///nowhere/at/all.html", 2.0,
                              tmp_path / "bg.mp4")


# ------------------------------------------------ choosing the page to film

def _job(**kwargs) -> JobInput:
    base = {"source_type": "url", "source": "https://exemplo.test/artigo"}
    base.update(kwargs)
    return JobInput(**base)


def test_the_pasted_link_is_the_page_to_film():
    material = SourceMaterial(kind="artigo", title="x",
                              url="https://exemplo.test/artigo")
    assert orchestrator._page_to_record(_job(), material) == \
        "https://exemplo.test/artigo"  # noqa: SLF001


def test_an_explicit_address_wins_over_the_source():
    material = SourceMaterial(kind="artigo", title="x", url="https://a.test/x")
    job = _job(background_query="https://b.test/y")
    assert orchestrator._page_to_record(job, material) == "https://b.test/y"  # noqa: SLF001


def test_a_video_link_is_not_a_page_to_scroll():
    """It is a video, and the source-video background already exists for it."""
    material = SourceMaterial(kind="video", title="x",
                              url="https://youtube.com/watch?v=abc")
    job = _job(source="https://youtube.com/watch?v=abc")
    assert orchestrator._page_to_record(job, material) == ""  # noqa: SLF001


def test_a_theme_with_no_link_has_nothing_to_film():
    material = SourceMaterial(kind="tema", title="x", text="x")
    assert orchestrator._page_to_record(  # noqa: SLF001
        _job(source_type="tema", source="um tema"), material) == ""


def test_a_search_phrase_in_the_background_query_is_not_an_address():
    """`background_query` is normally stock search terms, not a URL."""
    material = SourceMaterial(kind="tema", title="x", text="x")
    job = _job(source_type="tema", source="um tema",
               background_query="hands typing on a laptop")
    assert orchestrator._page_to_record(job, material) == ""  # noqa: SLF001


# ----------------------------------------------------- the background choice

def _script() -> ShortScript:
    return ShortScript(title="t", description="d", hashtags=[],
                       segments=[ScriptSegment(kind="corpo", text="uma frase")],
                       estimated_seconds=20)


class _Narration:
    duration = 20.0
    words = [{"word": "uma", "start": 0.0, "end": 0.4}]


def _run(job, material, tmp_path, monkeypatch, recorder=None):
    called: list[str] = []
    events: list[tuple[str, str]] = []
    from app.pipeline import broll, imagegen, render

    for name in ("background_gradient", "background_from_video",
                 "background_from_clips", "background_from_images_kenburns",
                 "background_from_multi_highlights"):
        monkeypatch.setattr(render, name,
                            lambda *a, _n=name, **k: called.append(_n))
    monkeypatch.setattr(render, "ensure_min_duration", lambda p, d, w: p)
    monkeypatch.setattr(broll, "providers_ready", lambda: [])
    monkeypatch.setattr(imagegen, "providers_ready", lambda *a, **k: False)
    monkeypatch.setattr(webcast, "available",
                        lambda: (recorder is not None, "no browser"))
    if recorder is not None:
        monkeypatch.setattr(webcast, "record_scroll",
                            lambda *a, **k: called.append("record_scroll") or recorder(*a, **k))

    orchestrator._build_background(  # noqa: SLF001
        job, _script(), _Narration(), material, tmp_path, 20.0,
        lambda m, level="info": events.append((level, m)))
    return called, events


def test_auto_films_the_page_when_there_is_one(tmp_path, monkeypatch):
    """A short about a repository should show that repository's page, the way
    whoever opened it saw it — not a gradient."""
    material = SourceMaterial(kind="artigo", title="x", url="https://a.test/x")
    called, _ = _run(_job(background="auto"), material, tmp_path, monkeypatch,
                     recorder=lambda *a, **k: a[2])

    assert called[0] == "record_scroll"


def test_auto_falls_back_when_there_is_no_browser(tmp_path, monkeypatch):
    material = SourceMaterial(kind="artigo", title="x", url="https://a.test/x")
    called, _ = _run(_job(background="auto"), material, tmp_path, monkeypatch,
                     recorder=None)

    assert called == ["background_gradient"]


def test_asking_for_the_recording_without_a_browser_refuses(tmp_path, monkeypatch):
    """Asked for something specific and it cannot be done: say so, rather than
    drawing a gradient that looks finished."""
    material = SourceMaterial(kind="artigo", title="x", url="https://a.test/x")

    def refuse(*a, **k):
        raise webcast.RecorderUnavailable("no browser here")

    with pytest.raises(RuntimeError, match="could not be recorded"):
        _run(_job(background="site_scroll"), material, tmp_path, monkeypatch,
             recorder=refuse)


def test_asking_for_the_recording_with_no_url_refuses(tmp_path, monkeypatch):
    material = SourceMaterial(kind="tema", title="x", text="x")
    job = _job(source_type="tema", source="um tema", background="site_scroll")

    with pytest.raises(RuntimeError, match="no URL"):
        _run(job, material, tmp_path, monkeypatch, recorder=lambda *a, **k: a[2])


def test_real_footage_still_beats_filming_the_page(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fake")
    material = SourceMaterial(kind="video", title="x", video_path=video,
                              url="https://a.test/x")
    called, _ = _run(_job(background="auto"), material, tmp_path, monkeypatch,
                     recorder=lambda *a, **k: a[2])

    assert called == ["background_from_video"]
