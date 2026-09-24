"""Elenco: quem conversa nos vídeos de diálogo."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..config import settings
from ..pipeline import elenco as elenco_mod

router = APIRouter(prefix="/api/elenco", tags=["elenco"])


@router.get("")
def listar():
    return {"personagens": [p.as_dict() for p in elenco_mod.listar()],
            "lados": list(elenco_mod.SIDES),
            "tamanhos": list(elenco_mod.SIZES)}


@router.post("")
async def criar(
    name: str = Form(...),
    voice_id: str = Form(...),
    side: str = Form(elenco_mod.DEFAULT_SIDE),
    size: str = Form(elenco_mod.DEFAULT_SIZE),
    note: str = Form(""),
    image: UploadFile | None = File(None),
):
    """Nome, voz e cara — os três amarrados, que é o que o roteiro precisa
    para transformar "o Peter diz isso" em som e imagem."""
    stored = None
    if image is not None and image.filename:
        temp = settings.data_dir / "tmp"
        temp.mkdir(parents=True, exist_ok=True)
        stored = temp / image.filename
        stored.write_bytes(await image.read())
    try:
        person = elenco_mod.create(name, voice_id, stored, side, note, size)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if stored is not None:
            stored.unlink(missing_ok=True)
    return person.as_dict()


@router.get("/{person_id}/imagem")
def imagem(person_id: str):
    from fastapi.responses import FileResponse

    person = elenco_mod.get(person_id)
    if person is None or not person.image:
        raise HTTPException(404, "Este personagem não tem imagem")
    return FileResponse(person.image)


@router.delete("/{person_id}")
def apagar(person_id: str):
    if not elenco_mod.remove(person_id):
        raise HTTPException(404, "Personagem não encontrado")
    return {"deleted": True}
