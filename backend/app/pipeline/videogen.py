"""Entry point for the AI video generators used as the short's synthetic
background (background='ia_video').

Which provider runs is decided by generators/registry.py, from what each one
can do and what is actually configured — not from a hardcoded name. The chosen
provider is always logged before the first byte is generated, so the job log
says which model made the video.

Credentials and local server URLs come from connectors.credentials(<id>), so
the Accounts screen keeps beating .env.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings
from .generators import registry

# The name the rest of the app already catches. It stays the canonical alias of
# the registry's exception rather than a subclass, so `except
# VideoGenNotConfigured` keeps catching what is actually raised.
VideoGenNotConfigured = registry.GeneratorNotConfigured


def plan(provider: str = "", model: str = "") -> list[registry.Candidate]:
    """The ordered providers considered for the background, each with the
    reason it was kept or skipped. The UI and the log both read this, so what
    a user is told matches what generate_clip() will do."""
    return registry.candidates(registry.TEXT_TO_VIDEO,
                               prefer=provider or settings.videogen_provider,
                               model=model or settings.videogen_model)


def generate_clip(prompt: str, duration: float, out_path: Path,
                  aspect: str = "9:16", log=lambda m, *_: None,
                  provider: str = "", model: str = "") -> Path:
    """The single entry point called by the orchestrator when
    background='ia_video'.

    `provider` / `model` override VIDEOGEN_PROVIDER / VIDEOGEN_MODEL for one
    render — that is how "make this one with Seedance Pro" is expressed.

    Falling back to a second provider is deliberate for ProviderRefused only:
    no credit, a revoked key, a prompt the model will not draw. A timeout or a
    500 propagates, because swapping the model that makes someone's video over
    a transient blip is worse than failing — and even a legitimate fallback is
    logged as a warning naming both models.
    """
    candidates = plan(provider, model)
    usable = [c for c in candidates if c.eligible]
    if not usable:
        raise VideoGenNotConfigured(_no_provider_message(candidates))

    for index, candidate in enumerate(usable):
        named = f"{candidate.label}{f' / {candidate.model}' if candidate.model else ''}"
        log(f"ia_video: generating with {named} — {candidate.reason}")
        try:
            return registry.run_text_to_video(
                candidate.provider_id, prompt, duration, out_path,
                aspect=aspect, model=model or settings.videogen_model, log=log)
        except registry.ProviderRefused as exc:
            if index + 1 >= len(usable):
                raise VideoGenNotConfigured(
                    f"{named} refused the job ({exc}) and there is no other "
                    f"video generator configured."
                ) from exc
            nxt = usable[index + 1]
            log(f"{named} refused the job ({exc}). Falling back to {nxt.label} "
                f"— the clip will be made by a different model.", "warn")
    raise AssertionError("unreachable: the loop either returns or raises")


def _no_provider_message(candidates: list[registry.Candidate]) -> str:
    """Keeps the shape of the old message: what is missing, then what to do
    instead, so a missing provider stays a normal state and not a dead end."""
    blockers = [f"{c.label}: {c.reason}" for c in candidates
                if c.state in (registry.NOT_CONFIGURED, registry.UNREACHABLE)]
    detail = (" " + " ".join(blockers)) if blockers else ""
    return (
        "No AI video generator is available for the 'ia_video' background."
        + detail
        + " In the meantime use background='broll' / 'gradiente' / "
          "'video_fonte' / 'imagem_kenburns'."
    )
