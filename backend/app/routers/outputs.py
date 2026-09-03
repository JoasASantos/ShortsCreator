"""Arquivos finais servidos por URL estável — a Instagram Graph API não aceita
upload: ela baixa o MP4 (e a capa) de uma URL pública. Só o que está em
data/outputs e a capa do job saem por aqui; nada de listar diretório."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..config import settings

router = APIRouter(prefix="/api/outputs", tags=["outputs"])

_JOB = re.compile(r"^(job_[0-9a-f]{12})\.(mp4|jpg)$")


@router.get("/{filename}")
def get_output(filename: str):
    match = _JOB.match(filename)
    if not match:
        raise HTTPException(404, "Arquivo não disponível")
    job_id, ext = match.groups()
    if ext == "mp4":
        path = settings.outputs_dir / filename
        media = "video/mp4"
    else:
        path = settings.jobs_dir / job_id / "cover.jpg"
        media = "image/jpeg"
    if not path.exists():
        raise HTTPException(404, "Arquivo ainda não gerado")
    return FileResponse(path, media_type=media)
