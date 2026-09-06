"""Choosing which model writes the scripts.

  GET  /api/models   -> every model, whether it can be reached, and the chain
  PUT  /api/models   -> pick one
  DELETE /api/models -> hand the choice back to .env

Picking a model is one name, not a chain: the fallback order is derived from
it, so there is never a configuration where the chosen model is the only one
that can answer. That is the whole point — the chosen model is what should
write the short, and the rest are what keep the short getting written when it
cannot.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..config import FALLBACK_ORDER, MODEL_PRESETS, settings
from ..pipeline import llm

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelChoice(BaseModel):
    # A preset name ("astra"), or an explicit "provider:model" for something
    # the presets do not cover. Either way it keeps the presets behind it as
    # fallback.
    model: str


def _describe(name: str, provider: str, model_id: str, note: str) -> dict:
    reason = llm._unavailable_reason(provider, model_id)  # noqa: SLF001
    return {
        "name": name,
        "provider": provider,
        "model": model_id,
        "note": note,
        "ready": not reason,
        # The backend's own words about what is wrong — the UI shows them as
        # they are rather than inventing a friendlier version that says less.
        "reason": reason,
    }


@router.get("")
def list_models() -> dict:
    """Every known model and the chain currently in force."""
    chosen = llm.active_model()
    chain = llm.active_chain()

    models = [_describe(name, *MODEL_PRESETS[name]) for name in FALLBACK_ORDER
              if name in MODEL_PRESETS]

    described = []
    for provider, model_id in chain:
        reason = llm._unavailable_reason(provider, model_id)  # noqa: SLF001
        described.append({"provider": provider, "model": model_id,
                          "ready": not reason, "reason": reason})

    return {
        "chosen": chosen,
        # Where the choice came from, so a setting that seems ignored can be
        # traced instead of guessed at.
        "source": "interface" if db.get_setting(llm.SETTING_KEY) else "env",
        "chain": described,
        "models": models,
        # An override in LLM_CHAIN silently outranks the choice; saying so is
        # the difference between a bug report and a one-line fix.
        "chain_override": bool(settings.llm_chain_raw.strip())
                          and not _is_legacy(settings.llm_chain_raw),
        "any_ready": any(link["ready"] for link in described),
    }


def _is_legacy(raw: str) -> bool:
    from ..config import is_legacy_chain

    return is_legacy_chain(raw)


@router.put("")
def choose_model(choice: ModelChoice) -> dict:
    name = choice.model.strip().lower()
    if not name:
        raise HTTPException(400, "Send the name of a model to use.")
    if name not in MODEL_PRESETS and ":" not in name:
        raise HTTPException(
            400, f"Unknown model '{name}'. Pick one of: "
                 f"{', '.join(FALLBACK_ORDER)} — or give an explicit "
                 f"provider:model.")

    db.set_setting(llm.SETTING_KEY, name)
    return list_models()


@router.delete("")
def reset_model() -> dict:
    """Back to whatever .env says."""
    db.clear_setting(llm.SETTING_KEY)
    return list_models()


@router.post("/refresh")
def refresh() -> dict:
    """Ask the CLIs again which models they have.

    The listing is cached for the life of the process, so a model that was
    enrolled after the server started would otherwise stay invisible until a
    restart — and `gpt-6-astra` is on a staged rollout, so that is exactly the
    case that matters.
    """
    llm._MODEL_CACHE.clear()  # noqa: SLF001
    return list_models()
