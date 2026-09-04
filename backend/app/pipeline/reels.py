"""Editing a reel you recorded yourself.

Everything else in this project *generates* a video: an LLM writes a script, TTS
speaks it, and the captions come from the timings TTS reported. This module is
the other direction — you already have the footage and your own voice, and what
you want is the editing: captions synced to what you actually said, extra media
laid over you while you talk, and cuts.

So there is no script stage and no TTS stage here. The audio is yours, and the
word timings come from transcribing it. What it produces on disk is deliberately
the *same* set of artifacts a generated short produces (`short.mp4`,
`captions.ass`, `timeline.json`, `thumb.jpg`, and `words` in the job result), so
the timeline editor, QA, the cover builder, publishing and metrics all keep
working on a reel with nothing special-cased.

The source is either a file you uploaded or a link — a reel you already posted,
a talk, a recording elsewhere. `ingest` already knows how to fetch those.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from . import captions as captions_mod
from . import ingest, llm, notify, render, timeline as timeline_mod, timeline_render
from .timeline import AudioClip, Timeline, VideoClip

# What `JobInput.edit_mode` is set to for a recording of your own. The job
# queue reads it to route here instead of through script + TTS.
MODE = "meu_video"

# The reel is the user's own performance, so its length is theirs to choose.
# We only warn past what a feed will take.
MAX_SECONDS = 180


@dataclass
class Reel:
    """What the analysis found in the recording, before any editing."""
    source: Path
    duration: float
    words: list[dict]
    language: str = ""

    @property
    def transcript(self) -> str:
        return " ".join(w["word"] for w in self.words).strip()


# ------------------------------------------------------------- transcription

def transcribe_words(video: Path, log=lambda m, level="info": None) -> tuple[list[dict], str]:
    """Word-level timings for the speech in a recording.

    `ingest.whisper_segments` returns whole sentences, which is enough for the
    clipper (it only needs to know roughly where a moment sits). Captions on a
    reel are read while the person speaks, so a sentence-level timing is
    visibly wrong — the line appears a beat late and hangs a beat long. Hence
    `word_timestamps=True` here, which costs more but is the whole point.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "Captioning your own recording needs transcription. Install "
            "faster-whisper (pip install faster-whisper) and try again.")

    model = WhisperModel(settings.reels_whisper_model, device="cpu",
                         compute_type="int8")
    segments, info = model.transcribe(str(video), language=None,
                                      vad_filter=True, word_timestamps=True)

    words: list[dict] = []
    for segment in segments:
        # A segment can come back with no words at all (music, noise); its text
        # would then have no timing to attach to, so it is dropped rather than
        # guessed at.
        for word in (segment.words or []):
            text = word.word.strip()
            if not text:
                continue
            words.append({"word": text,
                          "start": round(float(word.start), 3),
                          "end": round(float(word.end), 3)})

    language = getattr(info, "language", "") or ""
    log(f"Transcription: {len(words)} timed words"
        + (f", speech detected as {language}" if language else ""))
    if not words:
        log("No speech found in the recording — it will be captioned only if "
            "you write the lines yourself in the editor.", "warn")
    return words, language


def extract_audio(video: Path, out: Path) -> Path:
    """The original audio as its own file, so the timeline can carry it as a
    clip like any other — that is what lets the editor move, trim and mix it
    against music without touching the video track."""
    render._run(["ffmpeg", "-y", "-i", str(video), "-vn",  # noqa: SLF001
                 "-c:a", "libmp3lame", "-q:a", "2", "-ar", "48000",
                 "-ac", "2", str(out)])
    return out


# -------------------------------------------------------------- the timeline

def build_timeline(job_dir: Path, reel: Reel, *, caption_style: str,
                   caption_position: str, watermark: str = "",
                   watermark_position: str = "baixo_centro",
                   watermark_size: str = "medio",
                   watermark_opacity: float = 0.6,
                   keep_audio: bool = True) -> Timeline:
    """The starting edit: the whole recording, its own audio, its own words.

    Everything after this is the user dragging things around in the editor.
    """
    video = [VideoClip(id=timeline_mod._new_id("v"),  # noqa: SLF001
                       source="reel_919.mp4", in_point=0.0,
                       out_point=round(reel.duration, 3), start=0.0)]

    audio: list[AudioClip] = []
    if keep_audio:
        audio.append(AudioClip(
            id=timeline_mod._new_id("a"),  # noqa: SLF001
            source="reel_audio.mp3", in_point=0.0,
            out_point=round(reel.duration, 3), start=0.0,
            gain=1.0, role="narration"))

    return Timeline(
        duration=round(reel.duration, 3),
        video=video, audio=audio,
        captions=timeline_mod.captions_from_words(reel.words),
        caption_style=caption_style, caption_position=caption_position,
        watermark=watermark, watermark_position=watermark_position,
        watermark_size=watermark_size, watermark_opacity=watermark_opacity,
    ).normalize()


def prepare(job_dir: Path, source: Path, log=lambda m, level="info": None) -> Reel:
    """Turn a raw recording into something the editor can work on: framed 9:16,
    audio on its own, and every word timed."""
    job_dir.mkdir(parents=True, exist_ok=True)

    duration = render.probe_duration(source)
    if duration <= 0:
        raise RuntimeError("Could not read the length of this video. It may be "
                           "corrupt or in a format FFmpeg does not handle.")
    if duration > MAX_SECONDS:
        log(f"The recording is {duration:.0f}s long. Kept whole — trim it in the "
            f"editor before publishing, since no feed accepts a reel this long.",
            "warn")

    words, language = transcribe_words(source, log)
    extract_audio(source, job_dir / "reel_audio.mp3")

    # Vertical framing happens once, up front: the editor previews and renders
    # against this file, so a 16:9 recording behaves the same as one already
    # shot vertically.
    framed = render.background_from_video(source, duration,
                                          job_dir / "reel_919.mp4")
    log(f"Recording framed to 9:16: {framed.name}, {duration:.1f}s")

    return Reel(source=source, duration=duration, words=words, language=language)


# ------------------------------------------------------- the intelligence bit

VIRALITY_SYSTEM = (
    "Você é editor de conteúdo curto vertical (Reels, Shorts, TikTok) e conhece "
    "o que segura atenção nos primeiros três segundos. Você recebe a "
    "transcrição real de um vídeo que a pessoa gravou e sugere edições "
    "concretas, ancoradas em tempos que existem na transcrição. "
    "Você nunca inventa um tempo que não está lá. "
    "Você prefere um corte que salva o vídeo a dez sugestões cosméticas. "
    "Responda somente JSON."
)

SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "virality": {
            "type": "object",
            "properties": {
                "score": {"type": "integer"},
                "why": {"type": "string"},
                "biggest_risk": {"type": "string"},
            },
            "required": ["score", "why", "biggest_risk"],
        },
        "hooks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"},
                               "why": {"type": "string"}},
                "required": ["text", "why"],
            },
        },
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"start": {"type": "number"},
                               "end": {"type": "number"},
                               "why": {"type": "string"}},
                "required": ["start", "end", "why"],
            },
        },
        "media": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "what": {"type": "string"},
                    "image_prompt": {"type": "string"},
                    "stock_query": {"type": "string"},
                },
                "required": ["start", "end", "what", "image_prompt", "stock_query"],
            },
        },
        "captions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"start": {"type": "number"},
                               "text": {"type": "string"},
                               "why": {"type": "string"}},
                "required": ["start", "text", "why"],
            },
        },
        "title": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["virality", "hooks", "cuts", "media", "captions",
                 "title", "hashtags"],
}


def _timed_transcript(words: list[dict], every: float = 3.0) -> str:
    """The transcript with a timestamp every few seconds.

    Without visible timings the model has nothing to anchor a suggestion to and
    returns advice instead of edits ("start stronger"), which the editor cannot
    apply to anything.
    """
    lines: list[str] = []
    marker = -every
    chunk: list[str] = []
    for word in words:
        if word["start"] >= marker + every:
            if chunk:
                lines.append(" ".join(chunk))
                chunk = []
            marker = word["start"]
            chunk.append(f"[{int(marker // 60):02d}:{marker % 60:04.1f}]")
        chunk.append(word["word"])
    if chunk:
        lines.append(" ".join(chunk))
    return "\n".join(lines)


def suggest(reel: Reel, instruction: str = "", niche: str = "generico",
            language: str = "pt-BR") -> dict:
    """Ask for concrete edits to this recording, aimed at holding attention.

    `instruction` is the user's own words about what they are going for — this
    is the "send it a prompt and it helps" part, and it is the one input that
    changes the answer most, so it goes near the top of the prompt where it is
    least likely to be skimmed past.
    """
    if not reel.words:
        raise RuntimeError(
            "There is no transcription for this recording, so there is nothing "
            "to base a suggestion on. Check that the video has audible speech.")

    from .script import language_name

    wanted = instruction.strip()
    prompt = f"""Vídeo gravado pela própria pessoa, {reel.duration:.0f} segundos, nicho {niche}.

{"O QUE A PESSOA PEDIU (prioridade máxima): " + wanted if wanted else
 "A pessoa não deu instrução específica: proponha o que mais aumenta retenção."}

TRANSCRIÇÃO COM TEMPOS REAIS:
{_timed_transcript(reel.words)}

Devolva JSON com:
- "virality": {{"score": 0-100, "why": por que esse número, "biggest_risk": o que mais faz perder a audiência}}
- "hooks": até 3 aberturas alternativas mais fortes que a atual, cada uma com "text" e "why"
- "cuts": trechos para remover (arrasto, repetição, silêncio, tangente), com "start", "end" e "why". Use só tempos que existem acima.
- "media": onde entra imagem ou vídeo sobreposto para sustentar o que está sendo dito, com "start", "end", "what" (o que mostrar), "image_prompt" (prompt pronto para gerar essa imagem) e "stock_query" (busca curta em banco de vídeo, em inglês)
- "captions": legendas reescritas onde a fala ficou confusa, com "start", "text" e "why"
- "title": título do post
- "hashtags": 6 a 10 hashtags

Regras:
- "start" e "end" em segundos, dentro de 0 e {reel.duration:.1f}.
- Não sugira corte que remova a explicação principal só para encurtar.
- Se o vídeo já está bom em algum aspecto, devolva lista vazia para aquele aspecto em vez de inventar sugestão fraca.
- Todo texto voltado ao público (hooks, captions, title, hashtags) em {language_name(language)}."""

    data = llm.complete_json(VIRALITY_SYSTEM, prompt, SUGGEST_SCHEMA,
                             max_tokens=6000, purpose="reels_suggest")
    return _clean_suggestions(data, reel.duration)


def _clean_suggestions(data: dict, duration: float) -> dict:
    """Drop what cannot be applied.

    A model asked for timestamps will occasionally return one past the end of
    the video, and a suggestion the editor cannot place is worse than no
    suggestion — the user clicks it and nothing happens.
    """
    def spans(key: str, needs: tuple[str, ...]) -> list[dict]:
        kept = []
        for item in data.get(key) or []:
            if not isinstance(item, dict) or any(k not in item for k in needs):
                continue
            try:
                start = max(float(item["start"]), 0.0)
                end = min(float(item.get("end", start)), duration)
            except (TypeError, ValueError):
                continue
            if key == "captions":
                if start >= duration:
                    continue
            elif end - start < 0.2:
                continue
            item["start"], item["end"] = round(start, 2), round(end, 2)
            kept.append(item)
        return kept

    virality = data.get("virality") or {}
    try:
        score = min(max(int(virality.get("score", 0)), 0), 100)
    except (TypeError, ValueError):
        score = 0

    return {
        "virality": {"score": score,
                     "why": str(virality.get("why", "")),
                     "biggest_risk": str(virality.get("biggest_risk", ""))},
        "hooks": [h for h in (data.get("hooks") or [])
                  if isinstance(h, dict) and h.get("text")][:3],
        "cuts": spans("cuts", ("start", "end", "why")),
        "media": spans("media", ("start", "end", "what")),
        "captions": spans("captions", ("start", "text")),
        "title": str(data.get("title", "")),
        "hashtags": [h if h.startswith("#") else f"#{h}"
                     for h in (data.get("hashtags") or []) if str(h).strip()][:12],
    }


# ---------------------------------------------------------------- the source

def fetch_source(job, job_dir: Path, log) -> tuple[Path, str]:
    """Get the recording itself — an upload, or a link to one.

    Deliberately NOT `ingest.ingest()`. That builds the LLM's reading material,
    and for a video with no subtitles it does that by running whisper over the
    whole file to produce one flat string. Nothing here reads that string: we
    transcribe again straight after, with word timings. Going through it would
    transcribe the recording twice, and on a long one that is minutes of CPU
    spent on a value nobody looks at.
    """
    from ..routers import uploads as uploads_router

    if job.attachments:
        try:
            source = uploads_router.resolve(job.attachments[0])
        except FileNotFoundError:
            raise RuntimeError(
                "The uploaded recording is no longer on disk. Send the file "
                "again.")
        log(f"Recording: {source.name}")
        return source, source.stem

    url = (job.source or "").strip()
    if not url:
        raise RuntimeError(
            "No recording was received. Upload a video file or paste the link "
            "to one.")

    log(f"Downloading the recording from {url}")
    source, info = ingest.download_video(url, job_dir)
    if source is None:
        raise RuntimeError(
            f"Could not download the video at {url}. Check the link, and that "
            f"yt-dlp is installed and up to date.")
    return source, str(info.get("title") or "Reel")


# ------------------------------------------------------------------- the run

def run(job_id: str, job, job_dir: Path, log, stage) -> dict:
    """The whole flow for a recording of your own: prepare, caption, render.

    Deliberately short. Anything the user might want to change afterwards
    belongs in the timeline editor, not in another pass through here.
    """
    from .. import db
    from . import qa as qa_mod

    render.ensure_ffmpeg()

    stage("ingest")
    source, fetched_title = fetch_source(job, job_dir, log)
    # A name the user typed when creating the reel wins over the file's or the
    # video host's — they named it for a reason.
    title = ((db.get_job(job_id) or {}).get("title") or "").strip() or fetched_title

    stage("legendas")
    reel = prepare(job_dir, source, log)

    caption_words = reel.words
    ass_path = captions_mod.build_ass(
        caption_words, job_dir / "captions.ass",
        style=job.caption_style, position=job.caption_position,
        title="", watermark=job.watermark,
        watermark_position=job.watermark_position,
        watermark_size=job.watermark_size,
        watermark_opacity=job.watermark_opacity)
    captions_mod.build_srt(caption_words, job_dir / "captions.srt")

    edl = build_timeline(
        job_dir, reel,
        caption_style=job.caption_style, caption_position=job.caption_position,
        watermark=job.watermark, watermark_position=job.watermark_position,
        watermark_size=job.watermark_size, watermark_opacity=job.watermark_opacity,
        keep_audio=job.keep_audio)
    if not job.keep_audio:
        log("Delivered silent, captions only — the recording's audio was left out.")
    timeline_mod.save(job_dir, edl)

    stage("render")
    final = job_dir / "short.mp4"
    timeline_render.render_timeline(job_dir, edl, final, log=lambda m: log(m))
    render.make_thumbnail(final, job_dir / "thumb.jpg",
                          at=min(1.0, edl.duration / 4))

    stage("qa")
    report = qa_mod.audit(final, ass_path, expected_duration=reel.duration)
    log(f"QA: score {report.score}/100 — "
        f"{'PASSED' if report.passed else 'FAILED'}")

    shutil.copy(final, settings.outputs_dir / f"{job_id}.mp4")

    result = {
        "mode": MODE,
        "title": title,
        "description": "",
        "hashtags": [],
        "duration": round(edl.duration, 2),
        "video": f"/api/jobs/{job_id}/file/short.mp4",
        "thumbnail": f"/api/jobs/{job_id}/file/thumb.jpg",
        "captions_srt": f"/api/jobs/{job_id}/file/captions.srt",
        # The editor and the caption builder both read `words`; a reel's come
        # from transcribing the person, not from TTS, but the shape is the same
        # so nothing downstream needs to know the difference.
        "words": reel.words,
        "transcript_language": reel.language,
        "source_kind": "video",
        "edit_mode": job.edit_mode,
    }
    (job_dir / "reel.json").write_text(
        json.dumps({"duration": reel.duration, "language": reel.language,
                    "words": reel.words}, ensure_ascii=False),
        encoding="utf-8")

    db.update_job(job_id, status="done", stage="qa", progress=1.0, title=title,
                  result_json=json.dumps(result, ensure_ascii=False),
                  qa_json=report.model_dump_json())
    notify.job_done(job_id, title, edl.duration, report.score, report.passed)
    return result


def load(job_dir: Path) -> Reel | None:
    """The analysis saved by `run`, so `suggest` can be asked again later
    without transcribing the video a second time."""
    path = job_dir / "reel.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Reel(source=job_dir / "reel_919.mp4",
                duration=float(data.get("duration", 0.0)),
                words=data.get("words") or [],
                language=data.get("language", ""))
