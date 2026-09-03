"""Short cover: the richest frame of the video + a large title + the niche band.

The default thumbnail (thumb.jpg) is a fixed frame from the very start — almost
always the background before anything has happened. Here we sample frames across
the video, measure detail (luminance standard deviation, a cheap proxy for
"there is something in the picture") and pick the best one past the first 300 ms.
On top of it goes a dark gradient at the base and the title wrapped into at most
3 lines, styled like the subtitles so the visual identity matches.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageStat

from ..config import settings
from . import broll
from .captions import SAFE_BOTTOM
from .overlays import _font

W, H = settings.width, settings.height
SAMPLE_EVERY = 0.7            # seconds between candidate frames
SKIP_HEAD = 0.3               # the opening is usually a fade-in or a bare background
TITLE_BAND_TOP = int(H * 0.56)
# The title respects the SAME safe area as the subtitles: the bottom 340 px
# disappear behind the app UI, and a cropped cover is worse than a dull one.
TITLE_BOTTOM = H - SAFE_BOTTOM - 40


def build(video: Path, title: str, niche: str, out: Path, duration: float,
          work_dir: Path | None = None) -> tuple[Path, float]:
    """Generate cover.jpg. Returns (path, timestamp of the chosen frame in seconds)."""
    work = (work_dir or out.parent) / "cover_frames"
    work.mkdir(parents=True, exist_ok=True)

    at = pick_frame_time(video, duration, work)
    frame = work / "chosen.jpg"
    _extract(video, at, frame)

    image = Image.open(frame).convert("RGB").resize((W, H))
    _compose(image, title, niche).save(out, "JPEG", quality=92, optimize=True)
    return out, at


def pick_frame_time(video: Path, duration: float, work: Path) -> float:
    """Frame with the most visual detail between SKIP_HEAD and 85% of the video."""
    end = max(duration * 0.85, SKIP_HEAD + 0.5)
    best_at, best_score = SKIP_HEAD, -1.0
    t = SKIP_HEAD
    index = 0
    while t < end and index < 60:
        sample = work / f"s_{index:02d}.jpg"
        try:
            _extract(video, t, sample, small=True)
            gray = Image.open(sample).convert("L")
            stat = ImageStat.Stat(gray)
            std = stat.stddev[0]
            mean = stat.mean[0]
            # penalize very dark or blown-out frames — they only look fine in the feed
            penalty = abs(mean - 118) / 118
            score = std * (1 - 0.5 * penalty)
            if score > best_score:
                best_score, best_at = score, t
        except Exception:  # noqa: BLE001 — a bad frame is simply skipped
            pass
        t += SAMPLE_EVERY
        index += 1
    return round(best_at, 2)


def _extract(video: Path, at: float, out: Path, small: bool = False) -> None:
    vf = "scale=270:480" if small else f"scale={W}:{H}"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
         "-vf", vf, "-q:v", "3", str(out)],
        check=True, capture_output=True,
    )


def _compose(image: Image.Image, title: str, niche: str) -> Image.Image:
    c0, _ = broll.palette(niche)
    accent = _hex(c0)

    # blur + darkening over the title band: without it the text competes for
    # attention with the background and vanishes on any bright image
    # 1. Where the text will sit. This has to come BEFORE the darkening:
    # otherwise the gradient has no idea how high to reach and a 3-line title is
    # born in the bright part of the band, unreadable over a white background.
    draw = ImageDraw.Draw(image)
    clean = " ".join(title.split())
    size = 132
    while size > 72:
        font = _font(size)
        lines = textwrap.wrap(clean.upper(), width=max(8, int(W * 0.86 / (size * 0.56))))
        if len(lines) <= 3 and all(
                draw.textlength(line, font=font) <= W * 0.88 for line in lines):
            break
        size -= 8
    else:
        font = _font(size)
        lines = textwrap.wrap(clean.upper(), width=18)[:3]

    line_h = int(size * 1.08)
    total_h = line_h * len(lines)
    # anchored to the usable BOTTOM edge: with 1, 2 or 3 lines the text never
    # enters the app UI band
    top = TITLE_BOTTOM - total_h

    # 2. Blur and darkening: opaque from where the text starts downwards, fading
    # out upwards over 220 px.
    fade_start = max(min(top - 220, TITLE_BAND_TOP), 0)
    band_h = H - fade_start
    band = image.crop((0, fade_start, W, H)).filter(ImageFilter.GaussianBlur(10))
    image.paste(band, (0, fade_start))

    solid_from = top - fade_start
    gradient = Image.new("L", (1, band_h))
    for y in range(band_h):
        ratio = 1.0 if solid_from <= 0 else min(y / solid_from, 1.0)
        gradient.putpixel((0, y), int(225 * ratio ** 1.4))
    shade = Image.new("RGB", (W, band_h), (6, 8, 10))
    image.paste(shade, (0, fade_start), gradient.resize((W, band_h)))

    # 3. Title, with the niche color band to the left of the block
    x_left = int(W * 0.06)
    draw = ImageDraw.Draw(image)
    draw.rectangle([x_left - 34, top + 8, x_left - 18, top + total_h - 8], fill=accent)

    y = top
    for line in lines:
        draw.text((x_left, y), line, font=font, fill=(255, 255, 255),
                  stroke_width=max(4, size // 22), stroke_fill=(0, 0, 0))
        y += line_h
    return image


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) != 6:
        return (255, 196, 0)
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
