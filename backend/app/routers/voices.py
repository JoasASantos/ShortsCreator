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


@router.get("/catalog/fish/{reference_id}/sample")
def fish_sample(reference_id: str):
    """Proxies the fish.audio audio sample.

    The original URL is signed and comes from another domain; serving it from
    here avoids a CORS problem in the player and keeps the API key out of the
    browser.
    """
    import httpx as _httpx
    from fastapi.responses import StreamingResponse

    from ..pipeline.tts import fish_sample_url

    url = fish_sample_url(reference_id)
    if not url:
        raise HTTPException(404, "This voice has no published sample.")

    upstream = _httpx.get(url, timeout=60, follow_redirects=True)
    if upstream.status_code != 200:
        raise HTTPException(502, "Could not download the sample.")
    return StreamingResponse(iter([upstream.content]), media_type="audio/mpeg")


@router.get("/presets")
def voice_presets():
    """Catalog curated by use: narration, cinema, characters and general voices."""
    from ..pipeline.voice_presets import all_presets

    installed = {v["provider_voice_id"] for v in db.list_voices()
                 if v["provider"] == "fishaudio"}
    return [{**v, "installed": v["id"] in installed} for v in all_presets()]


@router.post("/presets/{reference_id}/install")
def install_preset(reference_id: str):
    """Creates the local voice pointing at a fish.audio preset."""
    from ..pipeline.voice_presets import find

    preset = find(reference_id)
    if preset is None:
        raise HTTPException(404, "Preset not found")

    for existing in db.list_voices():
        if existing["provider_voice_id"] == reference_id:
            return existing

    voice_id = db.create_voice(
        name=preset["name"], provider="fishaudio",
        provider_voice_id=reference_id,
        settings_json={"fish_model": settings.fishaudio_backend,
                       "note": preset.get("note", "")},
    )
    return db.get_voice(voice_id)


@router.get("/catalog/fish")
def fish_catalog(query: str = "", language: str = "pt"):
    """fish.audio voice catalog, to pick one by id."""
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
    """Registers a voice. For character cloning (e.g. Seu Madruga):

    - provider=elevenlabs: create the voice in the ElevenLabs dashboard and
      supply its voice_id
    - provider=xtts: upload a 6-30s sample of the character (local cloning)
    """
    sample_path = ""
    if sample is not None and sample.filename:
        dest = settings.voices_dir / f"{db.new_id('sample')}_{sample.filename}"
        dest.write_bytes(await sample.read())
        sample_path = str(dest)

    if provider == "xtts" and not sample_path:
        raise HTTPException(400, "XTTS requires a reference audio file.")
    if provider == "elevenlabs" and not provider_voice_id:
        raise HTTPException(400, "ElevenLabs requires the provider_voice_id of the cloned voice.")
    if provider == "fishaudio" and not provider_voice_id:
        raise HTTPException(
            400, "fish.audio requires the reference_id of the voice model "
                 "(copy it from the voice's URL on fish.audio).")

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
        raise HTTPException(404, "Voice not found")
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
