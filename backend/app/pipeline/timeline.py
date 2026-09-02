"""Linha do tempo editável (EDL) — o modelo por trás do editor de vídeo.

Depois que o pipeline gera o short, ele descreve o resultado como uma lista de
decisões de edição: quais trechos de vídeo, em que ordem, com qual áudio e
quais legendas. O editor da interface manipula esse JSON, e `timeline_render`
recompila tudo com FFmpeg — sem passar de novo pelo LLM nem pelo TTS.

Isso é o que permite cortar, mover, esticar e reescrever texto depois de pronto.
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
    """Um trecho de vídeo posicionado na linha do tempo.

    `in_point`/`out_point` recortam o arquivo de origem; `start` é onde esse
    recorte aparece no short final. Separar os dois é o que permite mover um
    clipe sem reescolher o trecho, e vice-versa.
    """
    id: str
    source: str            # caminho relativo ao diretório do job
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
        """Ordena as trilhas e recalcula a duração total a partir do conteúdo.

        Chamado antes de renderizar: o editor pode deixar a timeline em qualquer
        estado, e a renderização precisa de algo coerente.
        """
        self.video.sort(key=lambda c: c.start)
        self.audio.sort(key=lambda c: c.start)
        self.captions.sort(key=lambda c: c.start)

        ends = ([c.end for c in self.video] + [c.end for c in self.audio]
                + [c.end for c in self.captions])
        self.duration = round(max(ends), 3) if ends else 0.0

        # legenda não pode passar do fim do vídeo — o QA reprova isso
        for cue in self.captions:
            cue.end = min(cue.end, self.duration)
        self.captions = [c for c in self.captions if c.end > c.start and c.text.strip()]
        return self


def build_from_job(job_dir: Path, words: list[dict], narration_duration: float,
                   background_parts: list[Path], caption_style: str,
                   caption_position: str, watermark: str,
                   music: Path | None = None, music_gain: float = 0.12,
                   ) -> Timeline:
    """Descreve o resultado do pipeline como timeline editável."""
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
    """Agrupa palavras cronometradas em falas curtas — a unidade que o usuário
    edita na interface (editar palavra por palavra seria inutilizável)."""
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
    """Volta de falas para palavras cronometradas, distribuindo o tempo da fala
    entre suas palavras — é o que o renderizador de legenda karaokê consome."""
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
