from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import FileResponse

from .. import db, worker
from ..config import settings
from ..pipeline import llm, orchestrator
from ..schemas import JobInput, ScriptEdit, ShortScript

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

ALLOWED_FILES = {
    "short.mp4": "video/mp4",
    "thumb.jpg": "image/jpeg",
    "cover.jpg": "image/jpeg",
    "preview.gif": "image/gif",
    "captions.srt": "text/plain; charset=utf-8",
    "captions.ass": "text/plain; charset=utf-8",
    "narration.mp3": "audio/mpeg",
    "script.json": "application/json",
    "background.mp4": "video/mp4",
}

# prévias de gancho: hook_0.mp3, hook_1.mp3...
_HOOK_FILE = re.compile(r"^hook_\d\.mp3$")


def _serialize(row: dict, with_metrics: dict | None = None) -> dict:
    out = dict(row)
    for key in ("input_json", "result_json", "qa_json"):
        raw = out.pop(key, None)
        out[key.replace("_json", "")] = json.loads(raw) if raw else None
    if with_metrics is not None:
        out["metrics"] = with_metrics.get(row["id"])
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
    rows = db.list_jobs(limit)
    metrics = db.metrics_by_jobs([r["id"] for r in rows])
    return [_serialize(row, metrics) for row in rows]


@router.get("/{job_id}")
def get_job(job_id: str):
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job não encontrado")
    out = _serialize(row)
    out["metrics"] = db.metrics_for_job(job_id)
    out["llm_calls"] = db.llm_calls_for_job(job_id)
    job_dir = settings.jobs_dir / job_id
    out["resumable_from"] = (orchestrator.resumable_stage(job_dir)
                             if job_dir.exists() else None)
    return out


@router.get("/{job_id}/events")
def get_events(job_id: str, after: int = 0):
    return db.get_events(job_id, after)


@router.post("/{job_id}/retry")
def retry_job(job_id: str, from_stage: str = Query("", alias="from")):
    """Reprocessa. Com `?from=voz|legendas|fundo|render` retoma da etapa
    indicada reaproveitando roteiro/narração já no disco — sem gastar LLM
    nem TTS de novo."""
    if db.get_job(job_id) is None:
        raise HTTPException(404, "Job não encontrado")
    if from_stage:
        try:
            orchestrator.request_resume(job_id, from_stage)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        db.update_job(job_id, error=None, qa_json=None)
    else:
        (settings.jobs_dir / job_id / "resume.json").unlink(missing_ok=True)
        db.update_job(job_id, error=None, result_json=None, qa_json=None)
    worker.enqueue(job_id)
    return {"job_id": job_id, "status": "queued", "from": from_stage or "início"}


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
    if filename in ALLOWED_FILES:
        media = ALLOWED_FILES[filename]
    elif _HOOK_FILE.match(filename):
        media = "audio/mpeg"
    else:
        raise HTTPException(404, "Arquivo não disponível")
    path: Path = settings.jobs_dir / job_id / filename
    if not path.exists():
        raise HTTPException(404, "Arquivo ainda não gerado")
    return FileResponse(path, media_type=media, filename=f"{job_id}_{filename}")


# ---------------------------------------------------------------- ganchos A/B

def _current_script(job_id: str) -> tuple[dict, JobInput, ShortScript, Path]:
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job não encontrado")
    job_dir = settings.job_dir(job_id)
    override = job_dir / "script_override.json"
    source = override if override.exists() else job_dir / "script.json"
    if not source.exists():
        raise HTTPException(400, "Este job ainda não tem roteiro.")
    job = JobInput(**json.loads(row["input_json"]))
    script = ShortScript(**json.loads(source.read_text(encoding="utf-8")))
    return row, job, script, job_dir


class HooksRequest(BaseModel):
    count: int = 3
    preview_audio: bool = True


@router.post("/{job_id}/hooks")
def build_hooks(job_id: str, request: HooksRequest | None = None):
    """Gera ganchos alternativos para o roteiro atual, com prévia em áudio de
    cada um na voz do job — só o gancho, poucos segundos de TTS por variante."""
    from ..pipeline import script as script_mod, tts as tts_mod

    request = request or HooksRequest()
    row, job, script, job_dir = _current_script(job_id)
    llm.current_job.set(job_id)

    try:
        hooks = script_mod.build_hook_variants(script, job, max(2, min(request.count, 5)))
    except Exception as exc:
        raise HTTPException(502, f"Falha ao gerar ganchos: {exc}")

    voice = db.get_voice(job.voice_id) if job.voice_id else None
    for index, hook in enumerate(hooks):
        hook["index"] = index
        hook["audio"] = None
        if not request.preview_audio:
            continue
        try:
            narration = tts_mod.synthesize(hook["text"], job_dir / f"hook_{index}.mp3", voice)
            hook["audio"] = f"/api/jobs/{job_id}/file/hook_{index}.mp3"
            hook["seconds"] = round(narration.duration, 2)
        except Exception as exc:  # noqa: BLE001 — prévia é opcional
            hook["audio_error"] = str(exc)[:160]

    current = next((s.text for s in script.segments if s.kind == "hook"), "")
    result = json.loads(row["result_json"] or "{}")
    result["hook_variants"] = {"current": current, "options": hooks}
    db.update_job(job_id, result_json=json.dumps(result))
    db.log_event(job_id, f"{len(hooks)} gancho(s) alternativo(s) gerado(s)")
    return result["hook_variants"]


class HookChoice(BaseModel):
    text: str
    render: bool = True


@router.post("/{job_id}/hooks/apply")
def apply_hook(job_id: str, choice: HookChoice):
    """Troca o gancho do roteiro pelo escolhido e re-renderiza este job."""
    from ..pipeline import script as script_mod

    if not choice.text.strip():
        raise HTTPException(400, "Gancho vazio.")
    row, job, script, job_dir = _current_script(job_id)
    updated = script_mod.with_hook(script, choice.text)
    (job_dir / "script_override.json").write_text(updated.model_dump_json(indent=2),
                                                  encoding="utf-8")
    result = json.loads(row["result_json"] or "{}")
    result["script"] = updated.model_dump()
    db.update_job(job_id, result_json=json.dumps(result), error=None,
                  **({"qa_json": None} if choice.render else {}))
    db.log_event(job_id, f"Gancho trocado: {choice.text[:100]}")
    if choice.render:
        orchestrator.request_resume(job_id, "voz")   # roteiro pronto: pula o LLM
        worker.enqueue(job_id)
    return {"job_id": job_id, "script": updated.model_dump(), "rendering": choice.render}


@router.post("/{job_id}/hooks/fork")
def fork_with_hook(job_id: str, choice: HookChoice):
    """Cria um NOVO job idêntico com outro gancho — o par A/B. Publique os dois
    e compare em Desempenho."""
    from ..pipeline import script as script_mod

    if not choice.text.strip():
        raise HTTPException(400, "Gancho vazio.")
    row, job, script, job_dir = _current_script(job_id)
    variant = script_mod.with_hook(script, choice.text)

    new_id = db.create_job(job.model_dump(), f"{variant.title} (B)")
    new_dir = settings.job_dir(new_id)
    (new_dir / "script_override.json").write_text(variant.model_dump_json(indent=2),
                                                  encoding="utf-8")
    # reaproveita o vídeo já baixado (source.<ext> + source.info.json) em vez
    # de puxar do YouTube outra vez
    for src in job_dir.glob("source.*"):
        shutil.copy(src, new_dir / src.name)
    db.log_event(new_id, f"Variante A/B de {job_id} com gancho: {choice.text[:100]}")
    db.log_event(job_id, f"Variante A/B criada: {new_id}")
    worker.enqueue(new_id)
    return {"job_id": new_id, "parent": job_id}


# ---------------------------------------------------------------------- capa

class CoverRequest(BaseModel):
    title: str = ""
    at: float | None = None      # segundo do frame; None = escolhe automático


@router.post("/{job_id}/cover")
def rebuild_cover(job_id: str, request: CoverRequest | None = None):
    from ..pipeline import cover as cover_mod, render as render_mod

    request = request or CoverRequest()
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job não encontrado")
    job_dir = settings.job_dir(job_id)
    video = job_dir / "short.mp4"
    if not video.exists():
        raise HTTPException(400, "Vídeo ainda não renderizado")

    job = JobInput(**json.loads(row["input_json"]))
    result = json.loads(row["result_json"] or "{}")
    title = request.title.strip() or result.get("title") or row["title"] or "Short"
    duration = float(result.get("duration") or render_mod.probe_duration(video))

    try:
        if request.at is not None:
            work = job_dir / "cover_frames"
            work.mkdir(exist_ok=True)
            frame = work / "chosen.jpg"
            cover_mod._extract(video, max(request.at, 0.0), frame)  # noqa: SLF001
            from PIL import Image

            image = Image.open(frame).convert("RGB").resize((settings.width, settings.height))
            cover_mod._compose(image, title, job.niche).save(  # noqa: SLF001
                job_dir / "cover.jpg", "JPEG", quality=92)
            at = request.at
        else:
            _, at = cover_mod.build(video, title, job.niche, job_dir / "cover.jpg",
                                    duration, job_dir)
    except Exception as exc:
        raise HTTPException(500, f"Falha ao gerar a capa: {exc}")

    (job_dir / "cover.json").write_text(json.dumps({"at": at}), encoding="utf-8")
    result["cover"] = f"/api/jobs/{job_id}/file/cover.jpg"
    result["cover_at"] = at
    db.update_job(job_id, result_json=json.dumps(result))
    db.log_event(job_id, f"Capa refeita (frame em {at:.1f}s)")
    return {"cover": result["cover"], "at": at}


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
    # o que estava em rascunho acabou de virar a versão oficial
    (settings.job_dir(job_id) / "draft.json").unlink(missing_ok=True)
    worker.enqueue(job_id)
    return {"job_id": job_id, "status": "queued", "applied": sorted(changes)}


@router.delete("/{job_id}/edit")
def reset_edit(job_id: str):
    """Descarta o roteiro editado e volta a gerar pelo LLM."""
    job_dir = settings.job_dir(job_id)
    (job_dir / "script_override.json").unlink(missing_ok=True)
    (job_dir / "draft.json").unlink(missing_ok=True)
    return {"job_id": job_id, "reset": True}


# ------------------------------------------------------------------ rascunho
# O editor guarda o que está sendo digitado aqui, sem renderizar nada. É o que
# permite fechar a aba (ou trocar de máquina) e voltar de onde parou — antes o
# texto só existia no estado do React e sumia junto com a tela.

@router.get("/{job_id}/draft")
def get_draft(job_id: str):
    if db.get_job(job_id) is None:
        raise HTTPException(404, "Job não encontrado")
    path = settings.job_dir(job_id) / "draft.json"
    if not path.exists():
        return {"draft": None}
    try:
        return {"draft": json.loads(path.read_text(encoding="utf-8"))}
    except json.JSONDecodeError:
        path.unlink(missing_ok=True)
        return {"draft": None}


@router.put("/{job_id}/draft")
def save_draft(job_id: str, draft: dict = Body(...)):
    if db.get_job(job_id) is None:
        raise HTTPException(404, "Job não encontrado")
    draft = {k: v for k, v in draft.items() if k != "saved_at"}
    draft["saved_at"] = db.now()
    (settings.job_dir(job_id) / "draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return {"saved_at": draft["saved_at"]}


@router.delete("/{job_id}/draft")
def delete_draft(job_id: str):
    (settings.job_dir(job_id) / "draft.json").unlink(missing_ok=True)
    return {"job_id": job_id, "discarded": True}


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
    llm.current_job.set(job_id)

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
    llm.current_job.set(job_id)

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
