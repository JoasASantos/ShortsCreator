"""Footage de fundo: guardar uma vez, usar em todos os shorts."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..pipeline import fundos as fundos_mod
from . import uploads as uploads_router

router = APIRouter(prefix="/api/fundos", tags=["fundos"])


class SaveRequest(BaseModel):
    source: str = Field(default="", description="Link de um vídeo longo")
    attachment: str = Field(default="", description="id de /api/uploads")
    name: str = ""


@router.get("")
def listar():
    return {"fundos": fundos_mod.listar()}


@router.post("")
def guardar(body: SaveRequest):
    """Baixa (ou copia) o vídeo uma vez e deixa pronto para reuso.

    Duas horas de parkour não podem ser baixadas de novo a cada short — aqui
    elas são baixadas uma vez, e dez shorts depois usam o mesmo arquivo.
    """
    try:
        if body.attachment:
            fundo = fundos_mod.adopt(uploads_router.resolve(body.attachment),
                                     body.name)
        else:
            fundo = fundos_mod.fetch(body.source)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — yt-dlp/ffmpeg falhando
        raise HTTPException(502, f"Não deu para guardar o fundo: {exc}") from exc
    return fundo.as_dict()


@router.delete("/{fundo_id}")
def apagar(fundo_id: str):
    if not fundos_mod.remove(fundo_id):
        raise HTTPException(404, "Fundo não encontrado")
    return {"deleted": True}
