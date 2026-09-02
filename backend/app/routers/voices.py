from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import db
from ..config import settings

router = APIRouter(prefix="/api/voices", tags=["voices"])


@router.get("")
def list_voices():
    return db.list_voices()


@router.get("/catalog/edge")
def edge_catalog(locale: str = "pt-BR"):
    from ..pipeline.tts import list_edge_voices
    return list_edge_voices(locale)


@router.get("/catalog/fish")
def fish_catalog(query: str = "", language: str = "pt"):
    """Catálogo de vozes do fish.audio para escolher pelo id."""
    from ..pipeline.tts import list_fish_voices

    try:
        return list_fish_voices(query=query, language=language)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@router.post("")
async def create_voice(
    name: str = Form(...),
    provider: str = Form("edge"),
    provider_voice_id: str = Form(""),
    rate: str = Form("+0%"),
    pitch: str = Form("+0Hz"),
    stability: float = Form(0.45),
    similarity_boost: float = Form(0.8),
    fish_model: str = Form("s2.1-pro"),
    speed: float = Form(1.0),
    sample: UploadFile | None = File(None),
):
    """Cadastra uma voz. Para clonagem de personagem (ex.: Seu Madruga):

    - provider=elevenlabs: crie a voz no painel da ElevenLabs e informe o voice_id
    - provider=xtts: envie um sample de 6-30s do personagem (clonagem local)
    """
    sample_path = ""
    if sample is not None and sample.filename:
        dest = settings.voices_dir / f"{db.new_id('sample')}_{sample.filename}"
        dest.write_bytes(await sample.read())
        sample_path = str(dest)

    if provider == "xtts" and not sample_path:
        raise HTTPException(400, "XTTS exige um arquivo de áudio de referência.")
    if provider == "elevenlabs" and not provider_voice_id:
        raise HTTPException(400, "ElevenLabs exige o provider_voice_id da voz clonada.")
    if provider == "fishaudio" and not provider_voice_id:
        raise HTTPException(
            400, "fish.audio exige o reference_id do modelo de voz "
                 "(copie da URL da voz em fish.audio).")

    voice_id = db.create_voice(
        name=name, provider=provider, provider_voice_id=provider_voice_id,
        sample_path=sample_path,
        settings_json={"rate": rate, "pitch": pitch,
                       "stability": stability, "similarity_boost": similarity_boost,
                       "fish_model": fish_model, "speed": speed},
    )
    return db.get_voice(voice_id)


@router.post("/{voice_id}/preview")
def preview(voice_id: str, text: str = Form("Testando a voz do personagem no ShortsCreator.")):
    from fastapi.responses import FileResponse

    from ..pipeline import tts

    voice = db.get_voice(voice_id)
    if voice is None:
        raise HTTPException(404, "Voz não encontrada")
    out = settings.voices_dir / f"preview_{voice_id}.mp3"
    voice_cfg = dict(voice)
    import json as _json
    extra = _json.loads(voice["settings_json"] or "{}")
    voice_cfg.update(extra)
    tts.synthesize(text, out, voice_cfg)
    return FileResponse(out, media_type="audio/mpeg")


@router.delete("/{voice_id}")
def delete_voice(voice_id: str):
    db.delete_voice(voice_id)
    return {"deleted": voice_id}
