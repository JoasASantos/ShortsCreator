"""Editing a reel you recorded yourself.

  POST /api/reels                  -> queues the analysis of a recording
  POST /api/reels/{job_id}/assist  -> asks for concrete edits, given your prompt
  POST /api/reels/{job_id}/media   -> places an uploaded file over the video

A reel is an ordinary job with `edit_mode = "meu_video"`, so everything the
dashboard already does — status polling, the timeline editor, QA, the cover,
publishing, metrics — works on it with nothing added. Which is also why there
is no `GET /api/reels/{id}` here: `GET /api/jobs/{id}` already answers that,
and a second endpoint returning the same row would only drift from it.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, worker
from ..config import settings
from ..pipeline import ingest, reels
from ..schemas import JobInput

router = APIRouter(prefix="/api/reels", tags=["reels"])


class ReelRequest(BaseModel):
    """Either an uploaded recording or a link to one — one of the two."""
    attachment_id: str = ""
    url: str = ""
    title: str = ""
    niche: str = "generico"
    language: str = "pt-BR"
    caption_style: str = "karaoke"
    caption_position: str = "centro"
    watermark: str = ""
    watermark_position: str = "baixo_centro"
    watermark_size: str = "medio"
    watermark_opacity: float = 0.6
    # There is no TTS in this mode, so this is the choice between keeping the
    # voice on the recording and delivering it silent with captions only.
    keep_audio: bool = True
    # What the person is going for. Kept on the job so the assist endpoint can
    # be asked again later without retyping it.
    instruction: str = ""


@router.post("")
def create_reel(request: ReelRequest):
    attachment = request.attachment_id.strip()
    url = request.url.strip()

    if bool(attachment) == bool(url):
        raise HTTPException(
            400, "Send attachment_id or url — exactly one of them.")
    if url and not ingest.is_url(url):
        raise HTTPException(
            400, "Invalid url (it has to start with http:// or https://).")

    job = JobInput(
        source_type="video",
        source=url,
        attachments=[attachment] if attachment else [],
        edit_mode=reels.MODE,
        niche=request.niche,
        language=request.language,
        instruction=request.instruction,
        caption_style=request.caption_style,
        caption_position=request.caption_position,
        watermark=request.watermark,
        watermark_position=request.watermark_position,
        watermark_size=request.watermark_size,
        watermark_opacity=request.watermark_opacity,
        keep_audio=request.keep_audio,
        # A recording of your own supplies its own picture and its own voice.
        # Leaving music on would mix a bed under someone talking to camera
        # without them asking for it.
        background="video_fonte",
        music=False,
        title_overlay=False,
        # QA autofix rewrites the script to fix a finding, and there is no
        # script here to rewrite. The audit still runs and still reports.
        qa_autofix=False,
    )

    job_id = db.create_job(job.model_dump(), request.title.strip())
    worker.enqueue(job_id)
    return {"job_id": job_id, "status": "queued"}


# ------------------------------------------------------------------- assist

class AssistRequest(BaseModel):
    # Free-form: "make it more aggressive", "this is for devs, cut the basics",
    # "suggest images for the reverse-engineering part". This is the input that
    # changes the answer most.
    instruction: str = ""


def _reel_or_404(job_id: str) -> tuple[dict, Path, reels.Reel]:
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job not found")

    job_dir = settings.job_dir(job_id)
    reel = reels.load(job_dir)
    if reel is None:
        # Either it is still transcribing, or it is a generated short rather
        # than a recording. Saying which one saves a support round-trip.
        input_data = json.loads(row["input_json"] or "{}")
        if input_data.get("edit_mode") != reels.MODE:
            raise HTTPException(
                400, "This job is a generated short, not a recording of your "
                     "own. Suggestions here read the transcript of your audio.")
        raise HTTPException(
            409, "This recording has not been transcribed yet. Wait for the "
                 "job to finish and try again.")
    return row, job_dir, reel


@router.post("/{job_id}/assist")
def assist(job_id: str, request: AssistRequest | None = None):
    """Concrete edits for this recording: hooks, cuts, media, captions.

    The result is stored on the job, so reopening the editor shows the last
    suggestions instead of spending another LLM call to show the same thing.
    """
    row, job_dir, reel = _reel_or_404(job_id)
    job = JobInput(**json.loads(row["input_json"]))

    instruction = ((request.instruction if request else "") or job.instruction).strip()
    try:
        suggestions = reels.suggest(reel, instruction, job.niche, job.language)
    except Exception as exc:
        raise HTTPException(500, f"Could not get suggestions: {exc}")

    result = json.loads(row["result_json"] or "{}")
    result["assist"] = {"instruction": instruction, **suggestions}
    db.update_job(job_id, result_json=json.dumps(result, ensure_ascii=False))
    db.log_event(
        job_id,
        f"Suggestions: virality {suggestions['virality']['score']}/100, "
        f"{len(suggestions['cuts'])} cut(s), {len(suggestions['media'])} media, "
        f"{len(suggestions['hooks'])} hook(s)")
    return result["assist"]


# -------------------------------------------------------------------- media

class MediaRequest(BaseModel):
    """An uploaded image or video to lay over the recording.

    Geometry is in fractions of the frame — see `timeline.MediaOverlay` for why.
    """
    attachment_id: str
    start: float
    end: float
    x: float = 0.5
    y: float = 0.3
    width: float = 0.6
    opacity: float = 1.0
    in_point: float = 0.0


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


@router.post("/{job_id}/media")
def add_media(job_id: str, request: MediaRequest):
    """Copy an upload into the job and place it on the timeline.

    The file is copied rather than referenced: the render runs with the job
    directory as its working directory, and an overlay pointing outside it
    would break the moment the upload is cleaned up.
    """
    from ..pipeline import timeline as timeline_mod
    from . import uploads as uploads_router

    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job not found")
    if request.end <= request.start:
        raise HTTPException(400, "The media has to end after it starts.")

    try:
        source = uploads_router.resolve(request.attachment_id)
    except FileNotFoundError:
        raise HTTPException(404, "Upload not found. Send the file again.")

    job_dir = settings.job_dir(job_id)
    edl = timeline_mod.load(job_dir)
    if edl is None:
        raise HTTPException(400, "This job has no timeline to place media on.")

    import shutil
    dest = job_dir / f"media_{len(edl.media):02d}{source.suffix.lower()}"
    shutil.copy(source, dest)

    kind = "image" if source.suffix.lower() in IMAGE_SUFFIXES else "video"
    overlay = timeline_mod.MediaOverlay(
        id=timeline_mod._new_id("m"),  # noqa: SLF001
        source=dest.name, kind=kind,
        start=request.start, end=request.end,
        x=request.x, y=request.y, width=request.width,
        opacity=request.opacity, in_point=request.in_point)
    edl.media.append(overlay)
    edl.normalize()
    timeline_mod.save(job_dir, edl)

    db.log_event(job_id, f"Media over the video: {dest.name} "
                         f"({overlay.start:.1f}s–{overlay.end:.1f}s)")
    # The timeline goes back whole: the editor redraws from it, and normalize()
    # may have clipped what was asked for.
    return edl.to_dict()
