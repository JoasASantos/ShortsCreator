"""Output formats — the frame everything is composed into.

For a long time there was one: 1080x1920, the vertical short, and its numbers
were module constants in four different files. A documentary is 16:9, and the
moment a second format exists every one of those constants is a bug waiting to
happen — a caption placed for a phone screen sits in the wrong third of a
television frame, and QA rejects a correct 1920x1080 file for not being a
short.

So the frame is a value, carried by the timeline, and everything that draws
into it or judges it asks the value. The vertical short stays the default, so
nothing that exists today changes behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Format:
    name: str
    width: int
    height: int
    # Bands the platform's own interface covers. Nothing important is drawn
    # there: on a phone the bottom third is buttons and a caption; on a
    # television frame there is no interface, only a margin for taste.
    safe_top: int
    safe_bottom: int
    side_margin: int
    # Captions. Word size follows the frame — 92px is readable on a phone held
    # at arm's length and a billboard on a 16:9 frame watched from a desk.
    caption_font_size: int
    caption_margins: dict[str, int]      # position -> distance from the bottom
    # How much text fits on one caption line before it wraps. A vertical frame
    # is narrow; a horizontal one takes a whole sentence.
    caption_max_words: int
    caption_max_chars: int
    # The QA duration window. Shorts have a ceiling because the feeds cut them
    # off; a documentary has none, only a target.
    min_seconds: int
    max_seconds: int | None
    aspect: str = field(default="9:16")

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def ratio(self) -> float:
        return self.width / self.height

    @property
    def landscape(self) -> bool:
        return self.width > self.height


# The vertical short. Every number here is what the four modules used to carry
# as their own constant, so this is behaviour-preserving by construction.
VERTICAL = Format(
    name="vertical", width=1080, height=1920,
    safe_top=200, safe_bottom=340, side_margin=110,
    caption_font_size=92,
    caption_margins={"baixo": 340, "centro": 780, "topo": 1280},
    caption_max_words=4, caption_max_chars=26,
    min_seconds=15, max_seconds=90,
    aspect="9:16",
)

# The horizontal frame — documentaries, films, anything watched on a screen
# rather than scrolled past on a phone. No platform interface to dodge, so the
# safe bands are only margins; captions sit low like subtitles, in a size that
# reads as subtitles and not as a hook.
HORIZONTAL = Format(
    name="horizontal", width=1920, height=1080,
    safe_top=80, safe_bottom=110, side_margin=160,
    caption_font_size=54,
    caption_margins={"baixo": 110, "centro": 420, "topo": 880},
    caption_max_words=9, caption_max_chars=52,
    min_seconds=60, max_seconds=None,
    aspect="16:9",
)

SQUARE = Format(
    name="quadrado", width=1080, height=1080,
    safe_top=120, safe_bottom=200, side_margin=110,
    caption_font_size=72,
    caption_margins={"baixo": 200, "centro": 440, "topo": 780},
    caption_max_words=6, caption_max_chars=34,
    min_seconds=15, max_seconds=90,
    aspect="1:1",
)

FORMATS: dict[str, Format] = {f.name: f for f in (VERTICAL, HORIZONTAL, SQUARE)}

# Aspect strings the rest of the code and the UI already speak.
BY_ASPECT: dict[str, Format] = {f.aspect: f for f in FORMATS.values()}


def get(name_or_aspect: str | None) -> Format:
    """Resolve "horizontal", "16:9" or None (the default) to a Format.

    Unknown names fall back to the vertical short rather than raising: a
    timeline written by an older version carries no format at all, and it has
    to keep rendering exactly as it did.
    """
    if not name_or_aspect:
        return VERTICAL
    key = name_or_aspect.strip().lower()
    return FORMATS.get(key) or BY_ASPECT.get(key) or VERTICAL
