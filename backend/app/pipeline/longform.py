"""Long-form productions: documentaries, mini-docs, short films, mini-series.

A short condenses one source into ninety seconds. A film (`story`) invents its
footage from a premise. This module is the third shape: five to thirty minutes
assembled from the user's OWN material — interviews they recorded, links, sites,
images — plus stock footage, with the AI deciding the edit. The user brings the
subject, the tone and the raw material; the model does the research, writes the
script and decides what is on screen at every second.

Like the film studio it is a staged pipeline, and for the same reason: half an
hour of narration, dozens of stock downloads and a long render cost real time
and money, so every decision is written down where the user can read it, fix it
and only then pay for the next one.

    material        what the user gave, ingested and transcribed with timestamps
        -> briefing         research brief: facts, themes, usable interview
                            moments, what is missing and the stock that covers it
        -> roteiro          the script, in blocks, to a words-per-second budget
        -> plano_de_edicao  for every block: what is on screen and when
        -> montagem         narration + cuts + stock + cards -> Timeline -> MP4

Two rules run through every stage. Timestamps are never taken on trust: a
reference to a material that does not exist, or to seconds outside its
transcript, is rejected in code and reported — never silently kept, because a
quote placed at the wrong second is the one mistake a documentary cannot
afford. And reference examples the user supplies are structure only: the
prompts say so, and the plan validator refuses to put them on screen.

The finished production is a normal job (one per episode for a series), in a
HORIZONTAL `Timeline`, so the editor, QA and publishing work on it unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .. import db
from ..config import settings
from ..routers import uploads as uploads_router
from ..schemas import JobInput
from . import (avatar as avatar_mod, broll, captions as captions_mod, clipper,
               formats, imagegen, ingest, livecuts, llm, notify, overlays,
               qa as qa_mod, reels, render, timeline as timeline_mod,
               timeline_render, tts)
from .generators import heygen
from .script import language_name
from .timeline import AudioClip, CaptionCue, MediaOverlay, Timeline, VideoClip

# What the jobs produced here carry in `result_json.mode`, the way a reel or a
# film marks itself. The dashboard reads it to tell the shapes apart.
MODE = "longform"

# The frame. Every timeline built here is 16:9; a documentary on a phone frame
# is a documentary watched sideways.
FRAME = formats.HORIZONTAL

# The development stages, in order. The URL segment is the stage name. The
# first is ingestion, not an LLM call — it is a stage all the same because the
# user has to be able to inspect and correct the catalog (a mislabelled
# interview, a link that failed) before anything is written against it.
STAGES = ("material", "briefing", "roteiro", "plano_de_edicao")

STAGE_COLUMN = {
    "material": "material_json",
    "briefing": "briefing_json",
    "roteiro": "roteiro_json",
    "plano_de_edicao": "plano_json",
}

# Stages invalidated when a stage is (re)written — the same cascade the film
# studio applies. New material changes what the briefing can cite; a new
# briefing changes what the script is built on; a new script leaves the edit
# plan pointing at blocks that no longer exist. The derived documents are
# dropped rather than left silently inconsistent, and the API says which.
CASCADE = {
    "material": ("briefing", "roteiro", "plano_de_edicao"),
    "briefing": ("roteiro", "plano_de_edicao"),
    "roteiro": ("plano_de_edicao",),
    "plano_de_edicao": (),
}

# The content types, each with the structure the model is told to follow.
# `minutes` is the target range for one production — for a series, for one
# episode.
TYPES: dict[str, dict] = {
    "documentario": {
        "label": "Documentário", "fiction": False,
        "min_minutes": 15, "max_minutes": 30, "default_minutes": 20,
        "structure": (
            "Abertura com gancho (o que está em jogo, em até 60 segundos) → "
            "contexto (quem, onde, por quê) → 3 a 5 atos temáticos, cada um "
            "sustentado por entrevista e fechado pela narração → conclusão que "
            "responde à pergunta da abertura. As entrevistas carregam a obra; a "
            "narração faz as pontes."),
    },
    "mini_documentario": {
        "label": "Mini-documentário", "fiction": False,
        "min_minutes": 5, "max_minutes": 10, "default_minutes": 7,
        "structure": (
            "A mesma espinha do documentário, comprimida: gancho em 20 segundos, "
            "contexto em um bloco, 2 ou 3 atos curtos, conclusão direta. Menos "
            "narração, mais fala de quem viveu o assunto."),
    },
    "curta": {
        "label": "Curta-metragem", "fiction": True,
        "min_minutes": 5, "max_minutes": 15, "default_minutes": 8,
        "structure": (
            "Ficção em três atos: apresentação (mundo, protagonista, o que ele "
            "quer), confronto (o obstáculo cresce, virada no meio) e resolução "
            "(o preço pago). O tom manda: terror constrói silêncio e sugestão "
            "antes de mostrar; drama constrói escolha; comédia constrói ritmo. "
            "Feito do material real do usuário, imagens de banco e cartelas — "
            "sem depender de nenhum gerador de vídeo."),
    },
    "mini_serie": {
        "label": "Mini-série", "fiction": False,
        "min_minutes": 5, "max_minutes": 15, "default_minutes": 8,
        "structure": (
            "Arco da série em N episódios, cada um um mini-documentário ou curta "
            "completo: abertura própria, recap do episódio anterior (a partir do "
            "segundo), atos e um gancho para o próximo (menos no último). O arco "
            "avança a cada episódio — nada de repetir o mesmo assunto com outras "
            "palavras."),
    },
}

MIN_EPISODES = 2
MAX_EPISODES = 12

# Who speaks. `oculto` is a voice-over; `avatar` a HeyGen presenter reading the
# narration blocks; `sem_narracao` leaves the interviews and the cards to carry
# the whole thing.
NARRATORS = ("oculto", "avatar", "sem_narracao")

NARRATOR_DESCRIPTION = {
    "oculto": "um narrador em off — voz sem rosto, sobre a imagem",
    "avatar": "um apresentador em quadro, que fala para a câmera",
    "sem_narracao": "ninguém — não há narrador; entrevistas e cartelas sustentam a obra",
}

# Tone presets. Free text is accepted as well; a preset is only a longer way of
# saying it that the model reads more reliably than one word.
TONE_PRESETS = {
    "investigativo": ("investigativo: dúvida metódica, fatos encadeados, perguntas "
                      "que a narração deixa no ar e a entrevista responde"),
    "jornalistico": ("jornalístico: sóbrio, factual, sem adjetivos, fontes "
                     "nomeadas, a narração informa e não opina"),
    "terror": ("terror: silêncios longos, sugestão antes de mostrar, narração "
               "baixa e lenta, tensão que cresce sem alívio"),
    "dramatico": ("dramático: emoção contida, pausas, foco nas pessoas e nas "
                  "escolhas, a imagem fala antes do texto"),
    "educativo": ("educativo: claro, didático, um conceito por vez, exemplos "
                  "concretos, recapitula antes de avançar"),
    "inspirador": ("inspirador: arco de superação, narração calorosa, fechamento "
                   "que aponta para frente"),
}

BLOCK_KINDS = ("abertura", "contexto", "ato", "entrevista", "transicao",
               "conclusao", "recap", "gancho")

SHOT_KINDS = ("entrevista", "link", "stock", "imagem", "avatar", "cartela")

# Material kinds. `referencia` is not a kind: any item can be a reference, and
# a reference is never content — see `reference` on the item.
MATERIAL_KINDS = ("entrevista", "link", "artigo", "imagem")

# The narration budget, in words per second of block time.
#
# `script.WORDS_PER_SECOND` is 2.0, measured end to end on a short: the voice
# says ~2.9 words a second and stops for most of a second at every full stop,
# and a short is written in one-idea sentences, so the pauses eat a third of
# the budget. Documentary narration is slower still, for a different reason:
# it is not wall-to-wall. It states a fact and lets the picture hold; it stops
# before an interview and starts again after. Roughly a fifth of a narrated
# block is meant to be air — footage with no voice over it — so the budget is
# the short's rate with that air taken out: 2.0 * 0.8. Writing to 2.0 here
# produces narration that never breathes and a block that runs long against
# its footage; the assembly then has to hold the last shot to cover it.
NARRATION_WORDS_PER_SECOND = 1.6

MIN_BLOCK_SECONDS = 4
MAX_BLOCK_SECONDS = 600
MIN_SHOT_SECONDS = 2.0
# A usable interview moment: shorter than this is a fragment out of context;
# longer is a scene, and the plan should cut it into more than one shot.
MIN_MOMENT_SECONDS = 3.0
MAX_MOMENT_SECONDS = 180.0

# How much transcript the briefing prompt is allowed to carry, in characters,
# across every material. A 40-minute interview is ~40k characters on its own;
# past the budget an item is condensed into its best stretches by the windowed
# clipper instead of being truncated — truncation is how every quote ends up
# coming from the first ten minutes.
BRIEFING_CHAR_BUDGET = 48000
MIN_ITEM_CHARS = 4000

# The production's job while it is being assembled. It must NOT be "queued" or
# "running": `worker.start()` requeues jobs in those states after a restart and
# the shorts orchestrator would run its own pipeline over the documentary's
# directory. The same word the film studio uses, so the dashboard reads both
# the same way.
JOB_STATUS_ASSEMBLING = "gerando"

# Hard ceiling on blocks for one episode. Not technical: at 20s a block this is
# already an hour of footage, and a model that answers with 400 blocks is about
# to synthesize somebody's whole afternoon.
MAX_BLOCKS = 200


class StageNotReady(RuntimeError):
    """A stage was asked for before the stage it is built on exists."""


class MaterialUnusable(RuntimeError):
    """Nothing the user sent could be ingested — there is no production to make."""


def _in_language(template: str, tag: str) -> str:
    """Injects the language into the prompt.

    `str.format` is no good here: these prompts carry JSON examples, and the `{`
    braces get read as placeholders — hence replacing the marker directly.
    """
    return template.replace("{language}", language_name(tag))


def _hash(text: str) -> str:
    """Fingerprint of a shot spec or a narration line, so a resume can tell
    whether what is already on disk still matches what is asked for now."""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def _fmt_time(seconds: float) -> str:
    minutes, secs = divmod(int(max(seconds, 0)), 60)
    return f"{minutes:02d}:{secs:02d}"


def project_dir(project_id: str) -> Path:
    """Where the ingested material lives.

    Not a job directory: the job is created per episode at assembly time, and
    the material has to exist long before that — and survive the job being
    deleted and regenerated. Inside DATA_DIR so a backup takes it along.
    """
    d = settings.data_dir / "longform" / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def options_of(project: dict) -> dict:
    """The production's settings with the defaults filled in."""
    raw = project.get("options_json") or "{}"
    stored = json.loads(raw) if isinstance(raw, str) else dict(raw)
    kind = project.get("type") or stored.get("type") or "documentario"
    spec = TYPES.get(kind, TYPES["documentario"])
    return {
        "type": kind,
        "language": stored.get("language", "pt-BR"),
        "niche": stored.get("niche", "generico"),
        "tone": stored.get("tone", "investigativo"),
        "style": stored.get("style", ""),
        "narrator": stored.get("narrator", "oculto"),
        "voice_id": stored.get("voice_id"),
        "avatar_id": stored.get("avatar_id", ""),
        "avatar_voice_id": stored.get("avatar_voice_id", ""),
        "target_minutes": int(stored.get("target_minutes") or spec["default_minutes"]),
        "episodes": int(stored.get("episodes") or 1) if kind == "mini_serie" else 1,
        # A documentary is watched, not scrolled past: block captions low on
        # the frame read as subtitles, which is what it wants.
        "caption_style": stored.get("caption_style", "bloco"),
        "caption_position": stored.get("caption_position", "baixo"),
        "captions": bool(stored.get("captions", True)),
        "lower_thirds": bool(stored.get("lower_thirds", True)),
        "watermark": stored.get("watermark", ""),
        "sources": stored.get("sources") or {"links": [], "attachments": [],
                                             "references": []},
    }


def tone_text(options: dict) -> str:
    tone = (options.get("tone") or "").strip()
    return TONE_PRESETS.get(tone.lower(), tone) or TONE_PRESETS["investigativo"]


def document(project: dict, stage: str) -> dict | None:
    raw = project.get(STAGE_COLUMN[stage])
    if not raw:
        return None
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def jobs_of(project: dict) -> dict[str, str]:
    """Episode number (as a string key) -> job id."""
    raw = project.get("jobs_json") or "{}"
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


# --------------------------------------------------------------------------
# Stage 1 — the material (ingestion, not an LLM call)
# --------------------------------------------------------------------------

def ingest_material(project_id: str) -> dict:
    """Downloads, copies and transcribes everything the user gave.

    Runs on the worker's long-form queue: an interview is transcribed with word
    timestamps, and forty minutes of that on a CPU is longer than any HTTP
    request should be held open. Progress goes to `job_events` under the
    project id, so the screen can follow it before a job exists.

    Each item is ingested on its own and a failure is written on the item, not
    raised: losing one link out of five must not lose the interviews already
    transcribed. Only when NOTHING usable came through is it an error.
    """
    row = db.get_longform(project_id)
    if row is None:
        raise RuntimeError(f"Production {project_id} does not exist")
    options = options_of(row)
    sources = options["sources"]
    work = project_dir(project_id)

    def log(message: str, level: str = "info") -> None:
        db.log_event(project_id, message, level)

    db.update_longform(project_id, status="ingesting", stage="material", error=None)
    items: list[dict] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"m{counter:02d}"

    for url in ingest.split_urls("\n".join(sources.get("links") or [])):
        items.append(_ingest_link(next_id(), url, work, False, log))
    for attachment in sources.get("attachments") or []:
        items.append(_ingest_upload(next_id(), attachment, work, False, log))
    for reference in sources.get("references") or []:
        reference = str(reference).strip()
        if not reference:
            continue
        if ingest.is_url(reference):
            items.append(_ingest_link(next_id(), reference, work, True, log))
        else:
            items.append(_ingest_upload(next_id(), reference, work, True, log))

    usable = [i for i in items if i["status"] == "ok" and not i["reference"]]
    doc = {"items": items, "ingested_at": db.now()}
    if not usable:
        reasons = "; ".join(f"{i['id']}: {i['error']}" for i in items if i["error"])
        db.update_longform(project_id, status="error",
                           material_json=json.dumps(doc, ensure_ascii=False),
                           error="None of the material could be used. " + reasons)
        raise MaterialUnusable(
            "None of the material could be used — a reference example alone is "
            "not content. " + reasons)

    invalidated = save_stage(row, "material", doc)
    if invalidated:
        log(f"Material re-ingested; {', '.join(invalidated)} dropped and must "
            f"be developed again.", "warn")
    log(f"Material: {len(usable)} usable item(s), "
        f"{sum(1 for i in items if i['reference'])} reference(s), "
        f"{sum(1 for i in items if i['status'] != 'ok')} failure(s)")
    return doc


def _blank_item(item_id: str, kind: str, reference: bool) -> dict:
    return {"id": item_id, "kind": kind, "reference": reference, "title": "",
            "url": "", "file": "", "words_file": "", "duration": 0.0,
            "transcript": "", "segments": [], "text": "", "description": "",
            "status": "ok", "error": ""}


def _ingest_link(item_id: str, url: str, work: Path, reference: bool, log) -> dict:
    """A link: a video (downloaded and transcribed with timed segments) or an
    article (its text)."""
    if ingest.is_video_url(url):
        item = _blank_item(item_id, "link", reference)
        item["url"] = url
        part = work / item_id
        part.mkdir(parents=True, exist_ok=True)
        try:
            log(f"{item_id}: downloading {url}")
            video, info = ingest.download_video(url, part)
            if video is None:
                raise RuntimeError("yt-dlp brought back no video file")
            item["title"] = str(info.get("title") or url)
            item["description"] = str(info.get("description") or "")[:1200]
            item["file"] = str(video.relative_to(work))
            item["duration"] = round(float(info.get("duration") or 0)
                                     or render.probe_duration(video), 3)
            segments = ingest.whisper_segments(video, log)
            item["segments"] = _clean_segments(segments)
            item["transcript"] = " ".join(s["text"] for s in item["segments"])
            if not item["transcript"]:
                log(f"{item_id}: no transcript — timestamps inside this video "
                    f"cannot be verified, so it can only be used by duration", "warn")
        except Exception as exc:  # noqa: BLE001 — one link is not the production
            item.update(status="failed", error=str(exc)[:400])
            log(f"{item_id}: {url} failed and was skipped: {exc}", "warn")
        return item

    item = _blank_item(item_id, "artigo", reference)
    item["url"] = url
    try:
        log(f"{item_id}: reading {url}")
        article = ingest._ingest_article(url)  # noqa: SLF001 — the one article reader
        item["title"] = article.title or url
        item["text"] = article.text
        if not article.text.strip():
            raise RuntimeError("the page has no readable text")
    except Exception as exc:  # noqa: BLE001
        item.update(status="failed", error=str(exc)[:400])
        log(f"{item_id}: {url} failed and was skipped: {exc}", "warn")
    return item


def _ingest_upload(item_id: str, attachment: str, work: Path, reference: bool,
                   log) -> dict:
    """An upload: an interview (transcribed word by word) or an image."""
    try:
        src = uploads_router.resolve(attachment)
    except FileNotFoundError:
        item = _blank_item(item_id, "entrevista", reference)
        item.update(status="failed",
                    error=f"upload {attachment} is no longer on disk — send it again")
        log(f"{item_id}: {item['error']}", "warn")
        return item

    if src.suffix.lower() in uploads_router.IMAGE_EXT:
        item = _blank_item(item_id, "imagem", reference)
        dest = work / f"{item_id}{src.suffix.lower()}"
        shutil.copy(src, dest)
        item["file"] = dest.name
        item["title"] = src.stem
        log(f"{item_id}: image {src.name}")
        return item

    item = _blank_item(item_id, "entrevista", reference)
    dest = work / f"{item_id}{src.suffix.lower()}"
    try:
        shutil.copy(src, dest)
        item["file"] = dest.name
        item["title"] = src.stem
        item["duration"] = round(render.probe_duration(dest), 3)
        if item["duration"] <= 0:
            raise RuntimeError("FFmpeg could not read the length of this video")
        log(f"{item_id}: interview {src.name} ({item['duration']:.0f}s), transcribing")
        words, segments = _transcribe(dest, log)
        if words:
            words_file = work / f"{item_id}.words.json"
            words_file.write_text(json.dumps(words, ensure_ascii=False),
                                  encoding="utf-8")
            item["words_file"] = words_file.name
        item["segments"] = _clean_segments(segments)
        item["transcript"] = " ".join(s["text"] for s in item["segments"])
        if not item["transcript"]:
            log(f"{item_id}: no speech transcribed — quotes from this interview "
                f"cannot be verified", "warn")
    except Exception as exc:  # noqa: BLE001
        item.update(status="failed", error=str(exc)[:400])
        log(f"{item_id}: {src.name} failed and was skipped: {exc}", "warn")
    return item


def _transcribe(video: Path, log) -> tuple[list[dict], list[dict]]:
    """Word timings when transcription is installed, sentence segments always.

    An interview is quoted by the second, so it gets the word-level pass a
    recording of your own gets (`reels.transcribe_words`). When that is not
    installed the segment-level fallback still gives the briefing something to
    cite; when neither is, the material is kept as footage with no transcript.
    """
    try:
        words, _language = reels.transcribe_words(video, log)
    except RuntimeError as exc:
        log(f"{exc} Trying the segment-level transcription instead.", "warn")
        words = []
    if words:
        return words, _segments_from_words(words)
    return [], ingest.whisper_segments(video, log)


def _segments_from_words(words: list[dict], max_seconds: float = 12.0,
                         gap: float = 0.8) -> list[dict]:
    """Sentences out of timed words: a break at a full stop, at a pause, or
    when a run gets too long to be one quote."""
    segments: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if current:
            segments.append({"start": round(current[0]["start"], 3),
                             "end": round(current[-1]["end"], 3),
                             "text": " ".join(w["word"] for w in current).strip()})

    for word in words:
        if current:
            pause = word["start"] - current[-1]["end"]
            span = word["start"] - current[0]["start"]
            ended = re.search(r"[.!?]$", current[-1]["word"]) is not None
            if pause > gap or span > max_seconds or ended:
                flush()
                current = []
        current.append(word)
    flush()
    return segments


def _clean_segments(segments) -> list[dict]:
    out = []
    for seg in segments or []:
        try:
            start, end = float(seg["start"]), float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(seg.get("text", "")).strip()
        if text and end > start:
            out.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return out


def _normalize_material(data: dict, existing: dict | None) -> dict:
    """The catalog as the USER edited it.

    Titles, descriptions, kinds and the reference flag are theirs to change: a
    file uploaded as an interview may really be B-roll of the office, and the
    model should be told so. Files, durations and transcripts are not — they
    were measured, and a hand-typed duration is exactly the kind of timestamp
    the validators exist to refuse. Dropping an item is allowed; inventing one
    is not: new material goes through ingestion.
    """
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Send the material as {\"items\": [...]}.")
    known = {i["id"]: i for i in (existing or {}).get("items", [])}
    items = []
    for edited in data["items"]:
        if not isinstance(edited, dict):
            continue
        item_id = str(edited.get("id", "")).strip()
        base = known.get(item_id)
        if base is None:
            raise ValueError(
                f"Material '{item_id}' is not in the catalog. New material is "
                f"added by re-ingesting (POST .../stage/material), not by hand.")
        merged = dict(base)
        kind = str(edited.get("kind", base["kind"])).strip()
        if kind not in MATERIAL_KINDS:
            raise ValueError(f"Material '{item_id}': unknown kind '{kind}'. "
                             f"Use one of {', '.join(MATERIAL_KINDS)}.")
        # a video cannot become an image and vice versa — the file decides
        if (kind == "imagem") != (base["kind"] == "imagem"):
            raise ValueError(f"Material '{item_id}': '{base['kind']}' cannot be "
                             f"relabelled as '{kind}' — the file is what it is.")
        merged.update({
            "kind": kind,
            "reference": bool(edited.get("reference", base.get("reference", False))),
            "title": str(edited.get("title", base["title"])).strip() or base["title"],
            "description": str(edited.get("description", base.get("description", ""))).strip(),
        })
        items.append(merged)
    if not [i for i in items if i["status"] == "ok" and not i["reference"]]:
        raise ValueError("At least one usable, non-reference material has to stay "
                         "in the catalog.")
    return {"items": items, "ingested_at": (existing or {}).get("ingested_at", db.now())}


def _by_id(material: dict) -> dict[str, dict]:
    return {i["id"]: i for i in material.get("items", []) if i.get("id")}


def _check_span(catalog: dict[str, dict], material_id: str, start, end,
                min_seconds: float = 0.5) -> tuple[dict | None, float, float, str]:
    """Whether [start, end) of a material can be put on screen.

    Returns the item and the seconds to use, or a reason it was refused. The end
    is clamped to the material's length (a model rounding up past the end is
    not lying about the quote); a start past the end is — there is nothing
    there. Where a transcript exists the span has to overlap speech in it: a
    quote nobody said at those seconds is the failure this whole check is for.
    """
    item = catalog.get(str(material_id or "").strip())
    if item is None:
        return None, 0.0, 0.0, f"material '{material_id}' does not exist"
    if item.get("reference"):
        return None, 0.0, 0.0, (f"{item['id']} is a reference example — structure "
                                f"only, never content")
    if item.get("status") != "ok":
        return None, 0.0, 0.0, f"{item['id']} failed to ingest"
    if item["kind"] not in ("entrevista", "link"):
        return None, 0.0, 0.0, f"{item['id']} is {item['kind']}, not a video"
    try:
        start_s, end_s = float(start), float(end)
    except (TypeError, ValueError):
        return None, 0.0, 0.0, f"{item['id']}: start/end are not numbers"
    duration = float(item.get("duration") or 0.0)
    start_s = max(start_s, 0.0)
    if duration and start_s >= duration - 0.5:
        return None, 0.0, 0.0, (f"{item['id']}: start {start_s:.1f}s is past the "
                                f"end of the material ({duration:.1f}s)")
    if duration:
        end_s = min(end_s, duration)
    if end_s - start_s < min_seconds:
        return None, 0.0, 0.0, f"{item['id']}: {start_s:.1f}-{end_s:.1f}s is empty"
    segments = item.get("segments") or []
    if segments and not any(s["end"] > start_s and s["start"] < end_s for s in segments):
        return None, 0.0, 0.0, (f"{item['id']}: nobody speaks between "
                                f"{start_s:.1f}s and {end_s:.1f}s in the transcript")
    return item, round(start_s, 2), round(end_s, 2), ""


# --------------------------------------------------------------------------
# Stage 2 — the briefing
# --------------------------------------------------------------------------

BRIEFING_SYSTEM = """Você é pesquisador e roteirista-chefe de documentários. Recebe TODO o
material bruto de uma produção — entrevistas transcritas com marcação de tempo,
vídeos, artigos, imagens — mais o pedido de quem produz, e devolve o briefing de
pesquisa que vai orientar o roteiro.

Todo o texto sai em {language}, exceto `stock_queries`, que é em INGLÊS: vai
direto para bancos de vídeo, que respondem melhor em inglês.

Regras absolutas:
- Fatos vêm do material. Cada fato aponta `source`, o id do material de onde saiu.
  Conhecimento geral só entra quando o contexto exige, com `source` vazio e dito
  como tal em `why`.
- `moments` são falas de entrevista ou de vídeo que merecem entrar no corte:
  `material` é o id, `start` e `end` são os segundos EXATOS onde a fala aparece
  na transcrição marcada [MM:SS]. Nunca invente um tempo: se a transcrição não
  mostra a fala, o momento não existe. Trechos de 8 a 60 segundos, com a `quote`
  transcrita.
- Material marcado como REFERÊNCIA serve APENAS para estrutura, ritmo e estilo de
  edição. Nada dele vira fato, fala, imagem ou trecho na produção — copiar
  conteúdo de uma referência é proibido.
- `gaps` diz o que o material NÃO cobre e o roteiro vai precisar; `stock_queries`
  são as buscas em banco de vídeo (2 a 5 palavras, inglês, concretas e visuais)
  que cobrem esses vazios e as passagens de narração.
- `structure` propõe as partes da obra, na ordem, com propósito e minutos, somando
  o alvo pedido. Segue a estrutura do tipo de produção informado.
- Para mini-série, `episodes` lista um objeto por episódio com o foco de cada um;
  para os outros tipos, lista vazia.
- Nada de markdown, emoji ou lista numerada dentro dos campos.
- A instrução do usuário vale mais que qualquer convenção.

Responda APENAS com JSON válido no formato:
{"title": str, "logline": str, "angle": str,
 "facts": [{"fact": str, "source": str, "why": str}],
 "themes": [str],
 "moments": [{"material": str, "start": number, "end": number, "quote": str, "why": str}],
 "gaps": [str], "stock_queries": [str],
 "structure": [{"part": str, "purpose": str, "minutes": number}],
 "episodes": [{"episode": int, "title": str, "focus": str}]}"""

BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "angle": {"type": "string"},
        "facts": {"type": "array", "items": {
            "type": "object",
            "properties": {"fact": {"type": "string"}, "source": {"type": "string"},
                           "why": {"type": "string"}},
            "required": ["fact", "source", "why"], "additionalProperties": False}},
        "themes": {"type": "array", "items": {"type": "string"}},
        "moments": {"type": "array", "items": {
            "type": "object",
            "properties": {"material": {"type": "string"}, "start": {"type": "number"},
                           "end": {"type": "number"}, "quote": {"type": "string"},
                           "why": {"type": "string"}},
            "required": ["material", "start", "end", "quote", "why"],
            "additionalProperties": False}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "stock_queries": {"type": "array", "items": {"type": "string"}},
        "structure": {"type": "array", "items": {
            "type": "object",
            "properties": {"part": {"type": "string"}, "purpose": {"type": "string"},
                           "minutes": {"type": "number"}},
            "required": ["part", "purpose", "minutes"], "additionalProperties": False}},
        "episodes": {"type": "array", "items": {
            "type": "object",
            "properties": {"episode": {"type": "integer"}, "title": {"type": "string"},
                           "focus": {"type": "string"}},
            "required": ["episode", "title", "focus"], "additionalProperties": False}},
    },
    "required": ["title", "logline", "angle", "facts", "themes", "moments", "gaps",
                 "stock_queries", "structure", "episodes"],
    "additionalProperties": False,
}


def _production_briefing(prompt_text: str, instruction: str, options: dict) -> str:
    """The head of every prompt: what the user asked for, in what tone, of what
    type — with the type's structure spelled out so the model follows it
    instead of its own idea of a documentary."""
    spec = TYPES[options["type"]]
    head = (f"INSTRUÇÃO DO USUÁRIO (prioridade máxima):\n{instruction.strip()}\n\n"
            if instruction.strip() else "")
    length = (f"{options['episodes']} episódios de cerca de {options['target_minutes']} "
              f"minutos cada" if options["type"] == "mini_serie"
              else f"cerca de {options['target_minutes']} minutos")
    style = f"\nEstilo visual e de edição: {options['style'].strip()}" if options.get("style", "").strip() else ""
    return (f"{head}PEDIDO:\n{prompt_text.strip()}\n\n"
            f"TIPO DE PRODUÇÃO: {spec['label']} ({options['type']}), {length}.\n"
            f"ESTRUTURA DESSE TIPO: {spec['structure']}\n"
            f"TOM: {tone_text(options)}{style}\n"
            f"NARRADOR: {NARRATOR_DESCRIPTION[options['narrator']]}\n")


def _catalog_lines(material: dict) -> list[str]:
    """One line per item: id, kind, title, length — what every prompt after the
    briefing needs to refer to material by id without carrying it all."""
    lines = []
    for item in material.get("items", []):
        if item.get("status") != "ok":
            continue
        tag = " [REFERÊNCIA — só estrutura, nunca conteúdo]" if item.get("reference") else ""
        length = f", {_fmt_time(item['duration'])}" if item.get("duration") else ""
        desc = f" — {item['description'][:160]}" if item.get("description") else ""
        lines.append(f"[{item['id']}] {item['kind'].upper()}{tag} \"{item['title']}\"{length}{desc}")
    return lines


def _material_text(item: dict, budget: int, log=lambda m: None) -> str:
    """The item as the briefing reads it.

    A transcript that fits the budget goes in whole, with [MM:SS] stamps — the
    stamps are what makes a moment's `start`/`end` verifiable. One that does
    not is condensed by the windowed clipper into its strongest stretches, each
    shown with its own stamped lines, so the briefing sees the whole interview
    and not its first ten minutes.
    """
    if item["kind"] == "artigo":
        return item.get("text", "")[:budget]
    if item["kind"] == "imagem":
        return "(imagem)"
    segments = item.get("segments") or []
    if not segments:
        return item.get("transcript", "")[:budget] or "(sem transcrição)"
    stamped = clipper.transcript_with_timestamps(segments)
    if len(stamped) <= budget:
        return stamped

    count = max(4, budget // 1200)
    try:
        picks = livecuts.collect_clips(segments, float(item.get("duration") or 0.0),
                                       count, 45, log=log)
    except Exception as exc:  # noqa: BLE001 — a failed condensation is not the stage
        log(f"{item['id']}: could not condense the transcript ({exc}); "
            f"showing the start of it")
        picks = []
    if not picks:
        return stamped[:budget]
    parts = [f"(transcrição longa — {len(picks)} trechos selecionados; os tempos são "
             f"os do vídeo inteiro)"]
    for pick in picks:
        inside = [s for s in segments if s["end"] > pick["inicio"] and s["start"] < pick["fim"]]
        parts.append(f"--- {pick.get('titulo', '')} ---\n"
                     + clipper.transcript_with_timestamps(inside))
    return "\n".join(parts)[:budget]


def _material_dossier(material: dict, log=lambda m: None) -> str:
    items = [i for i in material.get("items", []) if i.get("status") == "ok"]
    weights = {i["id"]: max(float(i.get("duration") or 0.0),
                            len(i.get("text", "")) / 200.0, 60.0) for i in items}
    total = sum(weights.values()) or 1.0
    chunks = []
    for item, line in zip(items, _catalog_lines(material)):
        budget = max(MIN_ITEM_CHARS, int(BRIEFING_CHAR_BUDGET * weights[item["id"]] / total))
        chunks.append(f"{line}\n{_material_text(item, budget, log)}")
    return "\n\n".join(chunks)


def develop_briefing(project: dict, material: dict, instruction: str,
                     options: dict, log=lambda m: None) -> dict:
    prompt = f"""{_production_briefing(project["prompt"], instruction, options)}
MATERIAL (ids entre colchetes; tempos em [MM:SS]):
{_material_dossier(material, log)}

Escreva o briefing de pesquisa."""

    data = llm.complete_json(_in_language(BRIEFING_SYSTEM, options["language"]),
                             prompt, BRIEFING_SCHEMA, max_tokens=10000,
                             purpose="longform_briefing")
    return _normalize_briefing(data, material, options)


def _normalize_briefing(data: dict, material: dict, options: dict) -> dict:
    if not isinstance(data, dict):
        raise RuntimeError("The briefing has to be a JSON object.")
    logline = str(data.get("logline", "")).strip()
    if not logline:
        raise RuntimeError("The model returned a briefing with no logline.")
    catalog = _by_id(material)
    rejected: list[dict] = []

    facts = []
    for item in data.get("facts") or []:
        if not isinstance(item, dict) or not str(item.get("fact", "")).strip():
            continue
        source = str(item.get("source", "")).strip()
        if source and source not in catalog:
            rejected.append({"what": "fact", "reason": f"source '{source}' is not in "
                                                       f"the catalog; kept without a source",
                             "fact": str(item["fact"]).strip()[:120]})
            source = ""
        elif source and catalog[source].get("reference"):
            rejected.append({"what": "fact", "reason": f"{source} is a reference — it "
                                                       f"cannot be a source of facts",
                             "fact": str(item["fact"]).strip()[:120]})
            continue
        facts.append({"fact": str(item["fact"]).strip(), "source": source,
                      "why": str(item.get("why", "")).strip()})

    moments = []
    for item in data.get("moments") or []:
        if not isinstance(item, dict):
            continue
        found, start, end, reason = _check_span(catalog, item.get("material"),
                                                item.get("start"), item.get("end"),
                                                MIN_MOMENT_SECONDS)
        if found is None:
            rejected.append({"what": "moment", "reason": reason,
                             "quote": str(item.get("quote", "")).strip()[:120]})
            continue
        end = min(end, start + MAX_MOMENT_SECONDS)
        moments.append({"material": found["id"], "start": start, "end": end,
                        "quote": str(item.get("quote", "")).strip(),
                        "why": str(item.get("why", "")).strip()})

    queries: list[str] = []
    for query in data.get("stock_queries") or []:
        text = str(query).strip()
        if text and text.lower() not in {q.lower() for q in queries}:
            queries.append(text)

    structure = []
    for part in data.get("structure") or []:
        if not isinstance(part, dict) or not str(part.get("part", "")).strip():
            continue
        try:
            minutes = max(float(part.get("minutes") or 0.0), 0.0)
        except (TypeError, ValueError):
            minutes = 0.0
        structure.append({"part": str(part["part"]).strip(),
                          "purpose": str(part.get("purpose", "")).strip(),
                          "minutes": round(minutes, 1)})

    episodes = []
    if options["type"] == "mini_serie":
        for index, ep in enumerate(data.get("episodes") or [], start=1):
            if not isinstance(ep, dict):
                continue
            episodes.append({"episode": index, "title": str(ep.get("title", "")).strip()
                             or f"Episódio {index}",
                             "focus": str(ep.get("focus", "")).strip()})
            if len(episodes) >= options["episodes"]:
                break
        while len(episodes) < options["episodes"]:
            episodes.append({"episode": len(episodes) + 1,
                             "title": f"Episódio {len(episodes) + 1}", "focus": ""})

    return {
        "title": str(data.get("title", "")).strip() or "Untitled",
        "logline": logline,
        "angle": str(data.get("angle", "")).strip(),
        "facts": facts,
        "themes": [str(t).strip() for t in (data.get("themes") or []) if str(t).strip()],
        "moments": moments,
        "gaps": [str(g).strip() for g in (data.get("gaps") or []) if str(g).strip()],
        "stock_queries": queries,
        "structure": structure,
        "episodes": episodes,
        # What was refused and why — visible, so the user can correct the
        # briefing by hand instead of wondering where a quote went.
        "rejected": rejected,
    }


# --------------------------------------------------------------------------
# Stage 3 — the script
# --------------------------------------------------------------------------

ROTEIRO_SYSTEM = """Você é roteirista de documentários e curtas. Recebe o briefing de pesquisa
e o catálogo do material e escreve o roteiro em blocos.

A narração sai em {language}, escrita para ser LIDA EM VOZ ALTA por {narrator}.

Cada bloco tem:
- `kind`: abertura | contexto | ato | entrevista | transicao | conclusao | recap | gancho
- `title`: nome interno do bloco (não aparece na tela)
- `narration`: o texto do narrador. VAZIO quando a entrevista fala por si — um
  bloco `entrevista` normalmente não leva narração, no máximo uma frase de entrada.
- `seconds`: duração pretendida do bloco na tela.
- `visual`: o que está na tela, em uma linha — a intenção, não a lista de
  arquivos (isso é decidido no plano de edição).
- `moment`: quando o bloco é carregado por uma fala do material, o `material` e os
  `start`/`end` EXATOS tirados dos momentos do briefing. Nulo nos demais.

Orçamento de palavras: narração de documentário é falada a cerca de {wps} palavras
por segundo, já contando as pausas e o ar para a imagem. Um bloco de 30 segundos
de narração cabe {words30} palavras, não mais. Escreva para o tempo, não o contrário.

Regras absolutas:
- Frases curtas, uma ideia por frase, presente do indicativo. Sem markdown, sem
  rubrica entre parênteses, sem emoji.
- A abertura tem um gancho nos primeiros 20 segundos: uma pergunta, um dado ou
  uma fala forte do material.
- Narração e entrevista alternam: a narração introduz, a entrevista prova, a
  narração conclui e liga ao próximo ato. Não repita em narração o que a
  entrevista já diz.
- Só cite momentos que estão no briefing; não invente tempos.
- Material de REFERÊNCIA orienta estrutura e ritmo, nunca entra como conteúdo.
- A soma dos `seconds` de cada episódio fica perto do alvo informado.
- Para mini-série: um objeto por episódio; a partir do segundo, um bloco `recap`
  no início; até o penúltimo, um bloco `gancho` no fim.
- Modo sem narração: `narration` fica vazio em todos os blocos e o texto
  necessário vai para `visual` como cartela.

Responda APENAS com JSON válido no formato:
{"episodes": [{"episode": int, "title": str,
  "blocks": [{"kind": str, "title": str, "narration": str, "seconds": int,
              "visual": str, "moment": {"material": str, "start": number, "end": number}}]}]}
Bloco sem momento leva "moment": null."""

ROTEIRO_SCHEMA = {
    "type": "object",
    "properties": {
        "episodes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "episode": {"type": "integer"},
                "title": {"type": "string"},
                "blocks": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "title": {"type": "string"},
                        "narration": {"type": "string"},
                        "seconds": {"type": "integer"},
                        "visual": {"type": "string"},
                        "moment": {"type": ["object", "null"],
                                   "properties": {"material": {"type": "string"},
                                                  "start": {"type": "number"},
                                                  "end": {"type": "number"}},
                                   "required": ["material", "start", "end"],
                                   "additionalProperties": False},
                    },
                    "required": ["kind", "title", "narration", "seconds", "visual",
                                 "moment"],
                    "additionalProperties": False}},
            },
            "required": ["episode", "title", "blocks"], "additionalProperties": False}},
    },
    "required": ["episodes"],
    "additionalProperties": False,
}


def write_script(project: dict, material: dict, briefing: dict, instruction: str,
                 options: dict) -> dict:
    system = (_in_language(ROTEIRO_SYSTEM, options["language"])
              .replace("{narrator}", NARRATOR_DESCRIPTION[options["narrator"]])
              .replace("{wps}", f"{NARRATION_WORDS_PER_SECOND:.1f}")
              .replace("{words30}", str(int(30 * NARRATION_WORDS_PER_SECOND))))

    shown = {k: v for k, v in briefing.items() if k != "rejected"}
    prompt = f"""{_production_briefing(project["prompt"], instruction, options)}
CATÁLOGO DO MATERIAL:
{chr(10).join(_catalog_lines(material))}

BRIEFING DE PESQUISA:
{json.dumps(shown, ensure_ascii=False, indent=2)}

Escreva o roteiro em blocos."""

    data = llm.complete_json(system, prompt, ROTEIRO_SCHEMA, max_tokens=12000,
                             purpose="longform_roteiro")
    return _normalize_script(data, material, options)


def _normalize_script(data: dict, material: dict, options: dict) -> dict:
    if not isinstance(data, dict):
        raise RuntimeError("The script has to be a JSON object.")
    raw_episodes = [e for e in (data.get("episodes") or []) if isinstance(e, dict)]
    if not raw_episodes:
        raise RuntimeError("The model returned a script with no episodes.")

    # A documentary is one episode. A model that splits it in two anyway has
    # written the same film in two parts, so the parts are joined.
    if options["type"] != "mini_serie":
        blocks = [b for e in raw_episodes for b in (e.get("blocks") or [])]
        raw_episodes = [{"episode": 1, "title": raw_episodes[0].get("title", ""),
                         "blocks": blocks}]
    raw_episodes = raw_episodes[:options["episodes"]]

    catalog = _by_id(material)
    rejected: list[dict] = []
    notes: list[str] = []
    episodes = []
    for number, episode in enumerate(raw_episodes, start=1):
        blocks = []
        for raw in episode.get("blocks") or []:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind", "")).strip().lower()
            if kind not in BLOCK_KINDS:
                kind = "ato"
            narration = str(raw.get("narration", "")).strip()
            if options["narrator"] == "sem_narracao":
                narration = ""
            try:
                seconds = int(raw.get("seconds") or 0)
            except (TypeError, ValueError):
                seconds = 0
            words = len(narration.split())
            # The words dictate: a block cannot be shorter than its own
            # narration takes to say, so the budget wins over the estimate.
            budget = math.ceil(words / NARRATION_WORDS_PER_SECOND) if words else 0
            seconds = min(max(seconds or budget or 10, MIN_BLOCK_SECONDS), MAX_BLOCK_SECONDS)
            adjusted = False
            if budget > seconds:
                seconds, adjusted = min(budget, MAX_BLOCK_SECONDS), True
            moment = None
            raw_moment = raw.get("moment")
            if isinstance(raw_moment, dict) and raw_moment.get("material"):
                found, start, end, reason = _check_span(
                    catalog, raw_moment.get("material"), raw_moment.get("start"),
                    raw_moment.get("end"), MIN_MOMENT_SECONDS)
                if found is None:
                    rejected.append({"what": "moment", "block": str(raw.get("title", "")),
                                     "reason": reason})
                else:
                    moment = {"material": found["id"], "start": start,
                              "end": min(end, start + MAX_MOMENT_SECONDS)}
            if not narration and not moment and not str(raw.get("visual", "")).strip():
                continue
            blocks.append({
                "key": "",
                "kind": kind,
                "title": str(raw.get("title", "")).strip(),
                "narration": narration,
                "words": words,
                "seconds": seconds,
                "budget_seconds": budget,
                "adjusted": adjusted,
                "visual": str(raw.get("visual", "")).strip(),
                "moment": moment,
            })
        if not blocks:
            continue
        if options["type"] == "mini_serie":
            # The recap and the cliffhanger are the series' structure, not a
            # suggestion. A missing one is inserted empty, where the user can
            # see it and fill it in, rather than the episode silently lacking it.
            if number > 1 and blocks[0]["kind"] != "recap":
                blocks.insert(0, _structural_block("recap", "Recap do episódio anterior"))
                notes.append(f"episode {number}: no recap block came back; an empty "
                             f"one was inserted")
            if number < len(raw_episodes) and blocks[-1]["kind"] != "gancho":
                blocks.append(_structural_block("gancho", "Gancho para o próximo episódio"))
                notes.append(f"episode {number}: no cliffhanger block came back; an "
                             f"empty one was inserted")
        for index, block in enumerate(blocks, start=1):
            block["key"] = f"e{number:02d}_b{index:02d}"
        episodes.append({
            "episode": number,
            "title": str(episode.get("title", "")).strip(),
            "blocks": blocks,
            "total_seconds": sum(b["seconds"] for b in blocks),
            "narration_words": sum(b["words"] for b in blocks),
        })

    if not episodes:
        raise RuntimeError("The model returned a script with no blocks.")
    total_blocks = sum(len(e["blocks"]) for e in episodes)
    if total_blocks > MAX_BLOCKS * len(episodes):
        raise RuntimeError(
            f"The script came back with {total_blocks} blocks, past the "
            f"{MAX_BLOCKS} ceiling per episode. Ask for a shorter production or "
            f"regenerate with an instruction to cut it down.")
    return {"episodes": episodes, "rejected": rejected, "notes": notes}


def _structural_block(kind: str, title: str) -> dict:
    return {"key": "", "kind": kind, "title": title, "narration": "", "words": 0,
            "seconds": 8, "budget_seconds": 0, "adjusted": False,
            "visual": title, "moment": None}


def script_blocks(script: dict) -> list[dict]:
    return [b for e in script.get("episodes", []) for b in e.get("blocks", [])]


# --------------------------------------------------------------------------
# Stage 4 — the edit plan (the heart)
# --------------------------------------------------------------------------

PLANO_SYSTEM = """Você é o editor de um documentário e decide, bloco a bloco, o que está na
tela e quando. O roteiro já existe; você escolhe as imagens.

Para cada bloco do roteiro (identificado por `key`) devolva a lista de `shots`
na ordem em que aparecem. Cada shot tem `kind`:
- `entrevista`: trecho de entrevista gravada. `material` é o id, `start`/`end`
  em segundos EXATOS da transcrição. O áudio do trecho é ouvido.
- `link`: trecho de vídeo baixado de link. Mesmas regras de `material`, `start` e `end`.
- `stock`: imagem de banco. `query` em INGLÊS, 2 a 5 palavras, concreta e visual
  ("hands typing on laptop at night", não "cybercrime concept"). `seconds` na tela.
- `imagem`: uma imagem do material. `material` é o id, `seconds` na tela.
- `avatar`: o apresentador em quadro lendo a narração deste bloco. Só existe
  quando o modo de narrador é avatar; nos outros modos não use.
- `cartela`: cartão de texto em tela cheia. `text` é o texto (título de ato, dado,
  pergunta), `seconds` na tela. O texto é para QUEM ASSISTE. Nunca escreva nele
  recado para quem edita — "pendência", "trecho não consta", "falta material",
  "a verificar". Se um trecho que você queria não existe, simplesmente não use
  esse shot: o filme não é o lugar de discutir o filme.

Regras absolutas:
- Só use ids que existem no CATÁLOGO. Um id inventado invalida o shot.
- `start`/`end` sempre dentro da duração do material e cobrindo uma fala que está
  na transcrição mostrada. Nunca estenda além do que a transcrição mostra.
- Material de REFERÊNCIA não entra em shot nenhum: existe só para imitar ritmo.
- Bloco com `moment` no roteiro usa esse trecho como shot de entrevista/link.
- A soma dos `seconds` dos shots cobre o bloco: sobre narração, a imagem muda a
  cada 5 a 12 segundos; numa entrevista, o trecho é o shot.
- `lower_third` num shot de entrevista é o nome (e cargo) de quem fala, tirado do
  título ou da descrição do material; vazio nos demais.
- Use as buscas de stock do briefing quando servirem; crie outras quando o bloco pedir.
- Sem markdown, sem emoji.

Responda APENAS com JSON válido no formato:
{"episodes": [{"episode": int, "blocks": [{"key": str,
  "shots": [{"kind": str, "material": str, "start": number, "end": number,
             "query": str, "text": str, "seconds": number, "lower_third": str}]}]}]}
Campos que não se aplicam ao kind ficam vazios ("" ou 0)."""

PLANO_SCHEMA = {
    "type": "object",
    "properties": {
        "episodes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "episode": {"type": "integer"},
                "blocks": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "shots": {"type": "array", "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string"},
                                "material": {"type": "string"},
                                "start": {"type": "number"},
                                "end": {"type": "number"},
                                "query": {"type": "string"},
                                "text": {"type": "string"},
                                "seconds": {"type": "number"},
                                "lower_third": {"type": "string"},
                            },
                            "required": ["kind", "material", "start", "end", "query",
                                         "text", "seconds", "lower_third"],
                            "additionalProperties": False}},
                    },
                    "required": ["key", "shots"], "additionalProperties": False}},
            },
            "required": ["episode", "blocks"], "additionalProperties": False}},
    },
    "required": ["episodes"],
    "additionalProperties": False,
}


def _catalog_for_plan(material: dict, briefing: dict, script: dict) -> str:
    """The catalog with the transcript around every moment the briefing and the
    script chose, stamped — enough for the editor to pick exact in and out
    points without carrying forty minutes of transcript again."""
    wanted: dict[str, list[tuple[float, float]]] = {}
    for moment in briefing.get("moments", []):
        wanted.setdefault(moment["material"], []).append((moment["start"], moment["end"]))
    for block in script_blocks(script):
        if block.get("moment"):
            m = block["moment"]
            wanted.setdefault(m["material"], []).append((m["start"], m["end"]))

    chunks = []
    for item, line in zip([i for i in material.get("items", []) if i.get("status") == "ok"],
                          _catalog_lines(material)):
        segments = item.get("segments") or []
        if item["kind"] in ("entrevista", "link") and segments and not item.get("reference"):
            spans = wanted.get(item["id"]) or []
            if spans:
                inside = [s for s in segments
                          if any(s["end"] > a - 20 and s["start"] < b + 20 for a, b in spans)]
            else:
                inside = segments[:40]
            chunks.append(f"{line}\n{clipper.transcript_with_timestamps(inside)[:6000]}")
        else:
            chunks.append(line)
    return "\n\n".join(chunks)


def plan_edit(project: dict, material: dict, briefing: dict, script: dict,
              instruction: str, options: dict, log=lambda m: None) -> dict:
    """Asks the editor for the plan, validates every reference in code, and
    when something was refused asks ONCE more with the refusals spelled out —
    a model told exactly which second does not exist usually fixes it. What
    the second answer still gets wrong is dropped and reported."""
    shown = {k: v for k, v in script.items() if k not in ("rejected", "notes")}
    base = f"""{_production_briefing(project["prompt"], instruction, options)}
CATÁLOGO DO MATERIAL (com a transcrição em volta dos momentos escolhidos):
{_catalog_for_plan(material, briefing, script)}

BUSCAS DE STOCK SUGERIDAS NO BRIEFING: {", ".join(briefing.get("stock_queries", [])) or "(nenhuma)"}

ROTEIRO (um plano por bloco, pela `key`):
{json.dumps(shown, ensure_ascii=False, indent=2)}

Decida o que está na tela em cada bloco."""

    system = _in_language(PLANO_SYSTEM, options["language"])
    data = llm.complete_json(system, base, PLANO_SCHEMA, max_tokens=12000,
                             purpose="longform_plano")
    plan = _normalize_plan(data, material, script, options, log)
    if plan["rejected"]:
        reasons = "\n".join(f"- bloco {r.get('block', '?')}: {r['reason']}"
                            for r in plan["rejected"])
        log(f"Edit plan: {len(plan['rejected'])} shot(s) refused; asking once more "
            f"with the reasons")
        retry = base + f"""

CORREÇÕES OBRIGATÓRIAS — a tentativa anterior usou referências que não existem:
{reasons}
Refaça o plano inteiro respeitando o catálogo e as transcrições."""
        data = llm.complete_json(system, retry, PLANO_SCHEMA, max_tokens=12000,
                                 purpose="longform_plano")
        plan = _normalize_plan(data, material, script, options, log)
    return plan


def _normalize_plan(data: dict, material: dict, script: dict, options: dict,
                    log=lambda m: None) -> dict:
    """Every shot checked against the catalog, in code.

    The script is the authority on which blocks exist and in what order; the
    plan only says what is on screen during each. A block the model skipped
    still gets a card so the production assembles, a shot pointing at nothing
    is dropped with its reason, and seconds past the end of a material are
    clamped to it. All of it is reported in `rejected`, and logged — a
    reference silently kept is a quote at the wrong second.
    """
    if not isinstance(data, dict):
        raise RuntimeError("The edit plan has to be a JSON object.")
    catalog = _by_id(material)
    rejected: list[dict] = []

    def refuse(block_key: str, reason: str, shot: dict | None = None) -> None:
        rejected.append({"block": block_key, "reason": reason,
                         "shot": {k: shot.get(k) for k in ("kind", "material", "start",
                                                           "end", "query")} if shot else None})
        log(f"Plan {block_key}: {reason} — dropped")

    proposed: dict[str, list] = {}
    by_position: dict[int, list[list]] = {}
    for episode in data.get("episodes") or []:
        if not isinstance(episode, dict):
            continue
        try:
            number = int(episode.get("episode") or 0)
        except (TypeError, ValueError):
            number = 0
        for block in episode.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            shots = block.get("shots") if isinstance(block.get("shots"), list) else []
            key = str(block.get("key", "")).strip()
            if key:
                proposed[key] = shots
            by_position.setdefault(number, []).append(shots)

    episodes = []
    for episode in script.get("episodes", []):
        number = int(episode.get("episode") or 1)
        blocks = []
        for index, block in enumerate(episode.get("blocks", [])):
            key = block["key"]
            raw_shots = proposed.get(key)
            if raw_shots is None:
                # no key came back for this block: the plan may still have
                # answered it by position inside the episode
                positional = by_position.get(number) or []
                raw_shots = positional[index] if index < len(positional) else None
            if raw_shots is None:
                refuse(key, "the plan has no entry for this block; a title card "
                            "stands in")
                raw_shots = []

            shots = []
            for raw in raw_shots:
                if not isinstance(raw, dict):
                    continue
                shot = _normalize_shot(raw, key, block, catalog, options, refuse)
                if shot is not None:
                    shots.append(shot)

            # A block the script carries by an interview moment but the plan
            # left without it: the moment is what the block IS, so it goes in.
            if block.get("moment") and not any(
                    s["kind"] in ("entrevista", "link") for s in shots):
                m = block["moment"]
                found, start, end, reason = _check_span(catalog, m["material"],
                                                        m["start"], m["end"])
                if found is not None:
                    shots.insert(0, _shot("entrevista" if found["kind"] == "entrevista"
                                          else "link", material=found["id"], start=start,
                                          end=end, seconds=round(end - start, 2),
                                          lower_third=found["title"] if options["lower_thirds"] else ""))
            if not shots:
                text = block.get("title") or (block.get("narration") or "").split(".")[0][:80]
                shots.append(_shot("cartela", text=text or block["kind"].capitalize(),
                                   seconds=float(block["seconds"])))
                if raw_shots:
                    refuse(key, "every shot was refused; a title card stands in")

            _fill_seconds(shots, float(block["seconds"]))
            blocks.append({
                "key": key,
                "kind": block["kind"],
                "title": block.get("title", ""),
                "narration": block.get("narration", ""),
                "narrator": (options["narrator"] if block.get("narration")
                             and options["narrator"] != "sem_narracao" else "nenhum"),
                "seconds": block["seconds"],
                "shots": shots,
            })
        episodes.append({"episode": number, "title": episode.get("title", ""),
                         "blocks": blocks})

    if not any(e["blocks"] for e in episodes):
        raise RuntimeError("The edit plan covers no block of the script.")
    return {"episodes": episodes, "rejected": rejected}


def _shot(kind: str, material: str = "", start: float = 0.0, end: float = 0.0,
          query: str = "", text: str = "", seconds: float = 0.0,
          lower_third: str = "") -> dict:
    return {"kind": kind, "material": material, "start": start, "end": end,
            "query": query, "text": text, "seconds": round(float(seconds), 2),
            "lower_third": lower_third}


def _normalize_shot(raw: dict, key: str, block: dict, catalog: dict[str, dict],
                    options: dict, refuse) -> dict | None:
    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in SHOT_KINDS:
        refuse(key, f"unknown shot kind '{kind}'", raw)
        return None
    try:
        seconds = max(float(raw.get("seconds") or 0.0), 0.0)
    except (TypeError, ValueError):
        seconds = 0.0
    lower_third = str(raw.get("lower_third", "")).strip() if options["lower_thirds"] else ""

    if kind in ("entrevista", "link"):
        found, start, end, reason = _check_span(catalog, raw.get("material"),
                                                raw.get("start"), raw.get("end"))
        if found is None:
            refuse(key, reason, raw)
            return None
        end = min(end, start + MAX_MOMENT_SECONDS)
        if found["kind"] == "link" and kind == "entrevista" or found["kind"] == "entrevista" and kind == "link":
            kind = found["kind"]   # the catalog knows what it is
        return _shot(kind, material=found["id"], start=start, end=end,
                     seconds=round(end - start, 2), lower_third=lower_third)

    if kind == "imagem":
        item = catalog.get(str(raw.get("material", "")).strip())
        if item is None:
            refuse(key, f"material '{raw.get('material')}' does not exist", raw)
            return None
        if item.get("reference"):
            refuse(key, f"{item['id']} is a reference example — never content", raw)
            return None
        if item["kind"] != "imagem" or item.get("status") != "ok":
            refuse(key, f"{item['id']} is not a usable image", raw)
            return None
        return _shot("imagem", material=item["id"], seconds=seconds)

    if kind == "stock":
        query = str(raw.get("query", "")).strip()
        if not query:
            refuse(key, "a stock shot with no query", raw)
            return None
        return _shot("stock", query=query, seconds=seconds)

    if kind == "avatar":
        if options["narrator"] != "avatar":
            refuse(key, f"an avatar shot in '{options['narrator']}' narrator mode", raw)
            return None
        if not block.get("narration"):
            refuse(key, "an avatar shot on a block with no narration to read", raw)
            return None
        return _shot("avatar", seconds=seconds)

    text = str(raw.get("text", "")).strip()
    if not text:
        refuse(key, "a title card with no text", raw)
        return None
    if _is_editorial_note(text):
        # A note to the editor, printed on screen at 24pt for the audience:
        # "Pendência de edição: o trecho previsto não consta na transcrição."
        # It belongs in the rejected list, which is where the note is now read
        # from — the film is not the place to discuss the film.
        refuse(key, "a title card carrying an editorial note, not content", raw)
        return None
    return _shot("cartela", text=text, seconds=seconds)


# Openings of a note written to whoever is editing, rather than of a card
# written for whoever is watching.
_EDITORIAL_MARKERS = (
    "pendência", "pendencia", "não consta", "nao consta", "a verificar",
    "verificar:", "checar", "nota de edição", "nota de edicao", "todo:",
    "falta ", "sem material", "trecho previsto", "placeholder",
    "editor:", "obs.:", "observação de edição", "observacao de edicao",
)


def _is_editorial_note(text: str) -> bool:
    lowered = text.strip().lower()
    return any(lowered.startswith(marker) or f" {marker}" in lowered
               for marker in _EDITORIAL_MARKERS)


def _fill_seconds(shots: list[dict], block_seconds: float) -> None:
    """Shots the model left without a length share what the block has left.

    Interview and link shots already know their length (it is the cut). The
    rest — stock, images, cards, the presenter — split the remainder evenly,
    never below the minimum a picture needs to register.
    """
    fixed = sum(s["seconds"] for s in shots if s["kind"] in ("entrevista", "link"))
    free = [s for s in shots if s["kind"] not in ("entrevista", "link")]
    given = sum(s["seconds"] for s in free if s["seconds"] > 0)
    missing = [s for s in free if s["seconds"] <= 0]
    if missing:
        remainder = max(block_seconds - fixed - given, MIN_SHOT_SECONDS * len(missing))
        share = round(remainder / len(missing), 2)
        for shot in missing:
            shot["seconds"] = share
    for shot in free:
        shot["seconds"] = round(max(shot["seconds"], MIN_SHOT_SECONDS), 2)


def plan_blocks(plan: dict) -> list[dict]:
    return [b for e in plan.get("episodes", []) for b in e.get("blocks", [])]


# --------------------------------------------------------------------------
# Running one stage from a stored production
# --------------------------------------------------------------------------

def develop_stage(project: dict, stage: str, instruction: str = "",
                  log=lambda m: None) -> dict:
    """Runs a single LLM stage for a stored production, reusing the earlier ones.

    Raises `StageNotReady` when what it is built on does not exist yet — the
    router turns that into a 400 with the same message. `material` is not
    developed here: it is ingested, on the queue (`ingest_material`).
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    if stage == "material":
        raise ValueError("Material is ingested, not developed — queue "
                         "`ingest_material` for it.")

    options = options_of(project)
    instruction = instruction.strip() or (project.get("instruction") or "")

    material = document(project, "material")
    if material is None:
        raise StageNotReady("The material has not been ingested yet — wait for the "
                            "ingestion to finish, or check its error.")
    if stage == "briefing":
        return develop_briefing(project, material, instruction, options, log)

    briefing = document(project, "briefing")
    if briefing is None:
        raise StageNotReady("Develop the briefing before the script.")
    if stage == "roteiro":
        return write_script(project, material, briefing, instruction, options)

    script = document(project, "roteiro")
    if script is None:
        raise StageNotReady("Write the script before the edit plan.")
    return plan_edit(project, material, briefing, script, instruction, options, log)


def accept_stage(project: dict, stage: str, data: dict) -> dict:
    """Validates and normalizes a stage the USER wrote or edited by hand.

    The user's version goes through the same normalizer the model's output
    does, so an edited stage carries the same guarantees — a moment typed in
    by hand at a second nobody speaks is refused exactly like the model's.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    if not isinstance(data, dict):
        raise ValueError("Send the stage as a JSON object.")

    options = options_of(project)
    if stage == "material":
        return _normalize_material(data, document(project, "material"))

    material = document(project, "material") or {"items": []}
    if stage == "briefing":
        return _normalize_briefing(data, material, options)
    if stage == "roteiro":
        return _normalize_script(data, material, options)

    script = document(project, "roteiro")
    if script is None:
        raise StageNotReady("Write the script before the edit plan.")
    return _normalize_plan(data, material, script, options)


def save_stage(project: dict, stage: str, doc: dict) -> list[str]:
    """Persists a stage and drops the stages derived from it.

    Returns the names of the invalidated stages so the caller can say what has
    to be developed again. The per-block ledger is deliberately KEPT: it is
    fingerprinted, so narration and cuts whose spec survived the rewrite are
    reused instead of being paid for twice.
    """
    invalidated = [s for s in CASCADE[stage] if project.get(STAGE_COLUMN[s])]
    fields: dict = {STAGE_COLUMN[stage]: json.dumps(doc, ensure_ascii=False)}
    for downstream in CASCADE[stage]:
        fields[STAGE_COLUMN[downstream]] = None

    fields["stage"] = stage
    fields["status"] = "ready" if stage == "plano_de_edicao" else "developing"
    fields["error"] = None
    if stage == "briefing" and doc.get("title") and not (project.get("title") or "").strip():
        fields["title"] = doc["title"]
    db.update_longform(project["id"], **fields)
    return invalidated


# --------------------------------------------------------------------------
# Cost honesty
# --------------------------------------------------------------------------

def narration_provider(options: dict) -> dict:
    """Who would speak, and whether they can."""
    if options["narrator"] == "sem_narracao":
        return {"id": "", "name": "sem narração", "configured": True, "reason": ""}
    if options["narrator"] == "avatar":
        ready = avatar_mod.is_configured()
        return {"id": avatar_mod.CONNECTOR, "name": "HeyGen", "configured": ready,
                "reason": "" if ready else avatar_mod.SETUP}
    voice = db.get_voice(options["voice_id"]) if options.get("voice_id") else None
    provider = (voice or {}).get("provider") or settings.tts_provider
    return {"id": provider, "name": (voice or {}).get("name") or provider,
            "configured": True, "reason": ""}


def stock_provider() -> dict:
    ready = broll.providers_ready()
    return {"id": ",".join(ready), "name": ", ".join(ready) or "nenhum",
            "configured": bool(ready),
            "reason": "" if ready else ("No stock provider has a key. Register Pexels, "
                                        "Pixabay or Coverr on the Accounts screen.")}


def estimate(project: dict) -> dict:
    """What assembling this production would cost, in minutes of narration,
    stock clips, interview minutes, avatar seconds — and who gets billed.

    Reported before anything is started, and again by the poll: a resume pays
    only for what is not already on disk, and the numbers say so.
    """
    plan = document(project, "plano_de_edicao") or {"episodes": []}
    options = options_of(project)
    ledger = _ledger_index(project)
    jobs = jobs_of(project)

    blocks = plan_blocks(plan)
    narrated = [b for b in blocks if b.get("narration") and b.get("narrator") != "nenhum"]
    words = sum(len(b["narration"].split()) for b in narrated)
    narration_seconds = words / NARRATION_WORDS_PER_SECOND if words else 0.0

    shots = [(b, s) for b in blocks for s in b.get("shots", [])]
    stock = [s for _, s in shots if s["kind"] == "stock"]
    cuts = [s for _, s in shots if s["kind"] in ("entrevista", "link")]
    cut_seconds = sum(s["end"] - s["start"] for s in cuts)

    def pending(keys_and_specs) -> int:
        count = 0
        for episode, key, spec in keys_and_specs:
            work = _episode_dir(jobs, episode)
            if not _reusable(ledger.get(key), spec, work):
                count += 1
        return count

    narration_pending = pending(
        (e["episode"], _narration_key(b), _narration_spec(b, options))
        for e in plan["episodes"] for b in e["blocks"]
        if b.get("narration") and b.get("narrator") != "nenhum")
    shots_pending = pending(
        (e["episode"], _shot_key(b, n), _shot_spec(s))
        for e in plan["episodes"] for b in e["blocks"]
        for n, s in enumerate(b.get("shots", [])))

    warnings = []
    narrator = narration_provider(options)
    stock_info = stock_provider()
    if narrated and not narrator["configured"]:
        warnings.append(f"The narrator is an avatar and HeyGen is not configured: "
                        f"{narrator['reason']}")
    if stock and not stock_info["configured"]:
        # What happens to those shots depends on whether an image generator is
        # around, and the estimate is read precisely to decide whether to run.
        instead = ("would be generated as images (a drawn frame, not real "
                   "footage)" if imagegen.providers_ready()
                   else "would become placeholder cards")
        warnings.append(f"{len(stock)} stock shot(s) {instead}: "
                        f"{stock_info['reason']}")

    return {
        "episodes": len(plan["episodes"]),
        "blocks": len(blocks),
        "target_seconds": sum(int(b.get("seconds") or 0) for b in blocks),
        "narration": {
            "blocks": len(narrated), "words": words,
            "seconds": round(narration_seconds, 1),
            "minutes": round(narration_seconds / 60, 2),
            "mode": options["narrator"],
            "provider": narrator,
        },
        "avatar": {
            "seconds": round(narration_seconds, 1) if options["narrator"] == "avatar" else 0.0,
            "configured": narrator["configured"] if options["narrator"] == "avatar" else None,
        },
        "stock": {"clips": len(stock), "queries": len({s["query"].lower() for s in stock}),
                  "provider": stock_info},
        "interview": {"cuts": len(cuts), "seconds": round(cut_seconds, 1),
                      "minutes": round(cut_seconds / 60, 2)},
        "images": sum(1 for _, s in shots if s["kind"] == "imagem"),
        "cards": sum(1 for _, s in shots if s["kind"] == "cartela"),
        # A resume pays only for what is not already on disk.
        "narration_to_synthesize": narration_pending,
        "shots_to_prepare": shots_pending,
        "reused": (len(narrated) - narration_pending) + (len(shots) - shots_pending),
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# Montagem — assembly
# --------------------------------------------------------------------------

def _ledger_index(project: dict) -> dict[str, dict]:
    raw = project.get("progress_json") or "[]"
    entries = json.loads(raw) if isinstance(raw, str) else list(raw)
    return {entry["key"]: entry for entry in entries if entry.get("key")}


def _reusable(entry: dict | None, spec: str, work: Path | None) -> bool:
    """Whether what a previous run produced for this key can be reused as is.

    The previous attempt succeeded, the file is still there, and the spec has
    not changed since. A placeholder is deliberately NOT reusable — it is a
    failure marker, and a resume is the chance to try that shot again.
    """
    if entry is None or work is None:
        return False
    if entry.get("status") != "ok" or entry.get("hash") != _hash(spec):
        return False
    return (work / entry.get("file", "")).exists()


def _episode_dir(jobs: dict[str, str], episode: int) -> Path | None:
    job_id = jobs.get(str(episode))
    return settings.jobs_dir / job_id if job_id else None


def _narration_key(block: dict) -> str:
    return f"nar_{block['key']}"


def _narration_spec(block: dict, options: dict) -> str:
    """What the narration depends on: the words, and who says them."""
    who = (f"avatar:{options['avatar_id']}:{options['avatar_voice_id']}"
           if options["narrator"] == "avatar" else f"voice:{options['voice_id'] or ''}")
    return f"{who}\n{block['narration']}"


def _shot_key(block: dict, index: int) -> str:
    return f"shot_{block['key']}_{index:02d}"


def _shot_spec(shot: dict) -> str:
    return json.dumps({k: shot.get(k) for k in ("kind", "material", "start", "end",
                                                "query", "text", "seconds")},
                      sort_keys=True, ensure_ascii=False)


def _hold_key(block: dict) -> str:
    return f"hold_{block['key']}"


def run(action: str, project_id: str) -> None:
    """Entry point for the worker's long-form queue."""
    if action == "ingest":
        ingest_material(project_id)
    elif action == "assemble":
        assemble(project_id)
    else:
        raise ValueError(f"Unknown long-form action: {action}")


def assemble(project_id: str) -> dict:
    """Narrates, cuts, fetches, lays out, renders — one job per episode.

    Runs on the worker's long-form queue — a documentary is minutes of TTS and
    stock downloads and a long render, and does not fit in an HTTP request.

    Two rules hold throughout, the same ones the film studio keeps: a block
    whose stock is not found or whose link failed leaves a placeholder card and
    the production still assembles, reported per block; and every block that
    succeeded is written to the ledger immediately, so a resume never redoes it.
    """
    row = db.get_longform(project_id)
    if row is None:
        raise RuntimeError(f"Production {project_id} does not exist")

    plan = document(row, "plano_de_edicao")
    script = document(row, "roteiro")
    material = document(row, "material")
    briefing = document(row, "briefing") or {}
    if not plan or not script or not material:
        raise StageNotReady("Develop the edit plan before assembling the production.")

    options = options_of(row)
    if options["narrator"] == "avatar":
        # Refused up front, with what to do — never a stack trace from HeyGen
        # twenty blocks in.
        avatar_mod.credentials()
        if not options["avatar_id"] or not options["avatar_voice_id"]:
            raise RuntimeError("The narrator is an avatar but no avatar_id / "
                               "avatar_voice_id was chosen. Pick them from the "
                               "account's avatars and voices.")

    render.ensure_ffmpeg()
    catalog = _by_id(material)
    source_dir = project_dir(project_id)
    jobs = jobs_of(row)
    title = (row.get("title") or briefing.get("title") or "Production").strip()

    live_keys = set()
    for episode in plan["episodes"]:
        for block in episode["blocks"]:
            live_keys.add(_narration_key(block))
            live_keys.add(_hold_key(block))
            for n, _ in enumerate(block.get("shots", [])):
                live_keys.add(_shot_key(block, n))
    ledger = {k: e for k, e in _ledger_index(row).items() if k in live_keys}

    def flush(status: str = "assembling", stage: str = "") -> None:
        db.update_longform(project_id, status=status, stage=stage or None,
                           jobs_json=json.dumps(jobs),
                           progress_json=json.dumps(list(ledger.values()),
                                                    ensure_ascii=False))

    results = []
    current_job = ""
    try:
        db.update_longform(project_id, status="assembling", error=None)
        for episode in plan["episodes"]:
            number = int(episode["episode"])
            episode_title = title
            if len(plan["episodes"]) > 1:
                episode_title = f"{title} — Ep. {number}" + (
                    f": {episode['title']}" if episode.get("title") else "")

            job_id = jobs.get(str(number)) or _create_episode_job(
                project_id, row, episode_title, options, number)
            jobs[str(number)] = job_id
            current_job = job_id
            work = settings.job_dir(job_id)
            llm.current_job.set(job_id)

            def log(message: str, level: str = "info", _job=job_id) -> None:
                db.log_event(_job, message, level)

            db.update_job(job_id, status=JOB_STATUS_ASSEMBLING, stage=MODE, error=None)
            log(f"Production {project_id}, episode {number}: {len(episode['blocks'])} "
                f"block(s), target {_fmt_time(sum(b['seconds'] for b in episode['blocks']))}")
            flush(stage=f"episódio {number}: preparação")

            _prepare_blocks(episode["blocks"], number, ledger, work, source_dir,
                            catalog, options, log, flush)

            flush(stage=f"episódio {number}: montagem")
            edl = build_timeline(work, episode["blocks"], ledger, catalog, options)
            timeline_mod.save(work, edl)
            log(f"Timeline: {len(edl.video)} clip(s), {len(edl.audio)} audio track(s), "
                f"{len(edl.captions)} caption(s), {len(edl.media)} overlay(s), "
                f"{_fmt_time(edl.duration)}")

            flush(stage=f"episódio {number}: render")
            final = work / "short.mp4"
            timeline_render.render_timeline(work, edl, final, log=lambda m, _l=log: _l(m))
            _thumbnail(final, work / "thumb.jpg", at=min(3.0, edl.duration / 4))
            words = timeline_mod.words_from_captions(edl.captions)
            captions_mod.build_srt(words, work / "captions.srt")
            shutil.copy(final, settings.outputs_dir / f"{job_id}.mp4")

            report = qa_mod.audit(final, None, expected_duration=edl.duration, fmt=FRAME)
            log(f"QA: score {report.score}/100 — "
                f"{'PASSED' if report.passed else 'FAILED'}",
                "info" if report.passed else "warn")

            blocks_report = _blocks_report(episode["blocks"], ledger)
            failures = [b for b in blocks_report if b["status"] != "ok"]
            result = {
                "mode": MODE,
                # The frontend preview reads this to draw a 16:9 frame instead
                # of a phone.
                "format": FRAME.name,
                "project_id": project_id,
                "type": options["type"],
                "episode": number,
                "title": episode_title,
                "description": briefing.get("logline", ""),
                "hashtags": [],
                "duration": round(edl.duration, 2),
                "video": f"/api/jobs/{job_id}/file/short.mp4",
                "thumbnail": f"/api/jobs/{job_id}/file/thumb.jpg",
                "captions_srt": f"/api/jobs/{job_id}/file/captions.srt",
                "words": words,
                "blocks": blocks_report,
                "edit_mode": "narrar_por_cima",
                "source_kind": MODE,
            }
            db.update_job(job_id, status="done", stage="qa", progress=1.0,
                          title=episode_title,
                          result_json=json.dumps(result, ensure_ascii=False),
                          qa_json=report.model_dump_json(), error=None)
            if failures:
                log(f"{len(failures)} block(s) carry placeholders — assemble again "
                    f"to retry only those.", "warn")
            notify.job_done(job_id, episode_title, edl.duration, report.score,
                            report.passed)
            results.append(result)
            flush()

        db.update_longform(project_id, status="done", stage="done", title=title,
                           jobs_json=json.dumps(jobs),
                           progress_json=json.dumps(list(ledger.values()),
                                                    ensure_ascii=False), error=None)
        return {"jobs": jobs, "episodes": results}

    except Exception as exc:  # noqa: BLE001 — the reason has to reach the UI
        if current_job:
            db.log_event(current_job, f"Failure: {exc}", "error")
            db.update_job(current_job, status="error", error=str(exc))
        db.update_longform(project_id, status="error", error=str(exc),
                           jobs_json=json.dumps(jobs),
                           progress_json=json.dumps(list(ledger.values()),
                                                    ensure_ascii=False))
        raise


def _create_episode_job(project_id: str, project: dict, title: str, options: dict,
                        episode: int) -> str:
    """The job an episode becomes.

    A production is not assembled *by* the job queue — it has its own queue and
    its own stages — but its result has to BE a job, because that is what
    publishing, QA, the outputs screen and the timeline editor all read. So the
    job is created up front and the episode is assembled straight into its
    directory.
    """
    job = JobInput(
        source_type="texto",
        source=project["prompt"][:400],
        instruction=project.get("instruction") or "",
        niche=options["niche"],
        language=options["language"],
        voice_id=options["voice_id"],
        caption_style=options["caption_style"],
        caption_position=options["caption_position"],
        watermark=options["watermark"],
        music=False,
        qa_autofix=False,
    )
    job_id = db.create_job(job.model_dump(), title)
    # Straight out of "queued": see JOB_STATUS_ASSEMBLING.
    db.update_job(job_id, status=JOB_STATUS_ASSEMBLING, stage=MODE, progress=0.05)
    db.log_event(job_id, f"Production {project_id}: assembling episode {episode}")
    return job_id


def _prepare_blocks(blocks: list[dict], episode: int, ledger: dict[str, dict],
                    work: Path, source_dir: Path, catalog: dict[str, dict],
                    options: dict, log, flush) -> None:
    """Narration and footage for every block, with the failures written down.

    Losing a half-hour run because block 14 of 40 found no stock would be the
    worst possible behaviour here, so a shot that cannot be prepared becomes a
    title card of the right length and the production carries on. The failure
    is recorded per shot — the API reports it, and the next run retries
    exactly those.
    """
    stock_unavailable = "" if broll.providers_ready() else stock_provider()["reason"]
    # Asked once, not once per shot: the answer involves probing local servers,
    # and forty shots would probe forty times for the same verdict.
    can_draw = imagegen.providers_ready()
    # Built once per episode: handing windows out needs to know what every
    # other shot took, and rebuilding it per shot would give the same stretch
    # to every card in the film.
    cutaways = _Cutaways(catalog, blocks)
    if stock_unavailable and any(s["kind"] == "stock" for b in blocks for s in b["shots"]):
        if can_draw:
            fate = "Stock shots will be generated as images instead."
        elif cutaways.take(2.0) is not None:
            fate = "Stock shots will be covered with your own footage instead."
            cutaways = _Cutaways(catalog, blocks)   # the probe consumed a window
        else:
            fate = "Every stock shot becomes a placeholder card."
        log(f"{stock_unavailable} {fate}", "warn")

    total = len(blocks)
    for index, block in enumerate(blocks, start=1):
        flush(stage=f"episódio {episode}: bloco {index}/{total}")
        _prepare_narration(block, ledger, work, options, log)
        for n, shot in enumerate(block.get("shots", [])):
            _prepare_shot(block, n, shot, ledger, work, source_dir, catalog, options,
                          stock_unavailable, can_draw, cutaways, log)
        _prepare_hold(block, ledger, work, options, log)
        # Written after every block: a crash on block 15 must not throw away
        # the fourteen already synthesized and cut.
        flush(stage=f"episódio {episode}: bloco {index}/{total}")


def _prepare_narration(block: dict, ledger: dict[str, dict], work: Path,
                       options: dict, log) -> None:
    if not block.get("narration") or block.get("narrator") == "nenhum":
        return
    key = _narration_key(block)
    spec = _narration_spec(block, options)
    if _reusable(ledger.get(key), spec, work):
        return

    entry = {"kind": "narration", "key": key, "block": block["key"], "file": "",
             "words_file": "", "seconds": 0.0, "hash": _hash(spec), "status": "ok",
             "error": ""}
    try:
        if options["narrator"] == "avatar":
            dest = work / f"av_{block['key']}.mp4"
            log(f"Block {block['key']}: avatar reads {len(block['narration'].split())} words")
            heygen.generate_avatar_video(block["narration"], options["avatar_id"],
                                         options["avatar_voice_id"], dest,
                                         avatar_mod.credentials(), aspect="16:9",
                                         log=lambda m: log(str(m)))
            duration = render.probe_duration(dest)
            if duration <= 0:
                raise RuntimeError("HeyGen returned a video with no readable duration")
            words = tts.estimate_words(block["narration"], duration)
        else:
            dest = work / f"nar_{block['key']}.mp3"
            voice = db.get_voice(options["voice_id"]) if options["voice_id"] else None
            narration = tts.synthesize(block["narration"], dest, voice,
                                       lambda m, level="info": log(str(m), level))
            duration, words = narration.duration, narration.words
        words_file = work / f"{dest.stem}.words.json"
        words_file.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        entry.update(file=dest.name, words_file=words_file.name,
                     seconds=round(duration, 3))
    except Exception as exc:  # noqa: BLE001 — one block, not the production
        entry.update(status="failed", error=str(exc)[:400])
        log(f"Block {block['key']}: narration was not recorded ({exc}) — it stays "
            f"as captions only", "warn")
    ledger[key] = entry


def _prepare_shot(block: dict, index: int, shot: dict, ledger: dict[str, dict],
                  work: Path, source_dir: Path, catalog: dict[str, dict],
                  options: dict, stock_unavailable: str, can_draw: bool,
                  cutaways: "_Cutaways | None", log) -> None:
    key = _shot_key(block, index)
    spec = _shot_spec(shot)
    if _reusable(ledger.get(key), spec, work):
        return

    entry = {"kind": "shot", "key": key, "block": block["key"], "shot": shot["kind"],
             "file": "", "seconds": float(shot["seconds"]), "has_audio": False,
             "hash": _hash(spec), "status": "ok", "error": ""}
    seconds = float(shot["seconds"])

    def placeholder(reason: str, label: str) -> None:
        dest = work / f"ph_{key}.png"
        entry.update(status="placeholder", error=reason[:400], file=dest.name,
                     seconds=max(seconds, MIN_SHOT_SECONDS), has_audio=False)
        log(f"Shot {key} ({shot['kind']}): {reason} — a placeholder card holds "
            f"the slot", "warn")
        try:
            _card(label, dest, options, subtitle="placeholder")
        except Exception as exc:  # noqa: BLE001
            log(f"Could not even build the placeholder for {key}: {exc}", "warn")
            entry["file"] = ""

    try:
        if shot["kind"] in ("entrevista", "link"):
            item = catalog[shot["material"]]
            src = source_dir / item["file"]
            if not src.exists():
                raise RuntimeError(f"the file for {item['id']} is gone from disk")
            dest = work / f"cut_{key}.mp4"
            log(f"Shot {key}: {item['id']} {_fmt_time(shot['start'])}-{_fmt_time(shot['end'])}")
            _cut_segment(src, shot["start"], shot["end"], dest)
            _skip_black_open(src, shot, key, dest, float(item.get("duration") or 0.0), log)
            entry.update(file=dest.name, has_audio=_has_audio(dest),
                         seconds=round(render.probe_duration(dest) or seconds, 3))
            # The quote's own words, translated once here and kept in the
            # ledger: the subtitle for an English answer under Portuguese
            # narration has to be in Portuguese, and re-translating it on
            # every assemble would spend a call per rebuild.
            covering = _covering_segments(item, shot["start"], shot["end"])
            if covering and options["captions"]:
                entry["captions"] = [
                    {"start": round(max(seg["start"] - shot["start"], 0.0), 3),
                     "end": round(min(seg["end"], shot["end"]) - shot["start"], 3),
                     "text": seg["text"]}
                    for seg in _translate_cues(covering, options["language"], log)]
        elif shot["kind"] == "imagem":
            item = catalog[shot["material"]]
            src = source_dir / item["file"]
            if not src.exists():
                raise RuntimeError(f"the file for {item['id']} is gone from disk")
            dest = work / f"img_{key}{src.suffix.lower()}"
            shutil.copy(src, dest)
            entry.update(file=dest.name)
        elif shot["kind"] == "stock":
            # Four ways to fill a stock shot, best first: the bank it was
            # written for, an image generated from the same words, a stretch
            # of the user's own footage nothing else is using, and — only if
            # all three are gone — a card with the query on it.
            if stock_unavailable:
                dest = (_generate_shot_image(shot["query"], key, work, options, log)
                        if can_draw else None)
                if dest is None:
                    dest = _cutaway(shot, key, work, source_dir, catalog,
                                    cutaways, log)
                if dest is None:
                    placeholder(stock_unavailable, shot["query"])
                else:
                    entry.update(file=dest.name,
                                 seconds=round(render.probe_duration(dest) or seconds, 3))
            else:
                dest = _fetch_stock(shot["query"], key, work, log)
                if dest is None and can_draw:
                    dest = _generate_shot_image(shot["query"], key, work,
                                                options, log)
                if dest is None:
                    dest = _cutaway(shot, key, work, source_dir, catalog,
                                    cutaways, log)
                if dest is None:
                    placeholder(f"no stock footage found for '{shot['query']}'",
                                shot["query"])
                else:
                    entry.update(file=dest.name,
                                 seconds=round(seconds, 3))
        elif shot["kind"] == "avatar":
            narration = ledger.get(_narration_key(block))
            if not narration or narration.get("status") != "ok" or not (
                    work / narration.get("file", "")).exists():
                placeholder("the presenter's video was not generated for this block",
                            block.get("title") or "Apresentador")
            else:
                # The same file the narration track plays: the picture shows
                # the presenter while the audio is already on the timeline.
                entry.update(file=narration["file"],
                             seconds=round(float(narration["seconds"]), 3))
        else:
            dest = work / f"card_{key}.png"
            _card(shot["text"], dest, options)
            entry.update(file=dest.name)
    except Exception as exc:  # noqa: BLE001 — one shot, not the production
        placeholder(str(exc), shot.get("text") or shot.get("query")
                    or block.get("title") or block["kind"])

    if entry["status"] == "ok" and shot.get("lower_third") and entry["file"]:
        try:
            lower = work / f"lt_{key}.png"
            _lower_third(shot["lower_third"], lower)
            entry["lower_third"] = lower.name
        except Exception as exc:  # noqa: BLE001 — a caption strip is not the shot
            log(f"Shot {key}: lower third not drawn ({exc})", "warn")
    ledger[key] = entry


def _prepare_hold(block: dict, ledger: dict[str, dict], work: Path, options: dict,
                  log) -> None:
    """A card to hold the picture when the narration outlasts the footage and
    the last shot is an interview — looping an interview replays the quote,
    so the block ends on a card instead."""
    key = _hold_key(block)
    dest = work / f"hold_{block['key']}.png"
    if (work / dest.name).exists() and ledger.get(key, {}).get("status") == "ok":
        return
    try:
        _card(block.get("title") or block["kind"].capitalize(), dest, options)
        ledger[key] = {"kind": "hold", "key": key, "block": block["key"],
                       "file": dest.name, "seconds": 0.0, "hash": _hash(block["key"]),
                       "status": "ok", "error": ""}
    except Exception as exc:  # noqa: BLE001
        log(f"Block {block['key']}: hold card not drawn ({exc})", "warn")


LEGENDA_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"},
                               "text": {"type": "string"}},
                "required": ["i", "text"],
            },
        },
    },
    "required": ["lines"],
}

LEGENDA_SYSTEM = """Você legenda documentários.

Recebe falas transcritas de um vídeo, numeradas, e devolve cada uma traduzida
para {language}.

REGRAS
- Uma linha de saída para cada linha de entrada, com o mesmo número `i`.
- Não junte nem divida linhas: cada uma é exibida no tempo exato da fala.
- Tradução de legenda, não literal: natural, curta, no registro de quem fala.
- Se a linha JÁ estiver em {language}, devolva o texto igual, sem reescrever.
- Nomes próprios, siglas e termos técnicos consagrados ficam como estão.
- Sem aspas ao redor, sem reticências decorativas, sem comentários."""


def _translate_cues(segments: list[dict], language: str, log) -> list[dict]:
    """The covering transcript lines, translated into the production's language.

    An archive quote in English under Portuguese narration was being subtitled
    in English — the viewer reads the language they do not need help with. The
    times are the speech's own: only the text changes, so the subtitle still
    lands on the word being said.

    A failure returns the original lines. A film subtitled in the source
    language is worse than one subtitled in the target, and much better than
    one with no subtitles at all.
    """
    if not segments:
        return []
    numbered = "\n".join(f"{i}. {s['text'].strip()}"
                         for i, s in enumerate(segments) if s.get("text"))
    if not numbered.strip():
        return segments
    try:
        answer = llm.complete_json(
            _in_language(LEGENDA_SYSTEM, language),
            f"Falas transcritas:\n{numbered}",
            LEGENDA_SCHEMA, max_tokens=4000, purpose="legendas")
        by_index = {int(line["i"]): str(line["text"]).strip()
                    for line in answer.get("lines") or []
                    if str(line.get("text") or "").strip()}
    except Exception as exc:  # noqa: BLE001 — subtitles are not the film
        log(f"Subtitles kept in the source language ({exc})", "warn")
        return segments
    if not by_index:
        return segments
    return [{**seg, "text": by_index.get(i, seg["text"])}
            for i, seg in enumerate(segments)]


def _covering_segments(item: dict, start: float, end: float) -> list[dict]:
    """The transcript lines a cut actually contains."""
    return [seg for seg in (item.get("segments") or [])
            if seg.get("end", 0) > start and seg.get("start", 0) < end]


def _generate_shot_image(query: str, key: str, work: Path, options: dict,
                         log) -> Path | None:
    """A drawn still for a shot no stock bank could fill.

    Second choice, never first: the query was written to find real footage,
    and a generated frame is an illustration of the idea rather than a
    recording of it. It is still much closer to the intended shot than a card
    with the search terms printed on it, which is what this replaces.

    Returns None rather than raising — the placeholder card is still the
    fallback, and one shot must not take the production down.
    """
    dest = work / f"gen_{key}.png"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    style = options.get("style", "").strip()
    prompt = (f"{query}. Fotografia documental, iluminação natural, "
              f"enquadramento horizontal 16:9, sem texto e sem marca d'água."
              + (f" Estilo: {style}." if style else ""))
    try:
        return imagegen.generate_image(prompt, dest, aspect="16:9",
                                       log=lambda m, level="info": log(str(m), level))
    except Exception as exc:  # noqa: BLE001 — one shot, not the production
        log(f"Shot {key}: no image could be generated for '{query}' ({exc})",
            "warn")
        return None


def _fetch_stock(query: str, key: str, work: Path, log) -> Path | None:
    """One landscape stock clip for the query, moved into the job directory
    under the shot's name so the timeline keeps pointing at it after the
    b-roll scratch folder is cleared."""
    found = broll.fetch_for_queries([query], log, job_dir=work, per_query=1,
                                    landscape=True)
    if not found:
        return None
    dest = work / f"stock_{key}.mp4"
    shutil.move(str(found[0]), dest)
    return dest


class _Cutaways:
    """Windows of the user's own footage that no quote is using.

    The last resort before a title card, and better than one: with no stock
    bank and no image generator, the alternative to real footage is a slide
    with the search terms printed on it. The material is already on disk,
    already about the subject, and already paid for.

    Handed out in order and never twice, so two cards in a row do not become
    the same shot twice in a row. Windows overlapping a quote are excluded —
    replaying a sentence the film already used reads as a mistake — and so are
    the first and last stretches of each file, where intros and credits live.
    """

    EDGE = 5.0        # intros and end credits
    MARGIN = 2.0      # breathing room around a quote already on screen

    def __init__(self, catalog: dict[str, dict], blocks: list[dict]):
        used: dict[str, list[tuple[float, float]]] = {}
        for block in blocks:
            for shot in block.get("shots", []):
                if shot["kind"] in ("entrevista", "link") and shot.get("material"):
                    used.setdefault(shot["material"], []).append(
                        (float(shot["start"]), float(shot["end"])))
        self._free: list[tuple[str, float, float]] = []
        for item in catalog.values():
            if item.get("reference") or item.get("status") != "ok":
                continue
            if item["kind"] not in ("entrevista", "link") or not item.get("file"):
                continue
            duration = float(item.get("duration") or 0.0)
            if duration <= self.EDGE * 2 + 4:
                continue
            taken = sorted(used.get(item["id"], []))
            cursor = self.EDGE
            for start, end in taken + [(duration - self.EDGE, duration)]:
                free_end = start - self.MARGIN
                if free_end - cursor >= 4.0:
                    self._free.append((item["id"], cursor, free_end))
                cursor = max(cursor, end + self.MARGIN)
        self._offsets: dict[str, float] = {}

    def take(self, seconds: float) -> tuple[str, float, float] | None:
        """The next unused window at least `seconds` long, consumed."""
        wanted = max(seconds, 2.0)
        for index, (material, start, end) in enumerate(self._free):
            used_from = self._offsets.get(f"{material}:{start}", start)
            if end - used_from < wanted:
                continue
            self._offsets[f"{material}:{start}"] = used_from + wanted
            if end - (used_from + wanted) < 4.0:
                self._free.pop(index)
            return material, round(used_from, 2), round(used_from + wanted, 2)
        return None


def _cutaway(shot: dict, key: str, work: Path, source_dir: Path,
             catalog: dict[str, dict], cutaways: "_Cutaways | None",
             log) -> Path | None:
    """A muted stretch of the user's own footage covering this shot."""
    if cutaways is None:
        return None
    window = cutaways.take(float(shot["seconds"]))
    if window is None:
        return None
    material, start, end = window
    item = catalog[material]
    src = source_dir / item["file"]
    if not src.exists():
        return None
    dest = work / f"cover_{key}.mp4"
    log(f"Shot {key}: no stock for '{shot.get('query', '')}' — covering with "
        f"{material} {_fmt_time(start)}-{_fmt_time(end)} (no audio)")
    try:
        _cut_segment(src, start, end, dest)
    except Exception as exc:  # noqa: BLE001 — the card is still the floor
        log(f"Shot {key}: could not cut the cover ({exc})", "warn")
        return None
    return dest


def _skip_black_open(source: Path, shot: dict, key: str, dest: Path,
                     duration: float, log) -> Path:
    """Re-cut past a dip to black the source itself opens with.

    The transcript cannot show this: someone talks over a title card or a
    chapter transition, so the window passes every check and then plays as
    five seconds of a broken player. Measured on the cut and fixed by sliding
    the window forward, keeping its length, as long as the material has room.

    The shot dict is updated in place so the subtitles are read from the
    window that was actually used and not the one that was asked for.
    """
    black = render.leading_black(dest)
    if black < 0.4:
        return dest
    length = float(shot["end"]) - float(shot["start"])
    start = float(shot["start"]) + black + 0.2
    end = start + length
    if duration and end > duration:
        # No room to slide: the shot keeps what it has, and the QA report is
        # what says the picture goes dark there.
        log(f"Shot {key}: opens on {black:.1f}s of black and the material ends "
            f"at {_fmt_time(duration)} — no room to move the window", "warn")
        return dest
    log(f"Shot {key}: {black:.1f}s of black at the start of the window; moved to "
        f"{_fmt_time(start)}-{_fmt_time(end)}")
    shot["start"], shot["end"] = round(start, 2), round(end, 2)
    return _cut_segment(source, start, end, dest)


def _cut_segment(source: Path, start: float, end: float, dest: Path) -> Path:
    """An interview excerpt framed for the 16:9 frame, audio kept.

    The source's own baked-in bars are stripped first (`content_crop`) and the
    picture fitted to the frame — a phone recording of an interviewee comes
    out centred over a blurred copy of itself, not stretched.
    """
    duration = max(end - start, 0.5)
    debar = render.content_crop(source)
    render._run([  # noqa: SLF001 — the project's one ffmpeg runner
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}", "-vf", debar + render.fit_filter(FRAME),
        "-r", str(settings.fps), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", str(dest)])
    return dest


def _has_audio(video: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True)
    return "audio" in out.stdout


def _thumbnail(video: Path, out: Path, at: float = 1.0) -> Path:
    """`render.make_thumbnail` scales to the short's 1080x1920; a 16:9 frame
    squeezed into that is a squashed thumbnail."""
    render._run(["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(video),  # noqa: SLF001
                 "-frames:v", "1", "-vf", f"scale={FRAME.width}:{FRAME.height}",
                 str(out)])
    return out


def _palette(options: dict) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    def rgb(value: str) -> tuple[int, int, int]:
        v = int(value, 16)
        return (v >> 16) & 255, (v >> 8) & 255, v & 255
    c0, c1 = broll.palette(options.get("niche", "generico"))
    return rgb(c0), rgb(c1)


def _text_font(size: int):
    try:
        return overlays._font(size)  # noqa: SLF001 — the project's font finder
    except Exception:  # noqa: BLE001 — no TrueType font on this machine
        try:
            return ImageFont.load_default(size)
        except TypeError:   # Pillow < 10.1 takes no size
            return ImageFont.load_default()


def _card(text: str, dest: Path, options: dict, subtitle: str = "") -> Path:
    """A full-frame title card: the niche's palette, the text centred.

    Used for `cartela` shots and as the placeholder for anything that could not
    be prepared — the timing stays intact and the user replaces just that clip
    in the editor.
    """
    dark, accent = _palette(options)
    image = Image.new("RGB", (FRAME.width, FRAME.height), dark)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, FRAME.height - 14, FRAME.width, FRAME.height], fill=accent)

    font = _text_font(72)
    lines = textwrap.wrap(text.strip() or " ", width=34)[:5]
    heights = [draw.textbbox((0, 0), line, font=font)[3] for line in lines]
    block_height = sum(heights) + 18 * (len(lines) - 1)
    y = (FRAME.height - block_height) // 2 - (40 if subtitle else 0)
    for line, height in zip(lines, heights):
        width = draw.textbbox((0, 0), line, font=font)[2]
        draw.text(((FRAME.width - width) // 2, y), line, font=font, fill=(245, 245, 245))
        y += height + 18
    if subtitle:
        small = _text_font(34)
        width = draw.textbbox((0, 0), subtitle, font=small)[2]
        draw.text(((FRAME.width - width) // 2, y + 24), subtitle, font=small,
                  fill=(180, 180, 190))
    image.save(dest)
    return dest


def _lower_third(text: str, dest: Path) -> Path:
    """The name strip over an interviewee — a translucent bar with the text,
    laid over the cut as a `MediaOverlay` so the editor can move or drop it."""
    width, height = 900, 150
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, height], fill=(10, 12, 18, 200))
    draw.rectangle([0, 0, 12, height], fill=(255, 200, 0, 255))
    lines = textwrap.wrap(text.strip(), width=36)[:2]
    font = _text_font(44 if len(lines) == 1 else 36)
    y = 30 if len(lines) == 1 else 22
    for line in lines:
        draw.text((40, y), line, font=font, fill=(245, 245, 245, 255))
        y += 52
    image.save(dest)
    return dest


def _load_words(work: Path, entry: dict | None) -> list[dict]:
    if not entry or not entry.get("words_file"):
        return []
    path = work / entry["words_file"]
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _cues_from_words(words: list[dict], offset: float) -> list[CaptionCue]:
    """Subtitle cues for a 16:9 frame: whole sentences, not the four-word
    bursts a phone gets."""
    cues = []
    for line in captions_mod.group_lines(words, max_words=FRAME.caption_max_words,
                                         max_seconds=5.0,
                                         max_chars=FRAME.caption_max_chars):
        cues.append(CaptionCue(
            id=timeline_mod._new_id("c"),  # noqa: SLF001
            text=" ".join(w["word"] for w in line),
            start=round(offset + line[0]["start"], 3),
            end=round(offset + line[-1]["end"], 3)))
    return cues


def build_timeline(work: Path, blocks: list[dict], ledger: dict[str, dict],
                   catalog: dict[str, dict], options: dict) -> Timeline:
    """Everything prepared, laid out as an editable HORIZONTAL timeline.

    Block by block, in script order: the block's shots on the video track, its
    narration on the audio track starting exactly where the block starts —
    "a narração encaixada no momento certo" — the interview audio under its own
    cut, subtitles for both, lower thirds as overlays. The result is a plain
    `Timeline`, which is what makes a finished documentary editable in the
    existing editor without going back through the LLM or the TTS.
    """
    video: list[VideoClip] = []
    audio: list[AudioClip] = []
    cues: list[CaptionCue] = []
    media: list[MediaOverlay] = []

    def is_image(name: str) -> bool:
        return Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

    cursor = 0.0
    for block in blocks:
        block_start = cursor
        placed: list[tuple[VideoClip, dict]] = []

        for n, shot in enumerate(block.get("shots", [])):
            entry = ledger.get(_shot_key(block, n))
            if entry is None or not entry.get("file") or not (work / entry["file"]).exists():
                continue
            length = max(float(entry.get("seconds") or shot["seconds"]), 0.5)
            spoken = shot["kind"] in ("entrevista", "link") and entry.get("status") == "ok"
            clip = VideoClip(
                id=timeline_mod._new_id("v"),  # noqa: SLF001
                source=entry["file"], in_point=0.0, out_point=round(length, 3),
                start=round(cursor, 3),
                kind="image" if is_image(entry["file"]) else "video",
                mute=not (spoken and entry.get("has_audio")),
            )
            video.append(clip)
            placed.append((clip, shot))

            if spoken and entry.get("has_audio"):
                audio.append(AudioClip(
                    id=timeline_mod._new_id("a"),  # noqa: SLF001
                    source=entry["file"], in_point=0.0, out_point=round(length, 3),
                    start=round(cursor, 3), gain=1.0, role="entrevista"))
            if spoken and options["captions"]:
                # The translated cues if the cut has them (times already
                # relative to the cut), the raw transcript otherwise — an
                # older ledger, from before subtitles were translated, still
                # renders instead of losing its subtitles.
                if entry.get("captions"):
                    lines = [(c["start"], c["end"], c["text"])
                             for c in entry["captions"]]
                else:
                    item = catalog.get(shot["material"]) or {}
                    lines = [(max(seg["start"] - shot["start"], 0.0),
                              min(seg["end"], shot["end"]) - shot["start"],
                              seg["text"])
                             for seg in _covering_segments(
                                 item, shot["start"], shot["end"])]
                for begin, finish, text in lines:
                    cues.append(CaptionCue(
                        id=timeline_mod._new_id("c"),  # noqa: SLF001
                        text=text, start=round(cursor + begin, 3),
                        end=round(cursor + finish, 3)))
            if entry.get("lower_third") and (work / entry["lower_third"]).exists():
                media.append(MediaOverlay(
                    id=timeline_mod._new_id("m"),  # noqa: SLF001
                    source=entry["lower_third"], start=round(cursor + 0.5, 3),
                    end=round(cursor + min(length, 6.5), 3),
                    x=0.27, y=0.86, width=0.42, kind="image"))
            cursor += length
        video_end = cursor

        # The narration lands at the block's start, whatever the footage does.
        narration = ledger.get(_narration_key(block))
        spoken_len = 0.0
        if block.get("narration") and block.get("narrator") != "nenhum":
            has_audio = (narration is not None and narration.get("status") == "ok"
                         and (work / narration.get("file", "")).exists()
                         and float(narration.get("seconds") or 0) > 0)
            if has_audio:
                spoken_len = float(narration["seconds"])
                audio.append(AudioClip(
                    id=timeline_mod._new_id("a"),  # noqa: SLF001
                    source=narration["file"], in_point=0.0,
                    out_point=round(spoken_len, 3), start=round(block_start, 3),
                    gain=1.0, role="narration"))
                if options["captions"]:
                    words = _load_words(work, narration)
                    if words:
                        cues.extend(_cues_from_words(words, block_start))
                    else:
                        cues.append(CaptionCue(id=timeline_mod._new_id("c"),  # noqa: SLF001
                                               text=block["narration"], start=round(block_start, 3),
                                               end=round(block_start + spoken_len, 3)))
            else:
                # No audio for this block: the text still shows, sized by the
                # reading speed, so a failed synthesis does not silently drop
                # the narration out of the film.
                spoken_len = max(len(block["narration"].split()) / NARRATION_WORDS_PER_SECOND, 1.5)
                cues.extend(_cues_from_words(
                    tts.estimate_words(block["narration"], spoken_len), block_start))

        # Narration routinely outlasts the footage planned for it. Rather than
        # cutting to black, the last shot is held — the renderer loops a clip
        # whose out_point runs past its source — unless that shot is an
        # interview, whose loop would replay the quote: then a card holds.
        block_end = max(video_end, block_start + spoken_len)
        if block_end > video_end + 0.04:
            gap = block_end - video_end
            if placed and placed[-1][1]["kind"] not in ("entrevista", "link", "avatar"):
                placed[-1][0].out_point = round(placed[-1][0].out_point + gap, 3)
            else:
                hold = ledger.get(_hold_key(block))
                if hold and hold.get("file") and (work / hold["file"]).exists():
                    video.append(VideoClip(
                        id=timeline_mod._new_id("v"),  # noqa: SLF001
                        source=hold["file"], in_point=0.0, out_point=round(gap, 3),
                        start=round(video_end, 3), kind="image"))
        cursor = block_end

    return Timeline(
        duration=round(cursor, 3), video=video, audio=audio, captions=cues, media=media,
        caption_style=options["caption_style"],
        caption_position=options["caption_position"],
        watermark=options["watermark"],
        format=FRAME.name,
    ).normalize()


def _blocks_report(blocks: list[dict], ledger: dict[str, dict]) -> list[dict]:
    """Per block, what happened — the result the API shows next to the video."""
    report = []
    for block in blocks:
        shots = []
        for n, shot in enumerate(block.get("shots", [])):
            entry = ledger.get(_shot_key(block, n)) or {}
            shots.append({"kind": shot["kind"], "status": entry.get("status", "missing"),
                          "error": entry.get("error", ""), "file": entry.get("file", ""),
                          "seconds": entry.get("seconds", shot["seconds"])})
        narration = ledger.get(_narration_key(block)) or {}
        status = "ok"
        if any(s["status"] != "ok" for s in shots) or (
                block.get("narration") and block.get("narrator") != "nenhum"
                and narration.get("status") != "ok"):
            status = "partial"
        report.append({"key": block["key"], "kind": block["kind"],
                       "title": block.get("title", ""), "status": status,
                       "narration": narration.get("status", "none"),
                       "narration_error": narration.get("error", ""),
                       "shots": shots})
    return report
