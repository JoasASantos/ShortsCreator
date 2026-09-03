"""Cover: frame choice, safe area and title legibility."""
from __future__ import annotations

from PIL import Image, ImageStat

from app.config import settings
from app.pipeline import cover, render
from app.pipeline.captions import SAFE_BOTTOM

from conftest import needs_ffmpeg

W, H = settings.width, settings.height


@needs_ffmpeg
def test_cover_comes_out_in_9x16(sample_video, tmp_path):
    out, at = cover.build(sample_video, "Título de teste", "tecnologia",
                          tmp_path / "cover.jpg", 4.0, tmp_path)
    assert out.exists()
    assert Image.open(out).size == (W, H)
    assert at >= cover.SKIP_HEAD


@needs_ffmpeg
def test_chosen_frame_avoids_the_start_and_the_end(sample_video, tmp_path):
    """The start is usually a fade-in and the end a hard cut — neither one
    represents the video in a thumbnail."""
    at = cover.pick_frame_time(sample_video, 4.0, tmp_path)
    assert cover.SKIP_HEAD <= at <= 4.0 * 0.85


def test_title_never_enters_the_app_ui_area():
    """Same rule QA enforces for the caption: the bottom 340 px belong to the UI."""
    assert cover.TITLE_BOTTOM <= H - SAFE_BOTTOM


def test_long_and_short_titles_both_stay_inside_the_safe_area():
    """Anchored to the footer: with 1 or 3 lines, the block ends in the same place."""
    base = Image.new("RGB", (W, H), (255, 255, 255))
    limit = H - SAFE_BOTTOM

    for title in ("Vazou", "Como o ataque da SolarWinds funcionou de verdade"):
        img = cover._compose(base.copy(), title, "tecnologia")  # noqa: SLF001
        # no dark text marks below the safe-area limit
        band = img.crop((0, limit + 20, W, H)).convert("L")
        assert ImageStat.Stat(band).stddev[0] < 40, (
            f"'{title[:20]}' drew content inside the app UI band")


def test_title_stays_legible_over_a_white_background():
    """Without the darkening, white text over a bright image disappears. The
    contrast is measured where the title is actually drawn."""
    white = Image.new("RGB", (W, H), (255, 255, 255))
    img = cover._compose(white, "Como o ataque funcionou", "tecnologia")  # noqa: SLF001

    band = img.crop((0, cover.TITLE_BOTTOM - 200, W, cover.TITLE_BOTTOM)).convert("L")
    # the title region is no longer white (it was darkened)
    assert ImageStat.Stat(band).mean[0] < 130
    # and it still has contrast — the white text on top of the dark scrim
    assert ImageStat.Stat(band).stddev[0] > 25


def test_compose_handles_a_huge_title_without_overflowing():
    base = Image.new("RGB", (W, H), (40, 40, 40))
    img = cover._compose(base, " ".join(["palavra"] * 40), "generico")  # noqa: SLF001
    assert img.size == (W, H)


def test_invalid_hex_falls_back_to_the_default_amber():
    assert cover._hex("#ffc400") == (255, 196, 0)   # noqa: SLF001
    assert cover._hex("lixo") == (255, 196, 0)      # noqa: SLF001


@needs_ffmpeg
def test_preview_gif_is_small_and_animated(sample_video, tmp_path):
    gif = render.make_preview_gif(sample_video, tmp_path / "p.gif", seconds=2.0)
    assert gif.exists()
    # dashboard thumbnail: it has to be light enough to load inside a list
    assert gif.stat().st_size < 900_000
    with Image.open(gif) as im:
        assert im.n_frames > 1, "the GIF came out with a single frame"
        assert im.width == 270


# --------------------------------------------------- watermark as an overlay

@needs_ffmpeg
def test_watermark_overlay_honours_position_size_and_opacity(tmp_path):
    """The PNG path is what runs when FFmpeg has no libass, so it has to
    respect the same three settings the ASS path does."""
    from app.pipeline import overlays

    small = overlays.render_watermark("@channel", tmp_path, 5.0, size="pequeno")
    large = overlays.render_watermark("@channel", tmp_path / "l", 5.0, size="grande")
    assert small and large
    assert Image.open(large.path).width > Image.open(small.path).width

    left = overlays.render_watermark("@channel", tmp_path / "a", 5.0,
                                     position="baixo_esquerda")
    right = overlays.render_watermark("@channel", tmp_path / "b", 5.0,
                                      position="baixo_direita")
    top = overlays.render_watermark("@channel", tmp_path / "c", 5.0,
                                    position="topo_centro")
    assert left.x < right.x
    assert top.y < left.y            # the top anchor sits higher up the frame

    faint = overlays.render_watermark("@c", tmp_path / "d", 5.0, opacity=0.2)
    solid = overlays.render_watermark("@c", tmp_path / "e", 5.0, opacity=1.0)
    # the alpha channel is what carries the opacity
    assert ImageStat.Stat(Image.open(faint.path).getchannel("A")).mean[0] < \
        ImageStat.Stat(Image.open(solid.path).getchannel("A")).mean[0]


def test_empty_watermark_renders_no_overlay(tmp_path):
    from app.pipeline import overlays

    assert overlays.render_watermark("", tmp_path, 5.0) is None
