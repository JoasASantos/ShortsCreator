from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import FileResponse

from .. import db, worker
from ..config import settings
from ..schemas import JobInput, ScriptEdit, ShortScript

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

ALLOWED_FILES = {
    "short.mp4": "video/mp4",
    "thumb.jpg": "image/jpeg",
    "captions.srt": "text/plain; charset=utf-8",
    "captions.ass": "text/plain; charset=utf-8",
    "narration.mp3": "audio/mpeg",
    "script.json": "application/json",
    "background.mp4": "video/mp4",
}


def _serialize(row: dict) -> dict:
    out = dict(row)
    for key in ("input_json", "result_json", "qa_json"):
        raw = out.pop(key, None)
        out[key.replace("_json", "")] = json.loads(raw) if raw else None
    return out


@router.post("")
def create_job(job: JobInput):
    if job.source_type == "imagem" and not job.attachments:
        raise HTTPException(400, "Envie ao menos uma imagem antes de criar o job.")
    has_source = bool(job.source.strip()) or bool(job.attachments)
    if job.source_type != "imagem" and not has_source:
        raise HTTPException(400, "Informe uma URL, tema, texto, repositório ou envie um arquivo.")
    job_id = db.create_job(job.model_dump())
    worker.enqueue(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("")
def list_jobs(limit: int = Query(100, le=500)):
    return [_serialize(row) for row in db.list_jobs(limit)]


@router.get("/{job_id}")
def get_job(job_id: str):
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job não encontrado")
    return _serialize(row)


@router.get("/{job_id}/events")
def get_events(job_id: str, after: int = 0):
    return db.get_events(job_id, after)


@router.post("/{job_id}/retry")
def retry_job(job_id: str):
    if db.get_job(job_id) is None:
        raise HTTPException(404, "Job não encontrado")
    db.update_job(job_id, error=None, result_json=None, qa_json=None)
    worker.enqueue(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.delete("/{job_id}")
def delete_job(job_id: str):
    db.delete_job(job_id)
    job_dir = settings.jobs_dir / job_id
    if job_dir.exists():
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)
    (settings.outputs_dir / f"{job_id}.mp4").unlink(missing_ok=True)
    return {"deleted": job_id}


@router.get("/{job_id}/file/{filename}")
def get_file(job_id: str, filename: str):
    if filename not in ALLOWED_FILES:
        raise HTTPException(404, "Arquivo não disponível")
    path: Path = settings.jobs_dir / job_id / filename
    if not path.exists():
        raise HTTPException(404, "Arquivo ainda não gerado")
    return FileResponse(path, media_type=ALLOWED_FILES[filename],
                        filename=f"{job_id}_{filename}")


@router.post("/{job_id}/edit")
def edit_job(job_id: str, edit: ScriptEdit):
    """Aplica edições manuais e re-renderiza.

    O roteiro editado é gravado como override, então a re-renderização não
    chama o LLM de novo — o texto que você escreveu é exatamente o que vai
    ser narrado.
    """
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job não encontrado")

    job = JobInput(**json.loads(row["input_json"]))
    changes = edit.model_dump(exclude_none=True)

    if edit.segments is not None:
        if not edit.segments:
            raise HTTPException(400, "O roteiro precisa de ao menos um segmento.")
        current = json.loads(row["result_json"] or "{}").get("script", {})
        script = ShortScript(
            title=edit.title or current.get("title") or row["title"] or "Short",
            description=current.get("description", ""),
            hashtags=current.get("hashtags", []),
            segments=edit.segments,
            estimated_seconds=job.duration,
        )
        (settings.job_dir(job_id) / "script_override.json").write_text(
            script.model_dump_json(indent=2), encoding="utf-8")

    for field in ("voice_id", "caption_style", "caption_position", "caption_offset",
                  "music", "music_track", "music_volume", "watermark",
                  "background", "background_query", "scroll"):
        if field in changes:
            setattr(job, field, changes[field])

    db.update_job(job_id, input_json=json.dumps(job.model_dump()),
                  error=None, qa_json=None)
    worker.enqueue(job_id)
    return {"job_id": job_id, "status": "queued", "applied": sorted(changes)}


@router.delete("/{job_id}/edit")
def reset_edit(job_id: str):
    """Descarta o roteiro editado e volta a gerar pelo LLM."""
    override = settings.job_dir(job_id) / "script_override.json"
    override.unlink(missing_ok=True)
    return {"job_id": job_id, "reset": True}


class ScriptPrompt(BaseModel):
    instruction: str
    render: bool = True


@router.post("/{job_id}/script/prompt")
def refine_script(job_id: str, request: ScriptPrompt):
    """Reescreve o roteiro atual a partir de uma instrução em linguagem natural.

    Ex.: "deixa o hook mais agressivo", "corta pela metade", "tira o jargão
    técnico". O resultado vira o roteiro manual do job, então a renderização
    seguinte usa exatamente esse texto.
    """
    from ..pipeline import script as script_mod

    if not request.instruction.strip():
        raise HTTPException(400, "Escreva o que você quer mudar no roteiro.")

    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job não encontrado")

    job_dir = settings.job_dir(job_id)
    override = job_dir / "script_override.json"
    base = job_dir / "script.json"
    source = override if override.exists() else base
    if not source.exists():
        raise HTTPException(400, "Este job ainda não tem roteiro.")

    job = JobInput(**json.loads(row["input_json"]))
    current = ShortScript(**json.loads(source.read_text(encoding="utf-8")))

    try:
        updated = script_mod.refine_script(current, request.instruction, job)
    except Exception as exc:
        raise HTTPException(502, f"Falha ao refinar o roteiro: {exc}")

    override.write_text(updated.model_dump_json(indent=2), encoding="utf-8")

    # O editor e a interface leem o roteiro de result_json. Sem atualizar aqui,
    # o texto refinado só existia no disco: qualquer recarga da página voltava a
    # mostrar o roteiro antigo — e salvar por cima descartaria o refinamento.
    result = json.loads(row["result_json"] or "{}")
    result["script"] = updated.model_dump()
    result["title"] = updated.title
    result["description"] = updated.description
    result["hashtags"] = updated.hashtags

    db.update_job(job_id, title=updated.title,
                  result_json=json.dumps(result),
                  error=None, **({"qa_json": None} if request.render else {}))
    db.log_event(job_id, f"Roteiro refinado por prompt: {request.instruction[:120]}")

    if request.render:
        worker.enqueue(job_id)

    return {"job_id": job_id, "script": updated.model_dump(),
            "rendering": request.render}


class CaptionRequest(BaseModel):
    instruction: str = ""


@router.post("/{job_id}/caption")
def build_caption(job_id: str, request: CaptionRequest | None = None):
    """Gera o texto de publicação (título, descrição, hashtags) a partir do roteiro."""
    from ..pipeline import script as script_mod

    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job não encontrado")
    if not row["result_json"]:
        raise HTTPException(400, "O short ainda não foi gerado.")

    job_dir = settings.job_dir(job_id)
    override = job_dir / "script_override.json"
    source = override if override.exists() else job_dir / "script.json"
    if not source.exists():
        raise HTTPException(400, "Este job não tem roteiro.")

    job = JobInput(**json.loads(row["input_json"]))
    script = ShortScript(**json.loads(source.read_text(encoding="utf-8")))

    try:
        caption = script_mod.build_post_caption(
            script, job, (request.instruction if request else ""))
    except Exception as exc:
        raise HTTPException(502, f"Falha ao gerar a legenda do post: {exc}")

    result = json.loads(row["result_json"])
    result["caption"] = caption
    db.update_job(job_id, result_json=json.dumps(result))
    db.log_event(job_id, "Legenda de publicação gerada")
    return caption


@router.get("/{job_id}/timeline")
def get_timeline(job_id: str):
    """Linha do tempo editável do short (clipes, áudio e legendas)."""
    from ..pipeline import timeline as timeline_mod

    job_dir = settings.job_dir(job_id)
    edl = timeline_mod.load(job_dir)
    if edl is None:
        # Jobs renderizados antes do editor existir não têm timeline.json.
        # Reconstruímos a partir dos arquivos que já estão no disco para não
        # transformar produções antigas em beco sem saída.
        edl = _rebuild_timeline(job_id, job_dir)
        if edl is None:
            raise HTTPException(404, "Este job ainda não tem linha do tempo.")
        timeline_mod.save(job_dir, edl)
    return edl.to_dict()


def _rebuild_timeline(job_id: str, job_dir: Path):
    from ..pipeline import timeline as timeline_mod

    row = db.get_job(job_id)
    if row is None or not row["result_json"]:
        return None

    result = json.loads(row["result_json"])
    words = result.get("words") or []
    narration = job_dir / "narration.mp3"
    if not words or not narration.exists():
        return None

    job = JobInput(**json.loads(row["input_json"]))
    parts = (sorted(job_dir.glob("hl_*.mp4")) or sorted(job_dir.glob("kb_*.mp4"))
             or sorted(job_dir.glob("bgpart_*.mp4")))
    if not parts:
        for name in ("background_scroll.mp4", "background.mp4"):
            if (job_dir / name).exists():
                parts = [job_dir / name]
                break
    if not parts:
        return None

    from ..pipeline import tts as tts_mod
    return timeline_mod.build_from_job(
        job_dir, words, tts_mod.audio_duration(narration), parts,
        job.caption_style, job.caption_position, job.watermark,
        music=next(iter(job_dir.glob("music.*")), None) if job.music else None,
        music_gain=job.music_volume,
    )


@router.put("/{job_id}/timeline")
def save_timeline(job_id: str, data: dict):
    """Salva a linha do tempo sem renderizar — usado no autosave do editor."""
    from ..pipeline import timeline as timeline_mod

    job_dir = settings.job_dir(job_id)
    edl = timeline_mod.Timeline.from_dict(data).normalize()
    timeline_mod.save(job_dir, edl)
    return edl.to_dict()


@router.post("/{job_id}/timeline/render")
def render_timeline(job_id: str, data: dict | None = None):
    """Recompila o vídeo a partir da linha do tempo e reaudita no QA.

    Não passa pelo LLM nem pelo TTS: usa exatamente os arquivos já existentes,
    recortados e posicionados conforme o editor.
    """
    from ..pipeline import qa as qa_mod, render as render_mod
    from ..pipeline import timeline as timeline_mod, timeline_render

    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job não encontrado")

    job_dir = settings.job_dir(job_id)
    edl = (timeline_mod.Timeline.from_dict(data) if data
           else timeline_mod.load(job_dir))
    if edl is None:
        raise HTTPException(400, "Sem linha do tempo para renderizar.")
    edl.normalize()
    timeline_mod.save(job_dir, edl)

    events: list[str] = []
    try:
        final = job_dir / "short.mp4"
        timeline_render.render_timeline(job_dir, edl, final,
                                        log=lambda m: events.append(m))
        render_mod.make_thumbnail(final, job_dir / "thumb.jpg",
                                  at=min(1.0, edl.duration / 4))
        shutil.copy(final, settings.outputs_dir / f"{job_id}.mp4")
    except Exception as exc:
        raise HTTPException(500, f"Falha ao renderizar a linha do tempo: {exc}")

    for message in events:
        db.log_event(job_id, message)
    db.log_event(job_id, "Vídeo remontado pelo editor de linha do tempo")

    report = qa_mod.audit(final, None)
    db.log_event(job_id,
                 f"QA: score {report.score}/100 — "
                 f"{'APROVADO' if report.passed else 'REPROVADO'}")

    result = json.loads(row["result_json"] or "{}")
    result["duration"] = round(edl.duration, 2)
    result["words"] = timeline_mod.words_from_captions(edl.captions)
    db.update_job(job_id, result_json=json.dumps(result),
                  qa_json=report.model_dump_json())
    return {"job_id": job_id, "duration": edl.duration,
            "qa": report.model_dump()}


@router.post("/{job_id}/qa")
def rerun_qa(job_id: str):
    from ..pipeline import qa as qa_mod

    job_dir = settings.jobs_dir / job_id
    video = job_dir / "short.mp4"
    if not video.exists():
        raise HTTPException(400, "Vídeo ainda não renderizado")
    report = qa_mod.audit(video, job_dir / "captions.ass")
    db.update_job(job_id, qa_json=report.model_dump_json())
    return report
