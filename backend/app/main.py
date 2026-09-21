from __future__ import annotations

import shutil

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db, worker
from .config import settings
from .pipeline import doctor
from .routers import (avatar, clips, connectors, films, generators, jobs,
                      models as models_router,
                      livecuts, longform, metrics, music, outputs, publish, reels,
                      trends, uploads, voices, services, recycle, moldes)

app = FastAPI(title="ShortsCreator API", version="1.0.0",
              description="Automated generation of 9:16 vertical Shorts with QA.")

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
app.include_router(livecuts.router)
app.include_router(reels.router)
app.include_router(avatar.router)
app.include_router(films.router)
app.include_router(longform.router)
app.include_router(music.router)
app.include_router(publish.router)
app.include_router(connectors.router)
app.include_router(generators.router)
app.include_router(outputs.router)
app.include_router(metrics.router)
app.include_router(trends.router)
app.include_router(models_router.router)
app.include_router(services.router)
app.include_router(recycle.router)
app.include_router(moldes.router)


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    worker.start()


# The doctor owns this logic: it reports the same thing under the `llm` check,
# and two copies would eventually disagree about what "ready" means.
_provider_ready = doctor.provider_ready
_llm_ready = doctor.llm_ready


def _llm_auth_mode() -> str:
    provider = settings.llm_provider
    if provider == "chain":
        providers = {p for p, _ in settings.llm_chain}
        if providers <= {"claude_cli", "codex_cli"}:
            return "assinatura"
        return "misto"
    if provider in ("claude_cli", "codex_cli"):
        return "assinatura"
    if provider == "ollama":
        return "local"
    return "chave de API"


def _llm_chain_status() -> list[dict]:
    """Every link in the chain, in order, with why it cannot be used.

    `ready` alone was not enough to act on: a link can have its CLI installed
    and still be unusable because the account is not enrolled in that model.
    The reason is what turns "the primary is not answering" into something the
    user can fix.
    """
    if settings.llm_provider != "chain":
        return []

    from .pipeline import llm as llm_mod

    chain = []
    # The chain in force, not the one .env described at boot: the model can be
    # changed from the interface, and the health readout has to agree with what
    # would actually run.
    for provider, model in llm_mod.active_chain():
        reason = llm_mod._unavailable_reason(provider, model)  # noqa: SLF001
        chain.append({
            # empty model means "whatever the CLI session defaults to"; the UI
            # renders that in the chosen language
            "provider": provider, "model": model,
            "ready": not reason,
            "reason": reason,
        })
    return chain


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
        "llm_chain": _llm_chain_status(),
        "tts_provider": settings.tts_provider,
        "broll_ready": bool(settings.pexels_api_key or settings.pixabay_api_key),
        "queue": worker.queue_size(),
        "format": f"{settings.width}x{settings.height} @ {settings.fps}fps (9:16)",
        # Only the verdict here — this route is polled every few seconds, and
        # the per-check detail (versions, install commands) lives at
        # /api/system/requirements.
        "requirements": doctor.summary(),
    }


@app.get("/api/system/requirements")
def requirements() -> dict:
    """The full doctor report: what is installed, what is missing, what each
    missing piece would unlock and the command to install it on this OS."""
    return doctor.check()


@app.get("/api/config")
def config() -> dict:
    return {
        "width": settings.width,
        "height": settings.height,
        "fps": settings.fps,
        "min_seconds": settings.min_short_seconds,
        "max_seconds": settings.max_short_seconds,
        "niches": ["tecnologia", "ciberseguranca", "programacao", "cinema",
                   "historia", "ciencia", "curiosidades", "negocios",
                   "games", "saude", "politica", "generico"],
    }
