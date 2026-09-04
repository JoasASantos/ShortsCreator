"""Films developed from a premise, one stage at a time.

Development is synchronous — one LLM call per stage, the same way the script
refinement routes work — because the whole point is that the user reads what the
model wrote and corrects it before paying for the next step:

  POST /api/films                     -> creates the project and develops the bible
  GET  /api/films/{id}                -> the whole project, plus the cost estimate
  POST /api/films/{id}/stage/{stage}  -> regenerates one stage, optionally with a
                                         new instruction
  PUT  /api/films/{id}/{stage}        -> accepts the user's own edited version
  POST /api/films/{id}/generate       -> reports the estimate, then (with
                                         confirm) queues the generation

Generation is the one thing that does NOT happen in the request: it is minutes
of paid AI video per film, so it goes on the worker's film queue and is polled
here, the same shape as /api/clips.
"""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from .. import db, worker
from ..pipeline import story
from ..schemas import Niche

router = APIRouter(prefix="/api/films", tags=["films"])


class FilmRequest(BaseModel):
    premise: str
    instruction: str = ""
    title: str = ""
    language: str = "pt-BR"
    niche: Niche = "cinema"
    aspect: str = "9:16"
    scenes: int = 6
    shot_seconds: int = story.DEFAULT_SHOT_SECONDS
    voice_id: str | None = None
    # A film is watched, not scrolled past: block captions at the bottom read
    # like subtitles, which is what a film wants.
    caption_style: Literal["karaoke", "bloco", "palavra"] = "bloco"
    caption_position: Literal["centro", "baixo", "topo"] = "baixo"
    watermark: str = ""


class StageRequest(BaseModel):
    instruction: str = ""


class GenerateRequest(BaseModel):
    confirm: bool = False
    # Without a video generator configured every shot comes out as a
    # placeholder. That is a legitimate way to preview the edit, but it has to
    # be asked for — otherwise it looks like a broken film.
    placeholder_ok: bool = False


def serialize_film(row: dict) -> dict:
    out = dict(row)
    for key in ("options_json", "bible_json", "characters_json",
                "screenplay_json", "shots_json", "progress_json"):
        raw = out.pop(key, None)
        out[key.replace("_json", "")] = json.loads(raw) if raw else None
    return out


def _film(film_id: str) -> dict:
    row = db.get_film(film_id)
    if row is None:
        raise HTTPException(404, "Film not found")
    return row


@router.post("")
def create_film(request: FilmRequest):
    premise = request.premise.strip()
    if len(premise) < 12:
        raise HTTPException(400, "Describe the premise of the film in at least "
                                 "one sentence.")
    if not story.MIN_SCENES <= request.scenes <= story.MAX_SCENES:
        raise HTTPException(400, f"Pick between {story.MIN_SCENES} and "
                                 f"{story.MAX_SCENES} scenes.")
    if not story.MIN_SHOT_SECONDS <= request.shot_seconds <= story.MAX_SHOT_SECONDS:
        raise HTTPException(400, f"shot_seconds has to be between "
                                 f"{story.MIN_SHOT_SECONDS} and "
                                 f"{story.MAX_SHOT_SECONDS} — it is one shot, "
                                 f"not a whole scene.")

    options = request.model_dump(exclude={"premise", "instruction", "title"})
    film_id = db.create_film(premise, request.instruction.strip(),
                             request.title.strip(), options)

    # The project row exists before the LLM is called, so a model failure leaves
    # an inspectable film the user can retry the stage on instead of nothing.
    row = db.get_film(film_id)
    try:
        bible = story.develop_stage(row, "bible")
    except Exception as exc:
        db.update_film(film_id, status="error", error=str(exc))
        raise HTTPException(502, f"Failed to develop the story bible: {exc}")

    story.save_stage(row, "bible", bible)
    return serialize_film(db.get_film(film_id))


@router.get("")
def list_films():
    return [serialize_film(row) for row in db.list_films()]


@router.get("/{film_id}")
def get_film(film_id: str):
    body = serialize_film(_film(film_id))
    if body.get("shots"):
        body["estimate"] = story.estimate(_film(film_id))
    return body


@router.delete("/{film_id}")
def delete_film(film_id: str):
    db.delete_film(film_id)
    return {"deleted": film_id}


@router.post("/{film_id}/stage/{stage}")
def regenerate_stage(film_id: str, stage: str, request: StageRequest | None = None):
    """Regenerates a single stage, optionally with a new instruction.

    The instruction applies to this stage only and is not stored as the film's
    instruction: "make the antagonist colder" is about the cast, not about the
    whole film.
    """
    row = _film(film_id)
    if stage not in story.STAGES:
        raise HTTPException(400, f"Unknown stage '{stage}'. Available: "
                                 f"{', '.join(story.STAGES)}.")

    request = request or StageRequest()
    try:
        doc = story.develop_stage(row, stage, request.instruction)
    except story.StageNotReady as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        db.update_film(film_id, error=str(exc))
        raise HTTPException(502, f"Failed to develop the {stage} stage: {exc}")

    invalidated = story.save_stage(row, stage, doc)
    return {"film_id": film_id, "stage": stage, stage: doc,
            "invalidated": invalidated}


@router.put("/{film_id}/{stage}")
def save_stage(film_id: str, stage: str, data: dict = Body(...)):
    """Accepts the user's own version of a stage.

    It goes through the same normalizer the model's output does, so a hand-
    written stage carries the same guarantees — a shot list edited here still
    has every character's visual description inside its prompts.
    """
    row = _film(film_id)
    if stage not in story.STAGES:
        raise HTTPException(400, f"Unknown stage '{stage}'. Available: "
                                 f"{', '.join(story.STAGES)}.")
    try:
        doc = story.accept_stage(row, stage, data)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))

    invalidated = story.save_stage(row, stage, doc)
    return {"film_id": film_id, "stage": stage, stage: doc,
            "invalidated": invalidated}


@router.post("/{film_id}/generate")
def generate_film(film_id: str, request: GenerateRequest | None = None):
    """Queues the generation of every shot and the assembly of the film.

    Answers with the estimate and starts NOTHING unless `confirm` is true. A run
    is minutes of paid video generation, and the caller has to have been able to
    see the size of it first.
    """
    row = _film(film_id)
    request = request or GenerateRequest()

    if not row["shots_json"]:
        raise HTTPException(400, "Develop the shot list before generating the film.")
    if row["status"] in ("queued", "generating"):
        raise HTTPException(400, "This film is already being generated.")

    report = story.estimate(row)
    if not request.confirm:
        return {"film_id": film_id, "queued": False, "estimate": report,
                "detail": "Repeat the request with confirm=true to start "
                          "generating."}

    if not report["provider"]["configured"] and not request.placeholder_ok:
        raise HTTPException(
            400,
            "No AI video generator is configured, so every shot would come out "
            "as a placeholder. Register the key on the Accounts screen, or "
            "repeat with placeholder_ok=true to assemble the film as an "
            "animatic anyway.")

    worker.enqueue_film(film_id)
    return {"film_id": film_id, "queued": True, "status": "queued",
            "estimate": report}
