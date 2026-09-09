"""Entry point for the AI still generators — the image half of videogen.py.

Same contract, same reasons: which provider runs is decided by
generators/registry.py from what each one can do and what is configured, the
choice is logged before a byte is generated, and credentials come from
connectors.credentials(<id>) so the Accounts screen keeps beating .env.

Where the stills go:
  - background='ia_imagem' — one image per narration segment, then Ken Burns.
  - the 'auto' background, when there is no footage at all: generated stills
    beat the gradient, which is what 'nothing to show' used to look like.
  - a long-form stock shot with no stock bank configured: an image is closer
    to the intended shot than a title card with the query written on it.

Failing here is never fatal to a render. Every caller has a fallback that
already existed, so `generate_image` raising means "use it", not "stop".
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings
from .generators import registry

ImageGenNotConfigured = registry.GeneratorNotConfigured


def plan(provider: str = "", model: str = "",
         reference: Path | None = None) -> list[registry.Candidate]:
    """The ordered providers considered for a still, each with the reason it
    was kept or skipped. Editing a reference is a different capability from
    drawing from nothing — a provider that only does text_to_image must not be
    offered for an edit."""
    capability = registry.IMAGE_TO_IMAGE if reference else registry.TEXT_TO_IMAGE
    return registry.candidates(capability,
                               prefer=provider or settings.imagegen_provider,
                               model=model or settings.imagegen_model)


def providers_ready(reference: Path | None = None) -> bool:
    """Is there any still generator that would actually run right now?

    The callers ask before committing to a path — the 'auto' background picks
    between generated stills and a gradient, and asking afterwards would mean
    finding out halfway through a render.
    """
    return any(c.eligible for c in plan(reference=reference))


def why_not(reference: Path | None = None) -> str:
    """One line naming what is missing, for the log line the user reads."""
    return _no_provider_message(plan(reference=reference))


def generate_image(prompt: str, out_path: Path, aspect: str = "9:16",
                   log=lambda m, *_: None, provider: str = "", model: str = "",
                   reference: Path | None = None) -> Path:
    """One still, written to `out_path`.

    Falling back to a second provider is deliberate for ProviderRefused only —
    no credit, a revoked key, a prompt the model will not draw. A timeout or a
    500 propagates, because swapping the model mid-render over a transient
    blip is worse than failing; a legitimate fallback is logged as a warning
    naming both.
    """
    candidates = plan(provider, model, reference)
    usable = [c for c in candidates if c.eligible]
    if not usable:
        raise ImageGenNotConfigured(_no_provider_message(candidates))

    for index, candidate in enumerate(usable):
        named = f"{candidate.label}{f' / {candidate.model}' if candidate.model else ''}"
        log(f"ia_imagem: generating with {named} — {candidate.reason}")
        try:
            return registry.run_text_to_image(
                candidate.provider_id, prompt, out_path, aspect=aspect,
                model=model or settings.imagegen_model, reference=reference,
                log=log)
        except registry.ProviderRefused as exc:
            if index + 1 >= len(usable):
                raise ImageGenNotConfigured(
                    f"{named} refused the image ({exc}) and there is no other "
                    f"image generator configured."
                ) from exc
            nxt = usable[index + 1]
            log(f"{named} refused the image ({exc}). Falling back to "
                f"{nxt.label} — the still will be made by a different model.",
                "warn")
    raise AssertionError("unreachable: the loop either returns or raises")


def _no_provider_message(candidates: list[registry.Candidate]) -> str:
    blockers = [f"{c.label}: {c.reason}" for c in candidates
                if c.state in (registry.NOT_CONFIGURED, registry.UNREACHABLE)]
    detail = (" " + " ".join(blockers)) if blockers else ""
    return (
        "No AI image generator is available." + detail
        + " Register an OpenAI key (GPT Image 2.5) or a Gemini key (Nano "
          "Banana) on the Accounts screen, or start ComfyUI / Stable Diffusion "
          "WebUI locally."
    )
