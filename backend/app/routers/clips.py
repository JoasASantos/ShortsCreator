"""Fatiar um vídeo longo em vários shorts.

Fluxo em dois passos, porque transcrever um vídeo de uma hora leva minutos e
não cabe numa requisição HTTP síncrona:

  POST /api/clips           -> enfileira a análise, devolve plan_id
  GET  /api/clips/{id}      -> acompanha; quando pronto traz os trechos achados
  POST /api/clips/{id}/render -> corta os trechos escolhidos e cria um job por clipe
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, worker
from ..schemas import JobInput

router = APIRouter(prefix="/api/clips", tags=["clips"])


class ClipPlanRequest(BaseModel):
    attachment_id: str
    count: int = 3
    target_seconds: int = 45
    niche: str = "generico"
    language: str = "pt-BR"


class ClipRenderRequest(BaseModel):
    selected: list[int] = []          # índices dos clipes; vazio = todos
    voice_id: str | None = None
    caption_style: str = "karaoke"
    caption_position: str = "centro"
    music: bool = True
    watermark: str = ""
    qa_autofix: bool = True


def _serialize(row: dict) -> dict:
    out = dict(row)
    for key in ("options_json", "clips_json", "jobs_json"):
        raw = out.pop(key, None)
        out[key.replace("_json", "")] = json.loads(raw) if raw else None
    return out


@router.post("")
def create_plan(request: ClipPlanRequest):
    if request.count < 1 or request.count > 10:
        raise HTTPException(400, "Escolha entre 1 e 10 clipes.")
    plan_id = db.create_clip_plan(
        request.attachment_id, request.count, request.target_seconds,
        {"niche": request.niche, "language": request.language},
    )
    worker.enqueue_clip_plan(plan_id)
    return {"plan_id": plan_id, "status": "queued"}


@router.get("")
def list_plans():
    return [_serialize(row) for row in db.list_clip_plans()]


@router.get("/{plan_id}")
def get_plan(plan_id: str):
    row = db.get_clip_plan(plan_id)
    if row is None:
        raise HTTPException(404, "Plano não encontrado")
    return _serialize(row)


@router.post("/{plan_id}/render")
def render_clips(plan_id: str, request: ClipRenderRequest):
    row = db.get_clip_plan(plan_id)
    if row is None:
        raise HTTPException(404, "Plano não encontrado")
    if row["status"] != "ready":
        raise HTTPException(400, f"Plano ainda não está pronto (status: {row['status']}).")

    clips = json.loads(row["clips_json"] or "[]")
    if not clips:
        raise HTTPException(400, "Nenhum clipe disponível neste plano.")

    indices = request.selected or list(range(len(clips)))
    options = json.loads(row["options_json"] or "{}")

    from ..pipeline import clipper_jobs

    job_ids = clipper_jobs.spawn_jobs(
        plan_row=row, clips=clips, indices=indices, options=options,
        overrides=request.model_dump(),
    )
    db.update_clip_plan(plan_id, status="rendered", jobs_json=json.dumps(job_ids))
    for job_id in job_ids:
        worker.enqueue(job_id)

    return {"plan_id": plan_id, "jobs": job_ids}
