"""Upload de anexos (imagens ou vídeo local) referenciados por JobInput.attachments."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import db
from ..config import settings

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def _kind(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXT:
        return "imagem"
    if ext in VIDEO_EXT:
        return "video"
    raise HTTPException(400, f"Extensão não suportada: {ext or '(nenhuma)'}")


@router.post("")
async def upload(file: UploadFile = File(...)):
    kind = _kind(file.filename or "")
    upload_id = db.new_id("upl")
    ext = Path(file.filename or "").suffix.lower()
    dest = settings.uploads_dir / f"{upload_id}{ext}"

    size = 0
    limit = settings.max_upload_mb * 1024 * 1024
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"Arquivo maior que {settings.max_upload_mb}MB")
            fh.write(chunk)

    return {"id": upload_id, "kind": kind, "filename": file.filename,
            "size_bytes": size, "path": str(dest)}


@router.get("/{upload_id}")
def get_upload(upload_id: str):
    matches = list(settings.uploads_dir.glob(f"{upload_id}.*"))
    if not matches:
        raise HTTPException(404, "Upload não encontrado")
    return FileResponse(matches[0])


def resolve(upload_id: str) -> Path:
    """Usado pelo pipeline para traduzir um id de attachment em caminho real."""
    matches = list(settings.uploads_dir.glob(f"{upload_id}.*"))
    if not matches:
        raise FileNotFoundError(f"Upload {upload_id} não encontrado")
    return matches[0]
