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
    """Tudo que serve de fundo: o que foi baixado e o que você largou na
    pasta. Para quem escolhe, a origem não muda nada."""
    return {"fundos": fundos_mod.todos(),
            "pasta": str(fundos_mod.stocks_dir()),
            "catalogo": fundos_mod.catalog()}


@router.post("/catalogo/{item_id}")
def baixar_do_catalogo(item_id: str):
    """Guarda um dos gameplays livres do catálogo, com um clique."""
    entry = next((e for e in fundos_mod.STOCK_CATALOG if e["id"] == item_id), None)
    if entry is None:
        raise HTTPException(404, "Esse item não está no catálogo")
    try:
        fundo = fundos_mod.fetch(entry["url"])
    except Exception as exc:  # noqa: BLE001 — vídeo fora do ar, rede, etc.
        raise HTTPException(502, f"Não deu para baixar: {exc}") from exc
    return fundo.as_dict()


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
