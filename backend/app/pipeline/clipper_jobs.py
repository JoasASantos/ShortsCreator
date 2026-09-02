"""Cola entre o plano de clipes e a fila de jobs.

`analyze_plan` transcreve o vídeo e pede ao LLM os melhores trechos.
`spawn_jobs` corta cada trecho escolhido num arquivo próprio e cria um job
normal por clipe — daí em diante cada short segue o pipeline padrão, com
roteiro, narração, legenda e QA independentes.
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
        raise RuntimeError(f"Plano {plan_id} não existe")

    db.update_clip_plan(plan_id, status="analisando")
    source = uploads_router.resolve(row["attachment_id"])
    total = highlights.probe_duration(source)

    segments = ingest.whisper_segments(source)
    if not segments:
        raise RuntimeError(
            "Não foi possível transcrever o vídeo. Instale faster-whisper "
            "(pip install faster-whisper) para fatiar vídeos longos."
        )

    transcript = clipper.transcript_with_timestamps(segments)
    clips = clipper.pick_clips(
        transcript, total, row["requested"], row["target_seconds"]
    )
    if not clips:
        raise RuntimeError("O modelo não encontrou trechos aproveitáveis neste vídeo.")

    db.update_clip_plan(plan_id, status="ready", clips_json=json.dumps(clips))


def spawn_jobs(plan_row: dict, clips: list[dict], indices: list[int],
               options: dict, overrides: dict) -> list[str]:
    source = uploads_router.resolve(plan_row["attachment_id"])
    job_ids: list[str] = []

    for index in indices:
        if index < 0 or index >= len(clips):
            continue
        clip = clips[index]

        # cada clipe vira um arquivo próprio, registrado como upload para que
        # o job siga o caminho normal de "vídeo enviado pelo usuário"
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
            f"Clipe {index + 1} de {plan_row['id']}: "
            f"{clip['inicio']:.0f}s–{clip['fim']:.0f}s — {clip.get('motivo', '')}",
        )
        job_ids.append(job_id)

    return job_ids
