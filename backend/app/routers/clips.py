"""Slice a long video into several shorts.

A two-step flow, because transcribing an hour-long video takes minutes and
does not fit in a synchronous HTTP request:

  POST /api/clips           -> queues the analysis, returns a plan_id
  GET  /api/clips/{id}      -> follow along; once ready, brings the stretches found
  POST /api/clips/{id}/render -> cuts the chosen stretches and creates one job per
                                 clip (and, optionally, schedules one per day)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, worker

router = APIRouter(prefix="/api/clips", tags=["clips"])


class ClipPlanRequest(BaseModel):
    attachment_id: str
    count: int = 3
    target_seconds: int = 45
    niche: str = "generico"
    language: str = "pt-BR"


class ClipSchedule(BaseModel):
    """Schedules the batch's shorts in sequence: the first at `start_at`, the
    rest every `every_hours`. Publishing waits for each job to finish."""
    account_id: str
    start_at: str                 # ISO8601
    every_hours: float = 24.0
    privacy: str = "public"


class ClipRenderRequest(BaseModel):
    selected: list[int] = []          # clip indices; empty = all of them
    voice_id: str | None = None
    caption_style: str = "karaoke"
    caption_position: str = "centro"
    music: bool = True
    watermark: str = ""
    qa_autofix: bool = True
    schedule: ClipSchedule | None = None


def serialize_plan(row: dict) -> dict:
    """Public because the livestream router answers with the same row shape."""
    out = dict(row)
    for key in ("options_json", "clips_json", "jobs_json"):
        raw = out.pop(key, None)
        out[key.replace("_json", "")] = json.loads(raw) if raw else None
    return out


@router.post("")
def create_plan(request: ClipPlanRequest):
    if request.count < 1 or request.count > 12:
        raise HTTPException(400, "Pick between 1 and 12 clips.")
    plan_id = db.create_clip_plan(
        request.attachment_id, request.count, request.target_seconds,
        {"niche": request.niche, "language": request.language},
    )
    worker.enqueue_clip_plan(plan_id)
    return {"plan_id": plan_id, "status": "queued"}


@router.get("")
def list_plans():
    return [serialize_plan(row) for row in db.list_clip_plans()]


@router.get("/{plan_id}")
def get_plan(plan_id: str):
    row = db.get_clip_plan(plan_id)
    if row is None:
        raise HTTPException(404, "Plan not found")
    return serialize_plan(row)


@router.delete("/{plan_id}")
def delete_plan(plan_id: str):
    with db._lock, db.connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM clip_plans WHERE id=?", (plan_id,))
    return {"deleted": plan_id}


@router.post("/{plan_id}/render")
def render_clips(plan_id: str, request: ClipRenderRequest):
    row = db.get_clip_plan(plan_id)
    if row is None:
        raise HTTPException(404, "Plan not found")
    if row["status"] not in ("ready", "rendered"):
        raise HTTPException(400, f"Plan is not ready yet (status: {row['status']}).")

    clips = json.loads(row["clips_json"] or "[]")
    if not clips:
        raise HTTPException(400, "No clip available in this plan.")

    indices = request.selected or list(range(len(clips)))
    options = json.loads(row["options_json"] or "{}")

    account = None
    if request.schedule:
        account = db.get_account(request.schedule.account_id)
        if account is None:
            raise HTTPException(404, "Account for scheduling not found")
        try:
            first = datetime.fromisoformat(request.schedule.start_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Invalid start_at (use ISO8601)")
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)

    from ..pipeline import clipper_jobs

    overrides = request.model_dump(exclude={"schedule"})
    job_ids = clipper_jobs.spawn_jobs(
        plan_row=row, clips=clips, indices=indices, options=options,
        overrides=overrides,
    )
    previous = json.loads(row["jobs_json"] or "[]")
    db.update_clip_plan(plan_id, status="rendered",
                        jobs_json=json.dumps(previous + job_ids))

    schedules: list[dict] = []
    for position, job_id in enumerate(job_ids):
        worker.enqueue(job_id)
        if request.schedule and account is not None:
            when = first + timedelta(hours=request.schedule.every_hours * position)
            clip = clips[indices[position]]
            payload = {
                "title": clip.get("titulo", "Clipe"),
                "description": clip.get("motivo", ""),
                "tags": [],
                "privacy": request.schedule.privacy,
                "publish_at": None,
                "direct_post": (account["platform"] == "tiktok"
                                and request.schedule.privacy == "public"),
                "privacy_level": ("PUBLIC_TO_EVERYONE"
                                  if request.schedule.privacy == "public" else "SELF_ONLY"),
            }
            sid = db.create_schedule(job_id, account["id"], account["platform"],
                                     when.isoformat(), payload)
            schedules.append({"schedule_id": sid, "job_id": job_id,
                              "publish_at": when.isoformat()})
            db.log_event(job_id, f"Scheduled for {when.strftime('%d/%m %H:%M')} UTC "
                                 f"on {account['platform']} (batch {plan_id})")

    return {"plan_id": plan_id, "jobs": job_ids, "schedules": schedules}
