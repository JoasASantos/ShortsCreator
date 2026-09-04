"""Cut a livestream into many shorts.

Same two-step shape as /api/clips, because transcribing a 6-hour live takes far
longer than any HTTP request can wait:

  POST /api/livecuts       -> queues the analysis, returns a plan_id
  GET  /api/livecuts/{id}  -> follow along; once ready, brings the cuts found

Underneath it is deliberately the same `clip_plans` row as a regular clip plan,
marked `options_json.mode = "livestream"` — which is also what the clip queue
reads to run the windowed analysis (`pipeline.livecuts`) instead of the plain
long-video one. Rendering is therefore not duplicated here: a ready plan goes
through `POST /api/clips/{plan_id}/render` like any other.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, worker
from ..pipeline import ingest, livecuts
from .clips import serialize_plan

router = APIRouter(prefix="/api/livecuts", tags=["livecuts"])

# A live yields far more than a 2-hour upload does: 10–30 cuts is the usual
# ask, so the /api/clips ceiling of 12 would be the wrong limit here.
MAX_CUTS = 40
MIN_WINDOW_MINUTES = 10
MAX_WINDOW_MINUTES = 90


class LiveCutRequest(BaseModel):
    """Either an uploaded file or a link to the VOD — one of the two."""
    attachment_id: str = ""
    url: str = ""
    count: int = 12
    target_seconds: int = 45
    niche: str = "generico"
    language: str = "pt-BR"
    window_minutes: int = livecuts.WINDOW_SECONDS // 60


@router.post("")
def create_live_plan(request: LiveCutRequest):
    attachment = request.attachment_id.strip()
    url = request.url.strip()

    if bool(attachment) == bool(url):
        raise HTTPException(400, "Send attachment_id or url — exactly one of them.")
    if url and not ingest.is_url(url):
        raise HTTPException(400, "Invalid url (it has to start with http:// or https://).")
    if request.count < 1 or request.count > MAX_CUTS:
        raise HTTPException(400, f"Pick between 1 and {MAX_CUTS} cuts.")
    if not MIN_WINDOW_MINUTES <= request.window_minutes <= MAX_WINDOW_MINUTES:
        raise HTTPException(
            400, f"window_minutes has to be between {MIN_WINDOW_MINUTES} "
                 f"and {MAX_WINDOW_MINUTES}.")

    plan_id = db.create_clip_plan(
        attachment, request.count, request.target_seconds,
        # options_json already carries a free-form dict, so the live-only
        # fields ride along there instead of growing the table a column.
        {"mode": livecuts.MODE, "niche": request.niche,
         "language": request.language, "url": url,
         "window_seconds": request.window_minutes * 60},
    )
    worker.enqueue_clip_plan(plan_id)
    return {"plan_id": plan_id, "status": "queued"}


@router.get("/{plan_id}")
def get_live_plan(plan_id: str):
    row = db.get_clip_plan(plan_id)
    if row is None:
        raise HTTPException(404, "Plan not found")
    body = serialize_plan(row)
    # The table is shared with /api/clips; a plain clip plan is not a live one,
    # and answering as if it were would hide the mix-up.
    if (body.get("options") or {}).get("mode") != livecuts.MODE:
        raise HTTPException(404, "This plan is not a livestream plan")
    return body
