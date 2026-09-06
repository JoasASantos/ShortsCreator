"""Rendering subtitles and text as transparent PNGs.

Homebrew's default FFmpeg build ships without libass/libfreetype, so the `ass`,
`subtitles` and `drawtext` filters do not exist. Rather than demanding a
recompile, we draw the images with Pillow and composite them with `overlay` —
which exists in every build. If FFmpeg does have libass, the pipeline uses the
`ass` filter directly (faster).
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import settings
from .captions import (ACTIVE_COLOR, POSITION_MARGIN_V, SAFE_TOP, SIDE_MARGIN,
                       group_lines)

W, H = settings.width, settings.height

WHITE = (255, 255, 255, 255)
ACTIVE = (255, 200, 0, 255)          # amber — same color as the ASS style
STROKE = (0, 0, 0, 255)

BOLD_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/ariblk.ttf",
]
MONO_FONTS = [
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "C:/Windows/Fonts/consola.ttf",
]


@dataclass
class Overlay:
    path: Path
    start: float
    end: float
    x: int
    y: int
    kind: str = "caption"   # caption | title | watermark | scroll


def find_font(mono: bool = False) -> str:
    local = sorted(settings.assets_dir.joinpath("fonts").glob("*.tt[fc]"))
    if local:
        return str(local[0])
    for candidate in (MONO_FONTS if mono else BOLD_FONTS):
        if Path(candidate).exists():
            return candidate
    raise RuntimeError(
        "No TrueType font found. Drop a .ttf into assets/fonts/."
    )


def _font(size: int, mono: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(find_font(mono), size)


def _measure(draw: ImageDraw.ImageDraw, text: str, font, stroke: int) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return box[2] - box[0], box[3] - box[1]


def render_captions(words: list[dict], out_dir: Path, style: str = "karaoke",
                    position: str = "centro", font_size: int | None = None,
                    fmt=None) -> list[Overlay]:
    """Generate one PNG per subtitle event, already placed inside the safe area.

    `fmt` is the frame being drawn into; unset means the vertical short, with
    the numbers this file used to hard-code. The PNG path is the fallback for
    FFmpeg builds without libass, so it has to place words exactly where the
    .ass path would.
    """
    from . import formats

    fmt = fmt or formats.VERTICAL
    W, H = fmt.width, fmt.height   # noqa: N806 — shadows the module globals on purpose
    SIDE = fmt.side_margin         # noqa: N806
    # The PNG glyphs render a touch heavier than libass at the same size.
    font_size = font_size or max(fmt.caption_font_size - 4, 20)
    out_dir.mkdir(parents=True, exist_ok=True)
    font = _font(font_size)
    stroke = max(6, font_size // 14)
    margin_v = fmt.caption_margins.get(position, fmt.caption_margins["centro"])
    max_width = W - 2 * SIDE

    scratch = Image.new("RGBA", (10, 10))
    ruler = ImageDraw.Draw(scratch)

    overlays: list[Overlay] = []
    index = 0

    if style == "palavra":
        events = [([word], i) for i, word in enumerate(words)]
        groups = [[w] for w in words]
    else:
        groups = group_lines(words)

    for line in groups:
        tokens = [w["word"].upper() for w in line]
        space_w = _measure(ruler, " ", font, stroke)[0]
        widths = [_measure(ruler, t, font, stroke)[0] for t in tokens]

        # wrap into up to two visual rows if it overflows the usable width
        rows: list[list[int]] = [[]]
        row_width = 0
        for i, width in enumerate(widths):
            add = width + (space_w if rows[-1] else 0)
            if rows[-1] and row_width + add > max_width:
                rows.append([i])
                row_width = width
            else:
                rows[-1].append(i)
                row_width += add

        line_height = _measure(ruler, "ÁQg", font, stroke)[1] + int(font_size * 0.30)
        block_h = line_height * len(rows)
        block_w = max_width

        actives = range(len(line)) if style == "karaoke" else [None]
        for active in actives:
            if active is None:
                start, end = line[0]["start"], line[-1]["end"]
            else:
                start = line[active]["start"]
                end = (line[active + 1]["start"] if active + 1 < len(line)
                       else line[active]["end"])
            if end - start < 0.03:
                continue

            image = Image.new("RGBA", (block_w, block_h + stroke * 2), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            for row_i, row in enumerate(rows):
                row_w = sum(widths[i] for i in row) + space_w * (len(row) - 1)
                cursor = (block_w - row_w) // 2
                y = stroke + row_i * line_height
                for i in row:
                    color = ACTIVE if (active is not None and i == active) else WHITE
                    draw.text((cursor, y), tokens[i], font=font, fill=color,
                              stroke_width=stroke, stroke_fill=STROKE)
                    cursor += widths[i] + space_w

            path = out_dir / f"cap_{index:04d}.png"
            image.save(path)
            index += 1
            overlays.append(Overlay(path, start, end, SIDE,
                                    H - margin_v - block_h, kind="caption"))

    return overlays


def render_title(text: str, out_dir: Path, duration: float = 3.2,
                 font_size: int = 56, fmt=None) -> Overlay | None:
    if not text:
        return None
    from . import formats

    fmt = fmt or formats.VERTICAL
    W = fmt.width   # noqa: N806 — shadows the module global on purpose
    out_dir.mkdir(parents=True, exist_ok=True)
    font = _font(font_size)
    stroke = 5
    max_width = W - 160
    scratch = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    chars = max(int(max_width / (font_size * 0.58)), 12)
    lines = textwrap.wrap(text[:90], width=chars)[:2]
    line_h = _measure(scratch, "ÁQg", font, stroke)[1] + 14
    image = Image.new("RGBA", (max_width, line_h * len(lines) + 20), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        width = _measure(scratch, line, font, stroke)[0]
        draw.text(((max_width - width) // 2, i * line_h), line, font=font,
                  fill=WHITE, stroke_width=stroke, stroke_fill=STROKE)

    path = out_dir / "title.png"
    image.save(path)
    return Overlay(path, 0.0, duration, 80, fmt.safe_top - 10, kind="title")


# Font size per watermark size setting.
WATERMARK_SIZES = {"pequeno": 26, "medio": 34, "grande": 48}

# Side inset for the corner placements.
WATERMARK_SIDE_INSET = 48


def render_watermark(text: str, out_dir: Path, duration: float,
                     position: str = "baixo_centro", size: str = "medio",
                     opacity: float = 0.6, fmt=None) -> Overlay | None:
    """The channel handle burned over the video.

    Position, size and opacity are settings because one fixed style does not
    survive every background: a subtle mark disappears over light b-roll, and
    a bottom-centre mark collides with on-screen text in some layouts.
    """
    if not text:
        return None
    from . import formats

    fmt = fmt or formats.VERTICAL
    out_dir.mkdir(parents=True, exist_ok=True)
    font_size = WATERMARK_SIZES.get(size, WATERMARK_SIZES["medio"])
    font = _font(font_size)
    scratch = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    width, height = _measure(scratch, text, font, 3)

    alpha = max(0, min(int(round(opacity * 255)), 255))
    image = Image.new("RGBA", (width + 12, height + 12), (0, 0, 0, 0))
    ImageDraw.Draw(image).text(
        (6, 4), text, font=font, fill=(255, 255, 255, alpha),
        # the outline keeps it readable over a busy frame; it fades with the text
        stroke_width=3, stroke_fill=(0, 0, 0, min(alpha + 30, 255)))
    path = out_dir / "watermark.png"
    image.save(path)

    x, y = _watermark_xy(position, width, height, fmt)
    return Overlay(path, 0.0, duration, x, y, kind="watermark")


def _watermark_xy(position: str, width: int, height: int,
                  fmt=None) -> tuple[int, int]:
    """Both vertical anchors stay clear of the platform's own interface: the
    bottom band it covers, and the top row of buttons."""
    from . import formats

    fmt = fmt or formats.VERTICAL
    W, H = fmt.width, fmt.height   # noqa: N806 — the frame is the caller's
    bottom = H - fmt.caption_margins["baixo"] - height - 30
    top = fmt.safe_top - height // 2
    left = WATERMARK_SIDE_INSET
    right = W - width - WATERMARK_SIDE_INSET
    center = (W - width) // 2

    return {
        "baixo_centro": (center, bottom),
        "baixo_esquerda": (left, bottom),
        "baixo_direita": (right, bottom),
        "topo_centro": (center, top),
        "topo_esquerda": (left, top),
        "topo_direita": (right, top),
    }.get(position, (center, bottom))


def render_scroll_panel(text: str, out_dir: Path, mono: bool = False,
                        font_size: int = 38) -> Path:
    """A tall text panel for the vertical scroll effect."""
    out_dir.mkdir(parents=True, exist_ok=True)
    font = _font(font_size, mono=mono)
    panel_w = W - 160
    chars = max(int(panel_w / (font_size * (0.62 if mono else 0.52))), 20)

    if mono:
        # Code has to keep its line breaks and indentation — collapsing it all
        # into a single paragraph destroys readability and doesn't look like
        # code on screen.
        lines: list[str] = []
        for raw in text.splitlines():
            raw = raw.rstrip().replace("\t", "    ")
            if not raw:
                lines.append("")
                continue
            indent = len(raw) - len(raw.lstrip())
            wrapped = textwrap.wrap(
                raw, width=chars, subsequent_indent=" " * (indent + 2),
                drop_whitespace=False, replace_whitespace=False,
            ) or [""]
            lines.extend(wrapped)
            if len(lines) >= 200:
                break
        lines = lines[:200]
    else:
        lines = textwrap.wrap(" ".join(text.split()), width=chars)[:200]

    line_h = int(font_size * 1.45)
    height = max(line_h * len(lines) + 60, H)

    image = Image.new("RGBA", (panel_w, height), (0, 0, 0, 130))
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((26, 30 + i * line_h), line, font=font,
                  fill=(235, 240, 245, 225), stroke_width=2, stroke_fill=(0, 0, 0, 200))

    path = out_dir / "scroll_panel.png"
    image.save(path)
    return path
