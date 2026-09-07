"""Long-form productions — documentaries, short films, mini-series — developed
one stage at a time from the user's own material.

The LLM stages are synchronous, one call each, the same way films are: the
whole point is that the user reads what the model wrote and corrects it before
the next step is paid for.

  POST /api/longform                        -> creates the project and QUEUES the
                                               ingestion of the material
  GET  /api/longform                        -> the projects (catalog summarised)
  GET  /api/longform/{id}                   -> the whole project, plus the cost
                                               estimate once the plan exists
  GET  /api/longform/{id}/events            -> the ingestion / assembly log
  POST /api/longform/{id}/stage/{stage}     -> develops one stage, optionally with
                                               an instruction (`material` re-queues
                                               the ingestion instead)
  PUT  /api/longform/{id}/{stage}           -> accepts the user's own edited version
  POST /api/longform/{id}/generate          -> reports the estimate, then (with
                                               confirm) queues the assembly
  DELETE /api/longform/{id}

Two things do NOT happen in the request. Ingestion: an interview is transcribed
word by word and forty minutes of that does not fit in an HTTP call. Assembly:
minutes of TTS, stock downloads and a long render. Both go on the worker's
long-form queue and are polled here.
"""
from __future__ import annotations

import json
import shutil
from typing import Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ValidationError

from .. import db, worker
from ..pipeline import longform
from ..schemas import JobInput

router = APIRouter(prefix="/api/longform", tags=["longform"])

ProductionType = Literal["documentario", "mini_documentario", "curta", "mini_serie"]
Narrator = Literal["oculto", "avatar", "sem_narracao"]


class LongformRequest(BaseModel):
    type: ProductionType = "documentario"
    # What the production is about, in the user's words: "Os principais golpes
    # digitais no Brasil em 2026". The subject, not the script.
    prompt: str
    instruction: str = ""
    title: str = ""
    # A preset name (investigativo, jornalistico, terror, dramatico, educativo,
    # inspirador) or free text.
    tone: str = "investigativo"
    style: str = ""
    narrator: Narrator = "oculto"
    language: str = "pt-BR"
    niche: str = "generico"
    # Per production — per episode for a series. 0 means the type's default.
    target_minutes: int = 0
    episodes: int = 1
    # The material. Links (video or article), uploads (interviews, images) and
    # reference examples — links or upload ids — used for structure only.
    links: list[str] = []
    attachments: list[str] = []
    references: list[str] = []
    voice_id: str | None = None
    avatar_id: str = ""
    avatar_voice_id: str = ""
    # A documentary is watched, not scrolled past: block captions low on the
    # frame read like subtitles.
    caption_style: Literal["karaoke", "bloco", "palavra"] = "bloco"
    caption_position: Literal["centro", "baixo", "topo"] = "baixo"
    captions: bool = True
    lower_thirds: bool = True
    watermark: str = ""


class StageRequest(BaseModel):
    instruction: str = ""


class GenerateRequest(BaseModel):
    confirm: bool = False
    # Without a stock provider every stock shot comes out as a placeholder
    # card. That is a legitimate way to preview the edit, but it has to be
    # asked for — otherwise it looks like a broken documentary.
    placeholder_ok: bool = False


def serialize(row: dict, full: bool = True) -> dict:
    out = dict(row)
    for key in ("options_json", "material_json", "briefing_json", "roteiro_json",
                "plano_json", "progress_json", "jobs_json"):
        raw = out.pop(key, None)
        name = {"plano_json": "plano_de_edicao"}.get(key, key.replace("_json", ""))
        out[name] = json.loads(raw) if raw else None
    if not full:
        # The listing stays cheap: a catalog carries whole transcripts.
        material = out.get("material") or {}
        out["material"] = {"items": [
            {k: item.get(k) for k in ("id", "kind", "reference", "title", "duration",
                                      "status", "error")}
            for item in material.get("items", [])]} if material else None
        for key in ("briefing", "roteiro", "plano_de_edicao", "progress"):
            out[key] = bool(out.get(key))
    if out.get("options") and "sources" in out["options"]:
        out["sources"] = out["options"]["sources"]
    return out


def _project(project_id: str) -> dict:
    row = db.get_longform(project_id)
    if row is None:
        raise HTTPException(404, "Production not found")
    return row


def _record_refusals(project_id: str, stage: str, doc: dict) -> None:
    """Writes every refusal of a stage to the production's event log.

    The normalizers already refuse a moment at a second nobody speaks, a shot
    pointing at material that does not exist, a reference example used as
    content — and return them in `rejected`. Returning them is enough for the
    caller that made the request; it is NOT enough for anyone reading the
    screen an hour later, which is what "never silently kept" has to mean.
    So the same list also goes to `GET /api/longform/{id}/events`.

    Logged here, from the final document, rather than from the normalizer's own
    `log` hook: the edit plan is normalized twice when the first answer is
    refused, and logging inside would report the discarded attempt as well.
    """
    for entry in doc.get("rejected") or []:
        where = entry.get("block") or entry.get("what") or ""
        db.log_event(project_id,
                     f"{stage}: {where + ': ' if where else ''}"
                     f"{entry.get('reason', 'refused')}", "warn")
    for note in doc.get("notes") or []:
        db.log_event(project_id, f"{stage}: {note}", "warn")


def _check_niche(niche: str) -> str:
    """The niche list lives in `schemas.Niche`; asking JobInput is how this
    stays right when that list grows."""
    try:
        JobInput(niche=niche)
    except ValidationError:
        raise HTTPException(400, f"Unknown niche '{niche}'.")
    return niche


@router.post("")
def create_project(request: LongformRequest):
    prompt = request.prompt.strip()
    if len(prompt) < 12:
        raise HTTPException(400, "Describe what the production is about in at "
                                 "least one sentence.")
    spec = longform.TYPES[request.type]
    minutes = request.target_minutes or spec["default_minutes"]
    if not spec["min_minutes"] <= minutes <= spec["max_minutes"]:
        raise HTTPException(400, f"A {spec['label'].lower()} runs between "
                                 f"{spec['min_minutes']} and {spec['max_minutes']} "
                                 f"minutes" + (" per episode." if request.type ==
                                               "mini_serie" else "."))
    episodes = request.episodes if request.type == "mini_serie" else 1
    if request.type == "mini_serie" and not (
            longform.MIN_EPISODES <= episodes <= longform.MAX_EPISODES):
        raise HTTPException(400, f"A mini-series has between {longform.MIN_EPISODES} "
                                 f"and {longform.MAX_EPISODES} episodes.")
    links = [u for u in request.links if u.strip()]
    attachments = [a for a in request.attachments if a.strip()]
    if not links and not attachments:
        raise HTTPException(400, "Send at least one piece of material — a video "
                                 "link, an article, an interview upload or an "
                                 "image. Reference examples alone are not content.")
    if request.narrator == "avatar":
        from ..pipeline import avatar as avatar_mod
        if not avatar_mod.is_configured():
            raise HTTPException(400, "The narrator is an avatar but HeyGen is not "
                                     "configured. " + avatar_mod.SETUP + " Or pick "
                                     "narrator=oculto for a voice-over.")
        if not request.avatar_id or not request.avatar_voice_id:
            raise HTTPException(400, "An avatar narrator needs avatar_id and "
                                     "avatar_voice_id from the account's catalog "
                                     "(GET /api/avatar/avatars, /api/avatar/voices).")
    _check_niche(request.niche)

    options = request.model_dump(exclude={"prompt", "instruction", "title", "links",
                                          "attachments", "references"})
    options["target_minutes"] = minutes
    options["episodes"] = episodes
    options["sources"] = {"links": links, "attachments": attachments,
                          "references": [r for r in request.references if r.strip()]}
    project_id = db.create_longform(request.type, prompt, request.instruction.strip(),
                                    request.title.strip(), options)
    worker.enqueue_longform(project_id, "ingest")
    return serialize(db.get_longform(project_id))


@router.get("")
def list_projects():
    return [serialize(row, full=False) for row in db.list_longform()]


@router.get("/types")
def list_types():
    """What the screen offers: the types with their length ranges and
    structure, the narrator modes and the tone presets."""
    return {
        "types": [{"id": key, **spec} for key, spec in longform.TYPES.items()],
        "narrators": [{"id": key, "description": text}
                      for key, text in longform.NARRATOR_DESCRIPTION.items()],
        "tones": [{"id": key, "description": text}
                  for key, text in longform.TONE_PRESETS.items()],
        "stages": list(longform.STAGES),
        "block_kinds": list(longform.BLOCK_KINDS),
        "shot_kinds": list(longform.SHOT_KINDS),
        "min_episodes": longform.MIN_EPISODES,
        "max_episodes": longform.MAX_EPISODES,
    }


@router.get("/{project_id}")
def get_project(project_id: str):
    row = _project(project_id)
    body = serialize(row)
    if body.get("plano_de_edicao"):
        body["estimate"] = longform.estimate(row)
    return body


@router.get("/{project_id}/events")
def project_events(project_id: str, after: int = 0):
    """The ingestion log (written under the project id) followed by the log of
    every episode's job, so one call follows the whole thing."""
    row = _project(project_id)
    events = db.get_events(project_id, after)
    for job_id in longform.jobs_of(row).values():
        events += db.get_events(job_id, after)
    events.sort(key=lambda e: (e["created_at"], e["id"]))
    return events


@router.delete("/{project_id}")
def delete_project(project_id: str):
    row = _project(project_id)
    if row["status"] in ("ingesting", "queued", "assembling"):
        raise HTTPException(400, "This production is being worked on; wait for it "
                                 "to finish before deleting it.")
    db.delete_longform(project_id)
    # The material is big (interviews, downloads) and belongs to nothing else.
    shutil.rmtree(longform.project_dir(project_id), ignore_errors=True)
    return {"deleted": project_id}


@router.post("/{project_id}/stage/{stage}")
def develop_stage(project_id: str, stage: str, request: StageRequest | None = None):
    """Develops (or regenerates) a single stage, optionally with an instruction.

    The instruction applies to this stage only and is not stored as the
    production's instruction: "use less of the second interview" is about the
    plan, not about the whole documentary.
    """
    row = _project(project_id)
    if stage not in longform.STAGES:
        raise HTTPException(400, f"Unknown stage '{stage}'. Available: "
                                 f"{', '.join(longform.STAGES)}.")
    if row["status"] in ("ingesting", "queued", "assembling"):
        raise HTTPException(400, f"This production is busy ({row['status']}); wait "
                                 f"for it to finish before developing a stage.")

    if stage == "material":
        # Ingestion is downloads and transcription, not an LLM call: it goes
        # back on the queue, and everything derived from the old catalog is
        # dropped when the new one lands.
        would_drop = [s for s in longform.CASCADE["material"]
                      if row.get(longform.STAGE_COLUMN[s])]
        worker.enqueue_longform(project_id, "ingest")
        return {"project_id": project_id, "stage": stage, "queued": True,
                "will_invalidate": would_drop,
                "detail": "Re-ingesting the material; poll GET /api/longform/{id}."}

    request = request or StageRequest()
    try:
        doc = longform.develop_stage(row, stage, request.instruction)
    except longform.StageNotReady as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        db.update_longform(project_id, error=str(exc))
        raise HTTPException(502, f"Failed to develop the {stage} stage: {exc}")

    _record_refusals(project_id, stage, doc)
    invalidated = longform.save_stage(row, stage, doc)
    return {"project_id": project_id, "stage": stage, stage: doc,
            "invalidated": invalidated, "rejected": doc.get("rejected", [])}


@router.put("/{project_id}/{stage}")
def save_stage(project_id: str, stage: str, data: dict = Body(...)):
    """Accepts the user's own version of a stage.

    It goes through the same normalizer the model's output does, so a hand-
    written stage carries the same guarantees — a moment typed at a second
    nobody speaks is refused exactly like the model's would be.
    """
    row = _project(project_id)
    if stage not in longform.STAGES:
        raise HTTPException(400, f"Unknown stage '{stage}'. Available: "
                                 f"{', '.join(longform.STAGES)}.")
    if row["status"] in ("ingesting", "queued", "assembling"):
        raise HTTPException(400, f"This production is busy ({row['status']}); wait "
                                 f"for it to finish before editing a stage.")
    try:
        doc = longform.accept_stage(row, stage, data)
    except longform.StageNotReady as exc:
        raise HTTPException(400, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))

    _record_refusals(project_id, stage, doc)
    invalidated = longform.save_stage(row, stage, doc)
    return {"project_id": project_id, "stage": stage, stage: doc,
            "invalidated": invalidated, "rejected": doc.get("rejected", [])}


@router.post("/{project_id}/generate")
def generate(project_id: str, request: GenerateRequest | None = None):
    """Queues the assembly of every episode.

    Answers with the estimate and starts NOTHING unless `confirm` is true. A
    run is minutes of narration to synthesize, dozens of stock downloads and a
    long render, and the caller has to have been able to see the size of it
    first.
    """
    row = _project(project_id)
    request = request or GenerateRequest()

    if not row["plano_json"]:
        raise HTTPException(400, "Develop the edit plan before assembling the "
                                 "production.")
    if row["status"] in ("ingesting", "queued", "assembling"):
        raise HTTPException(400, "This production is already being worked on.")

    report = longform.estimate(row)
    if not request.confirm:
        return {"project_id": project_id, "queued": False, "estimate": report,
                "detail": "Repeat the request with confirm=true to start assembling."}

    narration = report["narration"]["provider"]
    if report["narration"]["blocks"] and not narration["configured"]:
        raise HTTPException(400, f"The narrator is an avatar and HeyGen is not "
                                 f"configured: {narration['reason']} Switch the "
                                 f"narrator to oculto (voice-over) or configure "
                                 f"HeyGen on the Accounts screen.")
    if report["stock"]["clips"] and not report["stock"]["provider"]["configured"] \
            and not request.placeholder_ok:
        raise HTTPException(
            400,
            f"No stock provider is configured, so {report['stock']['clips']} stock "
            f"shot(s) would come out as placeholder cards. Register Pexels, Pixabay "
            f"or Coverr on the Accounts screen, or repeat with placeholder_ok=true "
            f"to assemble the production with cards where the footage would be.")

    worker.enqueue_longform(project_id, "assemble")
    return {"project_id": project_id, "queued": True, "status": "queued",
            "estimate": report}
