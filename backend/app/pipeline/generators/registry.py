"""Catalog of the AI media generators, and the choice of which one runs.

Why a registry instead of more branches inside videogen: the providers listed
here do more than the short's synthetic background. Nano Banana Pro and the two
local servers also make stills, and the generators screen has to describe every
one of them whether or not a video is being rendered. So videogen stays exactly
what it was — the entry point for background='ia_video' — and asks this module
which provider to use.

Selection is data, not control flow. `candidates()` returns every provider in
priority order together with the reason it was kept or skipped, and `select()`
just takes the first eligible one. Whatever the UI shows about "why this model"
comes from the same function that made the decision, so the explanation and the
behaviour cannot drift apart.

Credentials are never read from settings here: they come from
connectors.credentials(<id>), so the Accounts screen keeps beating .env, and a
local server's URL follows the same rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ...config import settings
from .. import connectors

TEXT_TO_VIDEO = "text_to_video"
IMAGE_TO_VIDEO = "image_to_video"
TEXT_TO_IMAGE = "text_to_image"
IMAGE_TO_IMAGE = "image_to_image"

CAPABILITIES = (TEXT_TO_VIDEO, IMAGE_TO_VIDEO, TEXT_TO_IMAGE, IMAGE_TO_IMAGE)

# Provider states. "unreachable" only ever applies to a local server: a key
# that merely exists proves nothing about a server that is not running, so
# reachability is part of the state rather than an afterthought.
READY = "ready"
NOT_CONFIGURED = "not_configured"
UNREACHABLE = "unreachable"
INCAPABLE = "incapable"


class GeneratorNotConfigured(RuntimeError):
    """No provider can do the job. A normal state, not a bug — so the message
    lists what each provider is missing and what to use instead."""


class ProviderRefused(RuntimeError):
    """The provider will not serve this request and retrying will not change
    that: no credit, a revoked key, a prompt blocked by a content filter.

    Kept apart from a network error exactly the way tts.VoiceUnavailable is,
    because only a refusal justifies moving on to the next provider. A timeout
    or a 502 propagates: silently swapping the model that made someone's video
    over a transient blip is worse than failing.
    """


class LocalServerDown(RuntimeError):
    """A local generator (ComfyUI, Automatic1111) is not answering. Its message
    always names the URL that was tried and how to bring the server up."""


@dataclass
class Provider:
    id: str
    label: str
    capabilities: tuple[str, ...]
    hosting: str            # "hosted" | "local"
    cost: str               # "paid" | "free"
    speed: str              # rough wall clock per asset, for the UI to show
    setup: str              # one actionable sentence: how to make it work
    docs: str = ""
    # Connector holding the credential (or, for a local server, the base URL).
    connector: str = ""
    # Local servers only: resolves the effective base URL at call time, so a
    # changed setting or a test's monkeypatch is picked up.
    base_url: Callable[[], str] | None = None
    # Selectable model names, (id, label). Aliases let a user ask for
    # "seedance" and get the provider's own id for it.
    models: tuple[tuple[str, str], ...] = ()
    model_aliases: dict[str, str] = field(default_factory=dict)
    default_model: str = ""
    # Lower runs first among equally eligible providers. Authored best-first
    # per capability, not alphabetically.
    priority: int = 50


def _comfyui_url() -> str:
    return connectors.credentials("comfyui").get("base_url") or settings.comfyui_url


def _a1111_url() -> str:
    return connectors.credentials("a1111").get("base_url") or settings.a1111_url


PROVIDERS: list[Provider] = [
    Provider(
        id="higgsfield",
        label="Higgsfield",
        capabilities=(TEXT_TO_VIDEO,),
        hosting="hosted", cost="paid", speed="40-120 s per clip",
        setup="Register the Higgsfield key pair on the Accounts screen (or "
              "HIGGSFIELD_KEY_ID/HIGGSFIELD_KEY_SECRET in .env).",
        docs="https://docs.higgsfield.ai/docs",
        connector="higgsfield",
        # One API fronts all of these, so the model is the real choice here —
        # asking for "Seedance" must not mean writing a second client.
        models=(
            ("seedance-lite", "Seedance 1 Lite (fast, cheapest)"),
            ("seedance-pro", "Seedance 1 Pro (sharper, native 9:16)"),
            ("kling-2.5-pro", "Kling 2.5 Turbo Pro"),
            ("sora-2", "Sora 2"),
            ("hailuo-standard", "Hailuo 02 Standard"),
            ("hailuo-pro", "Hailuo 02 Pro"),
            ("wan-2.5", "Wan 2.5 Preview"),
        ),
        model_aliases={
            "seedance": "seedance-lite",
            "seedance-1-lite": "seedance-lite",
            "seedance-1-pro": "seedance-pro",
            "kling": "kling-2.5-pro",
            "sora": "sora-2",
            "hailuo": "hailuo-standard",
            "wan": "wan-2.5",
        },
        default_model="seedance-lite",
        priority=10,
    ),
    Provider(
        id="nanobanana",
        label="Nano Banana Pro (Gemini)",
        capabilities=(TEXT_TO_IMAGE, IMAGE_TO_IMAGE),
        hosting="hosted", cost="paid", speed="10-40 s per image",
        setup="Register the Gemini API key on the Accounts screen (or "
              "GEMINI_API_KEY in .env). Get one at aistudio.google.com.",
        docs="https://ai.google.dev/gemini-api/docs/image-generation",
        connector="gemini",
        models=(
            ("gemini-3-pro-image-preview", "Nano Banana Pro (best text in image)"),
            ("gemini-2.5-flash-image", "Nano Banana (fast, cheaper)"),
        ),
        model_aliases={
            "nano-banana-pro": "gemini-3-pro-image-preview",
            "nano-banana": "gemini-2.5-flash-image",
            "gemini-3-pro-image": "gemini-3-pro-image-preview",
        },
        default_model="gemini-3-pro-image-preview",
        priority=10,
    ),
    Provider(
        id="comfyui",
        label="ComfyUI (local)",
        capabilities=(TEXT_TO_IMAGE, TEXT_TO_VIDEO),
        hosting="local", cost="free", speed="1-10 min per clip on a local GPU",
        setup="Start ComfyUI and export a workflow with 'Save (API format)' to "
              "the path in COMFYUI_WORKFLOW. The workflow decides whether this "
              "provider makes stills or video.",
        docs="https://docs.comfy.org/development/comfyui-server/comms_routes",
        connector="comfyui",
        base_url=_comfyui_url,
        priority=20,
    ),
    Provider(
        id="a1111",
        label="Stable Diffusion WebUI (local)",
        capabilities=(TEXT_TO_IMAGE,),
        hosting="local", cost="free", speed="5-60 s per image on a local GPU",
        setup="Start the WebUI with --api (./webui.sh --api) and, if it is not "
              "on this machine, point A1111_URL at it.",
        docs="https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/API",
        connector="a1111",
        base_url=_a1111_url,
        priority=30,
    ),
]

BY_ID = {p.id: p for p in PROVIDERS}


def get(provider_id: str) -> Provider:
    if provider_id not in BY_ID:
        raise KeyError(f"Unknown generator: {provider_id}")
    return BY_ID[provider_id]


def resolve_model(provider_id: str, name: str) -> str:
    """A model asked for by any name the user might type, mapped to the id the
    provider's own API expects. An unknown name falls back to the default
    rather than being passed through — a typo should not become an HTTP 404
    halfway into a render."""
    provider = get(provider_id)
    wanted = (name or "").strip().lower()
    if not wanted:
        return provider.default_model
    known = {mid for mid, _ in provider.models}
    if wanted in known:
        return wanted
    return provider.model_aliases.get(wanted, provider.default_model)


# ----------------------------------------------------------------- probing

def _probe(provider: Provider) -> str:
    """Proves a local server is actually there. Returns a short line to show."""
    url = provider.base_url() if provider.base_url else ""
    if provider.id == "comfyui":
        from . import comfyui

        return comfyui.probe(url)
    if provider.id == "a1111":
        from . import a1111

        return a1111.probe(url)
    raise RuntimeError(f"{provider.label} has no reachability probe")


def state(provider_id: str, probe: bool = True,
          cache: dict | None = None) -> tuple[str, str]:
    """(state, reason) for one provider.

    `cache` lets a caller describing everything probe each local server once
    instead of once per capability.
    """
    provider = get(provider_id)
    if cache is not None and provider_id in cache:
        return cache[provider_id]

    result: tuple[str, str]
    if provider.connector and provider.base_url is None:
        if not connectors.is_configured(provider.connector):
            result = (NOT_CONFIGURED, provider.setup)
        else:
            result = (READY, f"credential in place ({connectors.source(provider.connector) or 'env'})")
    elif provider.base_url is not None:
        url = provider.base_url()
        if not probe:
            result = (READY, f"local server at {url} (not probed)")
        else:
            try:
                result = (READY, _probe(provider))
            except LocalServerDown as exc:
                result = (UNREACHABLE, str(exc))
            except ProviderRefused as exc:
                # The server is up but something the user has to supply is
                # missing — a ComfyUI with no workflow to queue. That is
                # "not configured", not "unreachable".
                result = (NOT_CONFIGURED, str(exc))
            except Exception as exc:  # noqa: BLE001 — a live server misbehaving
                result = (UNREACHABLE, f"{url} answered, but not like {provider.label}: {exc}")
    else:
        result = (READY, "needs no configuration")

    if cache is not None:
        cache[provider_id] = result
    return result


# --------------------------------------------------------------- selection

@dataclass
class Candidate:
    provider_id: str
    label: str
    state: str
    eligible: bool
    reason: str
    hosting: str
    cost: str
    speed: str
    model: str = ""

    def as_dict(self) -> dict:
        return {
            "provider": self.provider_id, "label": self.label,
            "state": self.state, "eligible": self.eligible,
            "reason": self.reason, "hosting": self.hosting,
            "cost": self.cost, "speed": self.speed, "model": self.model,
        }


def _order_key(provider: Provider, prefer_local: bool) -> tuple:
    if prefer_local:
        return (0 if provider.hosting == "local" else 1, provider.priority, provider.id)
    return (provider.priority, provider.id)


def candidates(capability: str, prefer: str = "", model: str = "",
               probe: bool = True, cache: dict | None = None) -> list[Candidate]:
    """Every provider considered for `capability`, best first, each with the
    reason it was kept or skipped.

    This is the whole selection logic: `select()` takes the first entry whose
    `eligible` is true. Exposing the list is the point — a user who wonders why
    their video came out of ComfyUI instead of Seedance reads it here.
    """
    if capability not in CAPABILITIES:
        raise KeyError(f"Unknown capability: {capability}")

    prefer = (prefer or "").strip()
    prefer_local = bool(getattr(settings, "generator_prefer_local", False))
    ordered = sorted(PROVIDERS, key=lambda p: _order_key(p, prefer_local))
    if prefer in BY_ID:
        ordered = [BY_ID[prefer]] + [p for p in ordered if p.id != prefer]

    out: list[Candidate] = []
    for provider in ordered:
        chosen_model = resolve_model(provider.id, model) if provider.models else ""
        if capability not in provider.capabilities:
            out.append(Candidate(provider.id, provider.label, INCAPABLE, False,
                                 f"does not do {capability}", provider.hosting,
                                 provider.cost, provider.speed, chosen_model))
            continue
        provider_state, reason = state(provider.id, probe=probe, cache=cache)
        eligible = provider_state == READY
        if eligible and provider.id == prefer:
            reason = f"asked for explicitly — {reason}"
        elif eligible and prefer_local and provider.hosting == "local":
            reason = f"local and free, preferred by GENERATOR_PREFER_LOCAL — {reason}"
        out.append(Candidate(provider.id, provider.label, provider_state, eligible,
                             reason, provider.hosting, provider.cost,
                             provider.speed, chosen_model))
    return out


def select(capability: str, prefer: str = "", model: str = "",
           probe: bool = True) -> Candidate:
    """The provider that will run, or GeneratorNotConfigured explaining why
    none can."""
    plan = candidates(capability, prefer=prefer, model=model, probe=probe)
    for candidate in plan:
        if candidate.eligible:
            return candidate
    raise GeneratorNotConfigured(unavailable_message(capability, plan))


def unavailable_message(capability: str, plan: list[Candidate]) -> str:
    """Why nothing can do `capability`, provider by provider. Built from the
    same candidate list the selection used, so it can never describe a
    different world than the one that failed."""
    blockers = [f"{c.label}: {c.reason}" for c in plan
                if c.state in (NOT_CONFIGURED, UNREACHABLE)]
    detail = " ".join(blockers) if blockers else "no provider declares it."
    return f"No generator is available for {capability}. {detail}"


# ------------------------------------------------------------------ running

def run_text_to_video(provider_id: str, prompt: str, duration: float,
                      out_path: Path, aspect: str = "9:16", model: str = "",
                      log=lambda m, *_: None) -> Path:
    provider = get(provider_id)
    if TEXT_TO_VIDEO not in provider.capabilities:
        raise GeneratorNotConfigured(f"{provider.label} does not generate video")

    if provider_id == "higgsfield":
        from . import higgsfield

        return higgsfield.generate_clip(
            prompt, duration, out_path, connectors.credentials("higgsfield"),
            model=resolve_model("higgsfield", model), log=log)
    if provider_id == "comfyui":
        from . import comfyui

        return comfyui.generate(prompt, out_path, want="video",
                                base_url=provider.base_url(), log=log)
    raise GeneratorNotConfigured(f"{provider.label} has no video client yet")


def run_text_to_image(provider_id: str, prompt: str, out_path: Path,
                      aspect: str = "9:16", model: str = "",
                      reference: Path | None = None,
                      log=lambda m, *_: None) -> Path:
    provider = get(provider_id)
    wanted = IMAGE_TO_IMAGE if reference else TEXT_TO_IMAGE
    if wanted not in provider.capabilities:
        raise GeneratorNotConfigured(f"{provider.label} does not do {wanted}")

    if provider_id == "nanobanana":
        from . import nanobanana

        return nanobanana.generate_image(
            prompt, out_path, connectors.credentials("gemini"),
            model=resolve_model("nanobanana", model), aspect=aspect,
            reference=reference, log=log)
    if provider_id == "comfyui":
        from . import comfyui

        return comfyui.generate(prompt, out_path, want="image",
                                base_url=provider.base_url(), log=log)
    if provider_id == "a1111":
        from . import a1111

        return a1111.generate_image(prompt, out_path, base_url=provider.base_url(),
                                    aspect=aspect, log=log)
    raise GeneratorNotConfigured(f"{provider.label} has no image client yet")


# ------------------------------------------------------------- description

def describe(provider_id: str, probe: bool = True,
             cache: dict | None = None) -> dict:
    provider = get(provider_id)
    provider_state, reason = state(provider_id, probe=probe, cache=cache)
    return {
        "id": provider.id,
        "label": provider.label,
        "capabilities": list(provider.capabilities),
        "hosting": provider.hosting,
        "cost": provider.cost,
        "speed": provider.speed,
        "state": provider_state,
        "reason": reason,
        "setup": provider.setup,
        "docs": provider.docs,
        "connector": provider.connector,
        "base_url": provider.base_url() if provider.base_url else "",
        "models": [{"id": mid, "label": label} for mid, label in provider.models],
        "default_model": provider.default_model,
        # What having this provider working actually buys the user.
        "unlocks": [_unlock(cap) for cap in provider.capabilities],
    }


_UNLOCKS = {
    TEXT_TO_VIDEO: "the 'ia_video' background, generated from the prompt",
    IMAGE_TO_VIDEO: "animating a still into the short's background",
    TEXT_TO_IMAGE: "generated stills for covers and Ken Burns backgrounds",
    IMAGE_TO_IMAGE: "restyling or extending an image you already have",
}


def _unlock(capability: str) -> str:
    return _UNLOCKS.get(capability, capability)


def describe_all(probe: bool = True) -> dict:
    """Everything the generators screen needs: the providers with their real
    state, plus the ordered plan per capability so the choice is inspectable
    before anything is rendered."""
    cache: dict = {}
    return {
        "providers": [describe(p.id, probe=probe, cache=cache) for p in PROVIDERS],
        "selection": {
            cap: [c.as_dict() for c in candidates(cap, probe=probe, cache=cache)]
            for cap in CAPABILITIES
        },
    }


def test(provider_id: str) -> str:
    """A real, cheap call proving the provider works right now. Mirrors
    connectors.test — same contract, same one-line answer on screen."""
    provider = get(provider_id)
    if provider.base_url is not None:
        return _probe(provider)
    if not connectors.is_configured(provider.connector):
        raise GeneratorNotConfigured(provider.setup)
    return connectors.test(provider.connector)
