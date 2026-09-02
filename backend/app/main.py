from __future__ import annotations

import shutil

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db, worker
from .config import settings
from .routers import clips, connectors, jobs, music, publish, uploads, voices

app = FastAPI(title="ShortsCreator API", version="1.0.0",
              description="Geração automática de Shorts verticais 9:16 com QA.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(voices.router)
app.include_router(uploads.router)
app.include_router(clips.router)
app.include_router(music.router)
app.include_router(publish.router)
app.include_router(connectors.router)


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    worker.start()


def _llm_ready() -> bool:
    """Providers por CLI dependem do binário logado, não de chave de API."""
    provider = settings.llm_provider
    if provider == "claude_cli":
        return bool(shutil.which(settings.claude_cli_bin))
    if provider == "codex_cli":
        return bool(shutil.which(settings.codex_cli_bin))
    if provider == "ollama":
        return True
    return bool(settings.anthropic_api_key or settings.openai_api_key)


def _llm_auth_mode() -> str:
    if settings.llm_provider in ("claude_cli", "codex_cli"):
        return "assinatura"
    if settings.llm_provider == "ollama":
        return "local"
    return "chave de API"


def _has_ytdlp() -> bool:
    if shutil.which("yt-dlp"):
        return True
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ytdlp": _has_ytdlp(),
        "llm_provider": settings.llm_provider,
        "llm_key_set": _llm_ready(),
        "llm_auth": _llm_auth_mode(),
        "tts_provider": settings.tts_provider,
        "broll_ready": bool(settings.pexels_api_key or settings.pixabay_api_key),
        "queue": worker.queue_size(),
        "format": f"{settings.width}x{settings.height} @ {settings.fps}fps (9:16)",
    }


@app.get("/api/config")
def config() -> dict:
    return {
        "width": settings.width,
        "height": settings.height,
        "fps": settings.fps,
        "min_seconds": settings.min_short_seconds,
        "max_seconds": settings.max_short_seconds,
        "niches": ["tecnologia", "ciberseguranca", "programacao", "cinema",
                   "historia", "ciencia", "curiosidades", "negocios", "generico"],
    }
