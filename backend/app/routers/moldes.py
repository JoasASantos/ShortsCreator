"""Moldes: medir a forma de um vídeo e escrever outros com ela.

Analisar é caro (transcrição + detecção de cortes), então acontece uma vez e
fica guardado. Escrever variantes a partir de um molde já guardado é uma
chamada de LLM por variante — barato o bastante para tentar cinco assuntos e
ficar com dois.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..config import settings
from ..pipeline import ingest, molde as molde_mod
from ..schemas import JobInput
from . import uploads as uploads_router

router = APIRouter(prefix="/api/moldes", tags=["moldes"])

# Quantas variantes de uma vez. Cada uma é um roteiro e um render; vinte de
# uma vez é uma fila que ninguém vai assistir.
MAX_VARIANTS = 6


class AnalyzeRequest(BaseModel):
    source: str = Field(default="", description="Link do vídeo de referência")
    attachment: str = Field(default="", description="id de /api/uploads")
    name: str = Field(default="")


class VariantRequest(BaseModel):
    subjects: list[str] = Field(description="Um assunto por vídeo novo")
    instruction: str = ""
    language: str = "pt-BR"
    niche: str = "generico"
    voice_id: str | None = None
    caption_style: str = "karaoke"
    background: str = "auto"
    watermark: str = ""
    # Só escreve os roteiros e devolve, sem enfileirar render nenhum.
    dry_run: bool = False


@router.get("")
def listar():
    return {"moldes": [_summary(row) for row in db.list_moldes()]}


@router.get("/{molde_id}")
def um(molde_id: str):
    row = db.get_molde(molde_id)
    if row is None:
        raise HTTPException(404, "Molde não encontrado")
    return _full(row)


@router.delete("/{molde_id}")
def apagar(molde_id: str):
    if not db.delete_molde(molde_id):
        raise HTTPException(404, "Molde não encontrado")
    return {"deleted": True}


@router.post("/analisar")
def analisar(body: AnalyzeRequest):
    """Mede um vídeo de referência e guarda a forma.

    O vídeo em si não é guardado: o que fica é a contagem de batidas, palavras
    e cortes. Uma vez medido, a referência não é mais necessária.
    """
    work = settings.data_dir / "moldes"
    work.mkdir(parents=True, exist_ok=True)

    try:
        video, url = _resolve(body, work)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — download falhando
        raise HTTPException(400, f"Não deu para baixar o vídeo: {exc}") from exc

    try:
        template = molde_mod.analyze(video, name=body.name.strip(),
                                     source_url=url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — whisper/ffmpeg falhando
        raise HTTPException(502, f"A análise falhou: {exc}") from exc

    template.id = db.create_molde(template.name, template.source_url,
                                  template.seconds, template.words,
                                  template.as_dict())
    return template.as_dict()


@router.post("/{molde_id}/variantes")
def variantes(molde_id: str, body: VariantRequest):
    """Um roteiro novo por assunto, todos na mesma forma."""
    row = db.get_molde(molde_id)
    if row is None:
        raise HTTPException(404, "Molde não encontrado")

    subjects = [s.strip() for s in body.subjects if s.strip()][:MAX_VARIANTS]
    if not subjects:
        raise HTTPException(400, "Diga pelo menos um assunto.")

    template = _template(row)
    written, failed = [], []
    for subject in subjects:
        try:
            script = molde_mod.write_variant(
                template, subject, body.instruction, body.language, body.niche)
        except Exception as exc:  # noqa: BLE001 — um assunto, não a leva
            failed.append({"subject": subject, "error": str(exc)[:300]})
            continue
        entry = {"subject": subject, "title": script.title,
                 "text": molde_mod.as_script_text(script),
                 "segments": [s.model_dump() for s in script.segments],
                 "job_id": ""}
        if not body.dry_run:
            entry["job_id"] = _queue(script, template, body)
        written.append(entry)

    if not written:
        raise HTTPException(
            502, "Nenhuma variante foi escrita. " +
                 "; ".join(f"{f['subject']}: {f['error']}" for f in failed)[:400])
    return {"molde": molde_id, "variants": written, "failed": failed}


def _resolve(body: AnalyzeRequest, work: Path) -> tuple[Path, str]:
    """O arquivo a medir: um upload, ou um link baixado."""
    if body.attachment:
        return uploads_router.resolve(body.attachment), ""
    url = body.source.strip()
    if not url:
        raise FileNotFoundError("Mande um link ou um arquivo de referência.")
    if not ingest.is_url(url):
        raise FileNotFoundError(f"Isso não é um link: {url}")
    target = work / db.new_id("ref")
    target.mkdir(parents=True, exist_ok=True)
    path, _info = ingest.download_video(url, target)
    if path is None or not path.exists():
        raise FileNotFoundError(f"Nada foi baixado de {url}")
    return path, url


def _queue(script, template, body: VariantRequest) -> str:
    """A variante entra pelo caminho normal: um job com roteiro pronto.

    Sem pipeline paralelo — o editor, o QA, a capa e a publicação continuam
    funcionando sem saber que o roteiro veio de um molde.
    """
    from .. import worker

    job = JobInput(
        source_type="roteiro",
        source=molde_mod.as_script_text(script),
        language=body.language, niche=body.niche, voice_id=body.voice_id,
        duration=max(int(round(template.seconds)), 15),
        caption_style=body.caption_style, background=body.background,
        watermark=body.watermark,
    )
    job_id = db.create_job(job.model_dump())
    worker.enqueue(job_id)
    return job_id


def _summary(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"],
            "source_url": row["source_url"], "seconds": row["seconds"],
            "words": row["words"], "created_at": row["created_at"]}


def _full(row: dict) -> dict:
    import json

    data = json.loads(row["data_json"])
    data["id"] = row["id"]
    data["created_at"] = row["created_at"]
    return data


def _template(row: dict) -> molde_mod.Template:
    import json

    template = molde_mod.Template.from_dict(json.loads(row["data_json"]))
    template.id = row["id"]
    return template
