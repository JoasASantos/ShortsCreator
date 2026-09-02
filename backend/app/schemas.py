from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Niche = Literal[
    "tecnologia", "ciberseguranca", "programacao", "cinema",
    "historia", "ciencia", "curiosidades", "negocios", "generico",
]

# roteiro: usuário cola o texto final, pula geração por LLM.
# github: URL de repositório — o pipeline clona e narra sobre o código.
# imagem: 1+ fotos enviadas, narração por cima com efeito Ken Burns.
SourceType = Literal["url", "tema", "texto", "video", "github", "imagem", "roteiro"]
ScrollStyle = Literal["nenhum", "texto", "pan", "codigo"]

# narrar_por_cima: usa o vídeo/repo inteiro no ritmo normal da narração.
# resumo: extrai só os trechos de destaque e condensa no duration alvo —
# é o modo "episódio de 40min -> short de 60s".
EditMode = Literal["narrar_por_cima", "resumo"]

# Que ângulo o roteiro deve tomar sobre o material. Muda o que é o ASSUNTO:
# "descreva a cena" é diferente de "fale da obra usando a cena como apoio".
Angle = Literal[
    "auto", "critica", "contexto", "analise", "historia",
    "explicacao", "enredo", "curiosidade", "tutorial",
]

BackgroundMode = Literal[
    "auto", "broll", "gradiente", "video_fonte", "imagem_kenburns",
    "codigo_scroll", "ia_video", "upload",
]


class JobInput(BaseModel):
    source_type: SourceType = "tema"
    source: str = Field(
        default="",
        description="URL, tema, texto bruto, URL de repo GitHub ou vazio quando usa attachments",
    )
    attachments: list[str] = Field(
        default=[],
        description="IDs retornados por /api/uploads — imagens ou vídeo local",
    )
    edit_mode: EditMode = "narrar_por_cima"
    angle: Angle = "auto"
    instruction: str = Field(
        default="",
        description="Instrução livre do que fazer neste vídeo, em linguagem natural",
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
    music: bool = True
    music_track: str = ""          # id de /api/music; vazio = primeira da pasta
    music_volume: float = 0.12
    caption_offset: float = 0.0    # ajuste fino de sincronia, em segundos
    hook_hard: bool = True
    cta: str = "Segue pra mais."
    title_overlay: bool = True
    watermark: str = ""
    variants: int = 1
    qa_autofix: bool = True
    qa_max_attempts: int = 3


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
    """Um round do loop de autoajuste: o que foi tentado e o resultado."""
    attempt: int
    action: str
    report: QAReport


class ScriptEdit(BaseModel):
    """Edição manual do roteiro + ajustes de montagem, aplicados numa
    re-renderização sem passar de novo pelo LLM."""
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
    background: str | None = None
    background_query: str | None = None
    scroll: str | None = None


class PublishRequest(BaseModel):
    job_id: str
    account_id: str
    platform: Literal["youtube", "tiktok"]
    title: str = ""
    description: str = ""
    tags: list[str] = []
    privacy: Literal["public", "private", "unlisted"] = "private"
    publish_at: str | None = None  # ISO8601; se ausente publica agora
