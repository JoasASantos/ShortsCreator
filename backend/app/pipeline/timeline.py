"""Editable timeline (EDL) — the model behind the video editor.

Once the pipeline has produced the short, it describes the result as a list of
edit decisions: which video excerpts, in what order, with which audio and which
subtitles. The editor in the UI manipulates that JSON, and `timeline_render`
recompiles everything with FFmpeg — without going back through the LLM or TTS.

That is what makes it possible to cut, move, stretch and rewrite text after the
fact.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class VideoClip:
    """A video excerpt placed on the timeline.

    `in_point`/`out_point` cut into the source file; `start` is where that cut
    appears in the final short. Keeping the two separate is what lets a clip be
    moved without re-picking the excerpt, and vice versa.
    """
    id: str
    source: str            # path relative to the job directory
    in_point: float
    out_point: float
    start: float
    kind: str = "video"    # video | image
    mute: bool = True

    @property
    def duration(self) -> float:
        return max(self.out_point - self.in_point, 0.0)

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class AudioClip:
    id: str
    source: str
    in_point: float
    out_point: float
    start: float
    gain: float = 1.0
    role: str = "narration"   # narration | music | sfx

    @property
    def duration(self) -> float:
        return max(self.out_point - self.in_point, 0.0)

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class CaptionCue:
    id: str
    text: str
    start: float
    end: float


@dataclass
class Timeline:
    duration: float
    video: list[VideoClip] = field(default_factory=list)
    audio: list[AudioClip] = field(default_factory=list)
    captions: list[CaptionCue] = field(default_factory=list)
    caption_style: str = "karaoke"
    caption_position: str = "centro"
    watermark: str = ""

    def to_dict(self) -> dict:
        return {
            "duration": round(self.duration, 3),
            "video": [asdict(c) for c in self.video],
            "audio": [asdict(c) for c in self.audio],
            "captions": [asdict(c) for c in self.captions],
            "caption_style": self.caption_style,
            "caption_position": self.caption_position,
            "watermark": self.watermark,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Timeline":
        return cls(
            duration=float(data.get("duration", 0.0)),
            video=[VideoClip(**c) for c in data.get("video", [])],
            audio=[AudioClip(**c) for c in data.get("audio", [])],
            captions=[CaptionCue(**c) for c in data.get("captions", [])],
            caption_style=data.get("caption_style", "karaoke"),
            caption_position=data.get("caption_position", "centro"),
            watermark=data.get("watermark", ""),
        )

    def normalize(self) -> "Timeline":
        """Sort the tracks and recompute the total duration from the content.

        Called before rendering: the editor may leave the timeline in any state
        at all, and the render needs something coherent.
        """
        self.video.sort(key=lambda c: c.start)
        self.audio.sort(key=lambda c: c.start)
        self.captions.sort(key=lambda c: c.start)

        ends = ([c.end for c in self.video] + [c.end for c in self.audio]
                + [c.end for c in self.captions])
        self.duration = round(max(ends), 3) if ends else 0.0

        # a subtitle must not run past the end of the video — QA flags that
        for cue in self.captions:
            cue.end = min(cue.end, self.duration)
        self.captions = [c for c in self.captions if c.end > c.start and c.text.strip()]
        return self


def build_from_job(job_dir: Path, words: list[dict], narration_duration: float,
                   background_parts: list[Path], caption_style: str,
                   caption_position: str, watermark: str,
                   music: Path | None = None, music_gain: float = 0.12,
                   ) -> Timeline:
    """Describe the pipeline's result as an editable timeline."""
    video: list[VideoClip] = []
    cursor = 0.0
    for part in background_parts:
        from . import render as render_mod
        length = render_mod.probe_duration(part)
        if length <= 0:
            continue
        video.append(VideoClip(
            id=_new_id("v"), source=part.name,
            in_point=0.0, out_point=round(length, 3), start=round(cursor, 3),
        ))
        cursor += length

    audio = [AudioClip(
        id=_new_id("a"), source="narration.mp3",
        in_point=0.0, out_point=round(narration_duration, 3),
        start=0.0, gain=1.0, role="narration",
    )]
    if music is not None:
        audio.append(AudioClip(
            id=_new_id("a"), source=music.name,
            in_point=0.0, out_point=round(cursor or narration_duration, 3),
            start=0.0, gain=music_gain, role="music",
        ))

    return Timeline(
        duration=round(max(cursor, narration_duration), 3),
        video=video, audio=audio,
        captions=captions_from_words(words),
        caption_style=caption_style, caption_position=caption_position,
        watermark=watermark,
    ).normalize()


def captions_from_words(words: list[dict]) -> list[CaptionCue]:
    """Group timed words into short cues — the unit the user edits in the UI
    (editing word by word would be unusable)."""
    from .captions import group_lines

    cues: list[CaptionCue] = []
    for line in group_lines(words):
        cues.append(CaptionCue(
            id=_new_id("c"),
            text=" ".join(w["word"] for w in line),
            start=round(line[0]["start"], 3),
            end=round(line[-1]["end"], 3),
        ))
    return cues


def words_from_captions(cues: list[CaptionCue]) -> list[dict]:
    """Go back from cues to timed words, spreading each cue's time across its
    words — this is what the karaoke subtitle renderer consumes."""
    from .tts import estimate_words

    words: list[dict] = []
    for cue in cues:
        span = max(cue.end - cue.start, 0.05)
        for word in estimate_words(cue.text, span):
            words.append({
                "word": word["word"],
                "start": round(cue.start + word["start"], 3),
                "end": round(cue.start + word["end"], 3),
            })
    return words


def load(job_dir: Path) -> Timeline | None:
    path = job_dir / "timeline.json"
    if not path.exists():
        return None
    return Timeline.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save(job_dir: Path, timeline: Timeline) -> Path:
    path = job_dir / "timeline.json"
    path.write_text(json.dumps(timeline.to_dict(), indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path
