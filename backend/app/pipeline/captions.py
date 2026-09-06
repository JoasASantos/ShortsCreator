"""ASS subtitles with per-word highlighting (karaoke), inside the 9:16 safe area."""
from __future__ import annotations

import re
from pathlib import Path

from ..config import settings

# ASS colors are &HAABBGGRR (alpha, blue, green, red)
BASE_COLOR = "&H00FFFFFF"       # white
ACTIVE_COLOR = "&H0000E5FF"     # yellow/amber
OUTLINE_COLOR = "&H00000000"    # black

# Vertical safe area: the TikTok/Shorts UI covers ~320px at the bottom and ~180px at the top.
SAFE_BOTTOM = 340
SAFE_TOP = 200
SIDE_MARGIN = 110

POSITION_MARGIN_V = {
    "baixo": SAFE_BOTTOM,
    "centro": 780,
    "topo": 1280,
}


def _ts(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def group_lines(words: list[dict], max_words: int = 4,
                max_seconds: float = 2.4, max_chars: int = 26) -> list[list[dict]]:
    """Group words into short lines that stay readable on a vertical screen."""
    lines: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        candidate = current + [word]
        chars = sum(len(w["word"]) + 1 for w in candidate)
        span = candidate[-1]["end"] - candidate[0]["start"]
        too_long = (len(candidate) > max_words or chars > max_chars
                    or span > max_seconds)
        gap = word["start"] - current[-1]["end"] if current else 0.0
        if current and (too_long or gap > 0.55):
            lines.append(current)
            current = [word]
        else:
            current = candidate
        if re.search(r"[.!?]$", word["word"]) and len(current) >= 2:
            lines.append(current)
            current = []
    if current:
        lines.append(current)
    return lines


# ASS alignment codes for each watermark placement (numpad layout).
WATERMARK_ALIGN = {
    "baixo_centro": 2, "baixo_esquerda": 1, "baixo_direita": 3,
    "topo_centro": 8, "topo_esquerda": 7, "topo_direita": 9,
}

WATERMARK_FONT_SIZES = {"pequeno": 30, "medio": 38, "grande": 52}


def _watermark_colour(opacity: float) -> str:
    """ASS colours are &HAABBGGRR — the alpha byte is inverted, so 0x00 is
    fully opaque and 0xFF invisible."""
    alpha = max(0, min(int(round((1 - opacity) * 255)), 255))
    return f"&H{alpha:02X}FFFFFF"


def build_ass(words: list[dict], out_path: Path, style: str = "karaoke",
              position: str = "centro", font: str = "Arial Black",
              font_size: int | None = None, title: str = "",
              watermark: str = "", watermark_position: str = "baixo_centro",
              watermark_size: str = "medio",
              watermark_opacity: float = 0.6, fmt=None) -> Path:
    """Write the subtitle file for one frame shape.

    `fmt` decides the canvas (PlayRes), where the safe bands are and how big a
    word is. Left unset it is the vertical short — the numbers this file used
    to hard-code — so every existing caller keeps producing the same file.
    """
    from . import formats

    fmt = fmt or formats.VERTICAL
    font_size = font_size or fmt.caption_font_size
    side_margin = fmt.side_margin
    safe_top = fmt.safe_top
    margin_v = fmt.caption_margins.get(position, fmt.caption_margins["centro"])
    mark_align = WATERMARK_ALIGN.get(watermark_position, 2)
    mark_size = WATERMARK_FONT_SIZES.get(watermark_size, 38)
    mark_colour = _watermark_colour(watermark_opacity)
    # both vertical anchors clear the platform's own interface
    mark_margin_v = safe_top if mark_align >= 7 else 120

    header = f"""[Script Info]
Title: ShortsCreator
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {fmt.width}
PlayResY: {fmt.height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Legenda,{font},{font_size},{BASE_COLOR},{ACTIVE_COLOR},{OUTLINE_COLOR},&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,{side_margin},{side_margin},{margin_v},1
Style: Titulo,{font},58,{BASE_COLOR},{BASE_COLOR},{OUTLINE_COLOR},&H80000000,-1,0,0,0,100,100,0,0,1,5,2,8,80,80,{safe_top},1
Style: Marca,{font},{mark_size},{mark_colour},{mark_colour},{OUTLINE_COLOR},&H00000000,0,0,0,0,100,100,0,0,1,3,0,{mark_align},60,60,{mark_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []
    total = words[-1]["end"] if words else 0.0

    if title:
        events.append(
            f"Dialogue: 0,{_ts(0)},{_ts(min(3.2, total))},Titulo,,0,0,0,,"
            f"{{\\fad(250,250)}}{_escape(title[:60])}"
        )
    if watermark:
        events.append(
            f"Dialogue: 0,{_ts(0)},{_ts(total)},Marca,,0,0,0,,{_escape(watermark[:40])}"
        )

    if style == "palavra":
        for word in words:
            events.append(
                f"Dialogue: 1,{_ts(word['start'])},{_ts(word['end'])},Legenda,,0,0,0,,"
                f"{{\\fad(60,60)\\fscx105\\fscy105}}{_escape(word['word']).upper()}"
            )
    else:
        for line in group_lines(words, max_words=fmt.caption_max_words,
                                max_chars=fmt.caption_max_chars):
            if style == "bloco":
                text = " ".join(_escape(w["word"]) for w in line).upper()
                events.append(
                    f"Dialogue: 1,{_ts(line[0]['start'])},{_ts(line[-1]['end'])},Legenda,,0,0,0,,"
                    f"{{\\fad(80,80)}}{text}"
                )
                continue
            # karaoke: one event per active word, whole line always visible
            for index, word in enumerate(line):
                start = word["start"]
                end = line[index + 1]["start"] if index + 1 < len(line) else word["end"]
                parts = []
                for j, w in enumerate(line):
                    token = _escape(w["word"]).upper()
                    if j == index:
                        parts.append(f"{{\\c{ACTIVE_COLOR}\\fscx108\\fscy108}}{token}"
                                     f"{{\\c{BASE_COLOR}\\fscx100\\fscy100}}")
                    else:
                        parts.append(token)
                events.append(
                    f"Dialogue: 1,{_ts(start)},{_ts(end)},Legenda,,0,0,0,,"
                    + " ".join(parts)
                )

    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return out_path


def build_srt(words: list[dict], out_path: Path) -> Path:
    lines = group_lines(words, max_words=7, max_seconds=3.5, max_chars=42)
    blocks: list[str] = []
    for i, line in enumerate(lines, 1):
        start = _srt_ts(line[0]["start"])
        end = _srt_ts(line[-1]["end"])
        text = " ".join(w["word"] for w in line)
        blocks.append(f"{i}\n{start} --> {end}\n{text}\n")
    out_path.write_text("\n".join(blocks), encoding="utf-8")
    return out_path


def _srt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
