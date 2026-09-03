"""Glue between the clip plan and the job queue.

`analyze_plan` transcribes the video and asks the LLM for the best stretches.
`spawn_jobs` cuts each chosen stretch into its own file and creates a regular
job per clip — from there on each short follows the standard pipeline, with an
independent script, narration, captions and QA.
"""
from __future__ import annotations

import json

from .. import db
from ..config import settings
from ..routers import uploads as uploads_router
from ..schemas import JobInput
from . import clipper, highlights, ingest, render


def analyze_plan(plan_id: str) -> None:
    row = db.get_clip_plan(plan_id)
    if row is None:
        raise RuntimeError(f"Plan {plan_id} does not exist")

    db.update_clip_plan(plan_id, status="analisando")
    source = uploads_router.resolve(row["attachment_id"])
    total = highlights.probe_duration(source)

    segments = ingest.whisper_segments(source)
    if not segments:
        raise RuntimeError(
            "Could not transcribe the video. Install faster-whisper "
            "(pip install faster-whisper) to slice long videos."
        )

    transcript = clipper.transcript_with_timestamps(segments)
    clips = clipper.pick_clips(
        transcript, total, row["requested"], row["target_seconds"]
    )
    if not clips:
        raise RuntimeError("The model found no usable stretches in this video.")

    db.update_clip_plan(plan_id, status="ready", clips_json=json.dumps(clips))


def spawn_jobs(plan_row: dict, clips: list[dict], indices: list[int],
               options: dict, overrides: dict) -> list[str]:
    source = uploads_router.resolve(plan_row["attachment_id"])
    job_ids: list[str] = []

    for index in indices:
        if index < 0 or index >= len(clips):
            continue
        clip = clips[index]

        # each clip becomes its own file, registered as an upload so that the
        # job follows the normal "video uploaded by the user" path
        clip_id = db.new_id("upl")
        clip_path = settings.uploads_dir / f"{clip_id}.mp4"
        render.trim_video(source, clip["inicio"], clip["fim"], clip_path)

        job = JobInput(
            source_type="video",
            source=clip.get("assunto") or clip.get("titulo", ""),
            attachments=[clip_id],
            edit_mode="narrar_por_cima",
            niche=options.get("niche", "generico"),
            language=options.get("language", "pt-BR"),
            duration=plan_row["target_seconds"],
            background="video_fonte",
            voice_id=overrides.get("voice_id"),
            caption_style=overrides.get("caption_style", "karaoke"),
            caption_position=overrides.get("caption_position", "centro"),
            music=overrides.get("music", True),
            watermark=overrides.get("watermark", ""),
            qa_autofix=overrides.get("qa_autofix", True),
        )
        job_id = db.create_job(job.model_dump(), clip.get("titulo", "Clipe"))
        db.log_event(
            job_id,
            f"Clip {index + 1} of {plan_row['id']}: "
            f"{clip['inicio']:.0f}s–{clip['fim']:.0f}s — {clip.get('motivo', '')}",
        )
        job_ids.append(job_id)

    return job_ids
