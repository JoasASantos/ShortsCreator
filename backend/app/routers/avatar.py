"""Your own avatar with your own voice.

  GET  /api/avatar               -> what is available, and what each part unlocks
  GET  /api/avatar/avatars       -> the presenters on the HeyGen account
  GET  /api/avatar/voices        -> the voices on the HeyGen account
  POST /api/avatar/videos        -> queues the avatar video

An avatar video is an ordinary job with `edit_mode = "avatar"`, so following
it, editing its timeline, auditing it and publishing it all go through
/api/jobs exactly as for every other short. That is why there is no
`GET /api/avatar/videos/{id}` here — `GET /api/jobs/{id}` already answers it,
and a second endpoint over the same row would only drift from it.

Registering your own voice from a sample lives on /api/voices/clone instead: a
cloned voice is a row in the voices table, usable everywhere a voice is usable,
not a thing that belongs to this screen.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import db, worker
from ..pipeline import avatar
from ..schemas import (JobInput, WatermarkPosition, WatermarkSize)

router = APIRouter(prefix="/api/avatar", tags=["avatar"])


@router.get("")
def capabilities(probe: bool = Query(True)):
    """Honest state of the whole feature: HeyGen configured or not, the local
    XTTS server reachable or not, and what each one unlocks.

    `probe=false` skips the round trip to the local server — for a caller that
    only wants the catalog and not the truth about what is running.
    """
    return avatar.describe(probe=probe)


@router.get("/avatars")
def avatars():
    try:
        return avatar.list_avatars()
    except avatar.AvatarNotConfigured as exc:
        # 400, not 500: an unconfigured provider is a normal answer to this
        # question, and the text is the instruction.
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — a live API failing is a message
        raise HTTPException(502, f"HeyGen did not answer: {exc}")


@router.get("/voices")
def voices():
    try:
        return avatar.list_voices()
    except avatar.AvatarNotConfigured as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"HeyGen did not answer: {exc}")


class AvatarVideoRequest(BaseModel):
    """The script, who reads it, and the finishing the short gets."""
    script: str
    avatar_id: str
    voice_id: str = ""
    title: str = ""
    language: str = "pt-BR"
    caption_style: str = "karaoke"
    caption_position: str = "centro"
    watermark: str = ""
    watermark_position: WatermarkPosition = "baixo_centro"
    watermark_size: WatermarkSize = "medio"
    watermark_opacity: float = 0.6


@router.post("/videos")
def create_avatar_video(request: AvatarVideoRequest):
    # Refused here rather than inside the worker: a job that can only fail is
    # worse than an answer that says what to configure.
    if not avatar.is_configured():
        raise HTTPException(400, avatar.SETUP)

    try:
        script = avatar.check_script(request.script)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if not request.avatar_id.strip():
        raise HTTPException(400, "Choose which avatar reads the script.")
    if not request.voice_id.strip():
        raise HTTPException(400, "Choose which voice the avatar speaks in.")

    job = JobInput(
        # The script is final text the user wrote or pasted — the same source
        # kind the "paste the script" flow uses, so nothing asks an LLM for it.
        source_type="roteiro",
        source=script,
        edit_mode=avatar.MODE,
        language=request.language,
        avatar_id=request.avatar_id.strip(),
        avatar_voice_id=request.voice_id.strip(),
        caption_style=request.caption_style,
        caption_position=request.caption_position,
        watermark=request.watermark,
        watermark_position=request.watermark_position,
        watermark_size=request.watermark_size,
        watermark_opacity=request.watermark_opacity,
        # The provider supplies both the picture and the voice. Music under a
        # presenter talking to camera, and a title card over their face, are
        # both things to add deliberately in the editor.
        background="video_fonte",
        music=False,
        title_overlay=False,
        # QA autofix rewrites the script to fix a finding, and rewriting the
        # script here would mean paying for a second avatar render. The audit
        # still runs and still reports.
        qa_autofix=False,
    )

    job_id = db.create_job(job.model_dump(), request.title.strip())
    worker.enqueue(job_id)
    return {"job_id": job_id, "status": "queued"}
