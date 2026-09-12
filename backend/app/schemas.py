from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Niche = Literal[
    "tecnologia", "ciberseguranca", "programacao", "cinema",
    "historia", "ciencia", "curiosidades", "negocios", "games", "saude", "politica", "generico",
]

# roteiro: the user pastes the final text, skipping LLM generation.
# github: repository URL — the pipeline clones it and narrates over the code.
# imagem: 1+ uploaded photos, narrated over with a Ken Burns effect.
SourceType = Literal["url", "tema", "texto", "video", "github", "imagem", "roteiro"]
ScrollStyle = Literal["nenhum", "texto", "pan", "codigo"]

# narrar_por_cima: uses the whole video/repo at the narration's normal pace.
# resumo: extracts only the highlight stretches and condenses them into the
# target duration — this is the "40min episode -> 60s short" mode.
# meu_video: a recording of your own. No script and no TTS — your audio stays,
# and the captions come from transcribing what you actually said. Handled by
# pipeline.reels instead of the generation pipeline.
# avatar: a talking presenter reads your script. The provider renders the
# picture AND the voice, so there is no TTS stage either; handled by
# pipeline.avatar, which writes the same artifacts as everything else.
EditMode = Literal["narrar_por_cima", "resumo", "meu_video", "avatar"]

# Which angle the script should take on the material. It changes what the
# SUBJECT is: "describe the scene" differs from "talk about the work, using
# the scene as support".
Angle = Literal[
    "auto", "critica", "contexto", "analise", "historia",
    "explicacao", "enredo", "curiosidade", "tutorial",
]

# Watermark placement. Bottom-centre is the default because it stays clear of
# the app's own interface on both TikTok and Shorts.
WatermarkPosition = Literal[
    "baixo_centro", "baixo_esquerda", "baixo_direita",
    "topo_centro", "topo_esquerda", "topo_direita",
]
WatermarkSize = Literal["pequeno", "medio", "grande"]

BackgroundMode = Literal[
    "auto", "broll", "gradiente", "video_fonte", "imagem_kenburns",
    "codigo_scroll", "ia_video", "ia_imagem", "site_scroll", "upload",
]


class JobInput(BaseModel):
    source_type: SourceType = "tema"
    source: str = Field(
        default="",
        description="URL, topic, raw text, GitHub repo URL, or empty when using attachments",
    )
    attachments: list[str] = Field(
        default=[],
        description="IDs returned by /api/uploads — images or a local video",
    )
    edit_mode: EditMode = "narrar_por_cima"
    angle: Angle = "auto"
    instruction: str = Field(
        default="",
        description="Free-form instruction of what to do in this video, in natural language",
    )
    niche: Niche = "generico"
    language: str = "pt-BR"
    voice_id: str | None = None
    duration: int = 45
    caption_style: Literal["karaoke", "bloco", "palavra"] = "karaoke"
    caption_position: Literal["centro", "baixo", "topo"] = "centro"
    scroll: ScrollStyle = "nenhum"
    background: BackgroundMode = "auto"
    background_query: str = ""
    # What to do with the space a landscape frame leaves in a 9:16 short:
    # "desfoque" keeps the whole picture over a blurred copy of itself,
    # "preencher" zooms until the frame is covered and cuts the sides. QA
    # switches to "preencher" by itself when the first framing still reads as
    # letterboxed.
    background_fill: Literal["desfoque", "preencher"] = "desfoque"
    music: bool = True
    # Only meaningful for edit_mode="meu_video": that mode has no TTS at all,
    # so this is the choice between keeping the voice on the recording and
    # delivering it silent with captions only (for a feed watched muted).
    keep_audio: bool = True
    music_track: str = ""          # id from /api/music; empty = first in the folder
    music_volume: float = 0.12
    caption_offset: float = 0.0    # fine sync adjustment, in seconds
    hook_hard: bool = True
    cta: str = "Segue pra mais."
    title_overlay: bool = True
    watermark: str = ""
    # Where the handle sits, how big and how visible. A single fixed style did
    # not survive every background — a light b-roll swallows a subtle mark.
    watermark_position: WatermarkPosition = "baixo_centro"
    watermark_size: WatermarkSize = "medio"
    watermark_opacity: float = 0.6
    variants: int = 1
    qa_autofix: bool = True
    qa_max_attempts: int = 3
    # Only meaningful for edit_mode="avatar": which presenter reads the script
    # and in which of the provider's voices. They are the provider's own ids,
    # not a row in the local `voices` table — that is what `voice_id` above is.
    avatar_id: str = ""
    avatar_voice_id: str = ""


class ScriptSegment(BaseModel):
    kind: Literal["hook", "corpo", "cta"] = "corpo"
    text: str
    broll_query: str = ""
    on_screen: str = ""


class ShortScript(BaseModel):
    title: str
    description: str
    hashtags: list[str] = []
    segments: list[ScriptSegment]
    estimated_seconds: int = 45


class WordTiming(BaseModel):
    word: str
    start: float
    end: float


class QAIssue(BaseModel):
    check: str
    severity: Literal["fatal", "erro", "aviso", "info"]
    message: str
    fix: str = ""


class QAReport(BaseModel):
    passed: bool
    score: int
    issues: list[QAIssue] = []
    metrics: dict = {}


class QAAttempt(BaseModel):
    """One round of the self-correction loop: what was tried and the outcome."""
    attempt: int
    action: str
    report: QAReport


class ScriptEdit(BaseModel):
    """Manual script edit + assembly tweaks, applied in a re-render without
    going through the LLM again."""
    segments: list[ScriptSegment] | None = None
    title: str | None = None
    voice_id: str | None = None
    caption_style: str | None = None
    caption_position: str | None = None
    caption_offset: float | None = None
    music: bool | None = None
    music_track: str | None = None
    music_volume: float | None = None
    watermark: str | None = None
    watermark_position: WatermarkPosition | None = None
    watermark_size: WatermarkSize | None = None
    watermark_opacity: float | None = None
    background: str | None = None
    background_query: str | None = None
    scroll: str | None = None


class PublishRequest(BaseModel):
    job_id: str
    account_id: str
    platform: Literal["youtube", "tiktok", "instagram", "linkedin"]
    title: str = ""
    description: str = ""
    tags: list[str] = []
    privacy: Literal["public", "private", "unlisted"] = "private"
    publish_at: str | None = None  # ISO8601; when absent, publishes now
