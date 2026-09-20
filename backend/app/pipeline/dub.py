"""Recycling a video: the same picture, speaking another language.

This is not the narration pipeline with a different prompt. There is no script
to write — the words already exist, someone said them — so the flow is
transcribe, translate, speak, and lay the new voice over the original timing:

    transcribe (whisper, timed)  ->  translate line by line  ->  TTS per line
    ->  each line placed at the second the original line starts

Placing per line rather than dubbing the whole text in one go is the difference
between a dub and a voice-over that drifts: by the third minute a single track
is seconds away from the mouth it belongs to. A line that comes out longer than
its slot is sped up slightly to fit; past what speeding can hide, it is allowed
to run over and the log says so, because a rushed, unintelligible line is worse
than a late one.

The original audio stays underneath, ducked. It is what makes this read as a
dub of something real instead of a stranger talking over a muted clip — the
music, the laugh, the room are all still there.

Ownership is not decided here. Translating and republishing a video you did not
make is a copyright and platform-rules question; the author and the source link
are carried into the job's result so crediting is the easy path.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings
from . import (captions as captions_mod, llm, notify, reels, render,
               script as script_mod, timeline as timeline_mod, timeline_render,
               tts)
from .timeline import AudioClip, CaptionCue, Timeline, VideoClip

# What `JobInput.edit_mode` carries for a dub. The orchestrator reads it and
# hands the job over here, the way it does for reels and avatars.
MODE = "dublar"

# The original, kept audible under the new voice. Loud enough that the music
# and the room are still there, quiet enough that two languages at once do not
# fight: broadcast dubbing sits around here.
ORIGINAL_GAIN = 0.14

# How much a line may be sped up to fit its slot. Past this the speech starts
# sounding like a disclaimer, and a late line is better than an unintelligible
# one.
MAX_TEMPO = 1.35
# Below this, fitting is not worth an extra encode.
TEMPO_FLOOR = 1.08

# Lines per translation request. One request for everything loses the pairing
# between line and timing on a long video; one request per line spends forty
# calls on a two-minute clip.
BATCH = 25

TRANSLATION_SCHEMA = {
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

TRANSLATION_SYSTEM = """Você dubla vídeos curtos.

Recebe as falas transcritas de um vídeo, numeradas e na ordem, e devolve cada
uma em {language}, para ser DITA EM VOZ ALTA por cima da fala original.

REGRAS
- Uma linha de saída para cada linha de entrada, com o mesmo número `i`.
- Não junte nem divida linhas: cada uma ocupa o tempo exato da fala original.
- Cada linha tem que caber no tempo da original. Prefira a formulação mais
  curta que diga a mesma coisa; corte muleta ("tipo", "então", "sabe"), não
  conteúdo.
- É fala, não legenda: escreva como alguém diria, com as contrações e o ritmo
  de quem fala. Sem literalidade dura.
- Mantenha o tom de quem falou — gíria vira gíria, palavrão vira palavrão,
  piada continua piada.
- Nomes próprios, marcas e números ficam como estão.
- Se a linha já estiver em {language}, devolva igual.
- Sem aspas ao redor, sem comentários, sem "tradução:"."""


@dataclass
class Line:
    """One spoken line: where it happens, what it said, what it now says."""
    start: float
    end: float
    original: str
    text: str = ""
    audio: Path | None = None
    seconds: float = 0.0
    tempo: float = 1.0

    @property
    def slot(self) -> float:
        return max(self.end - self.start, 0.3)


@dataclass
class Dubbed:
    source: Path
    duration: float
    language: str
    lines: list[Line] = field(default_factory=list)
    words: list[dict] = field(default_factory=list)
    title: str = ""
    url: str = ""
    author: str = ""


def transcribe(source: Path, log) -> tuple[list[Line], str, list[dict]]:
    """The original's spoken lines, with their timings.

    Word timings come along because the captions are read while the dub is
    spoken, and a sentence-level timing is visibly late on a nine-second clip.
    """
    words, language = reels.transcribe_words(source, log)
    lines = [Line(start=float(seg["start"]), end=float(seg["end"]),
                  original=str(seg["text"]).strip())
             for seg in _segments_from(words) if str(seg["text"]).strip()]
    return lines, language, words


def _segments_from(words: list[dict]) -> list[dict]:
    """Group timed words into spoken lines.

    A gap is where one line ends: people pause between sentences, and a dub
    that respects those pauses lands on the mouth. Punctuation alone is not
    enough — whisper's word list often carries none.
    """
    segments: list[dict] = []
    current: list[dict] = []
    for word in words:
        if current:
            gap = float(word["start"]) - float(current[-1]["end"])
            long_enough = (float(current[-1]["end"]) - float(current[0]["start"])) > 2.0
            ends_sentence = current[-1]["word"].rstrip().endswith((".", "!", "?", "…"))
            if gap > 0.45 or (ends_sentence and long_enough):
                segments.append(_as_segment(current))
                current = []
        current.append(word)
    if current:
        segments.append(_as_segment(current))
    return segments


def _as_segment(words: list[dict]) -> dict:
    return {"start": float(words[0]["start"]), "end": float(words[-1]["end"]),
            "text": " ".join(w["word"] for w in words).strip()}


def translate(lines: list[Line], language: str, log) -> None:
    """Fill each line's `text`, in batches, in place.

    A batch that fails leaves its lines in the original language rather than
    taking the job down: a dub with three untranslated lines is a fixable
    video, and a failed render is not.
    """
    target = script_mod.language_name(language)
    for start in range(0, len(lines), BATCH):
        chunk = lines[start:start + BATCH]
        numbered = "\n".join(f"{n}. {line.original}"
                             for n, line in enumerate(chunk))
        try:
            answer = llm.complete_json(
                TRANSLATION_SYSTEM.replace("{language}", target),
                f"Falas do vídeo, em ordem:\n{numbered}\n\n"
                f"Devolva cada uma em {target}, no mesmo número.",
                TRANSLATION_SCHEMA, max_tokens=4000, purpose="dublagem")
            by_index = {int(item["i"]): str(item["text"]).strip()
                        for item in answer.get("lines") or []
                        if str(item.get("text") or "").strip()}
        except Exception as exc:  # noqa: BLE001 — one batch, not the video
            log(f"Lines {start + 1}-{start + len(chunk)} were not translated "
                f"({exc}) — they stay in the original language.", "warn")
            by_index = {}
        for n, line in enumerate(chunk):
            line.text = by_index.get(n, line.original)


def synthesize(lines: list[Line], job_dir: Path, voice: dict | None,
               language: str, log) -> None:
    """Speak every line, and make it fit where it belongs."""
    work = job_dir / "dub"
    work.mkdir(parents=True, exist_ok=True)
    stretched = 0
    overflowed = 0

    for index, line in enumerate(lines):
        if not line.text.strip():
            continue
        raw = work / f"line_{index:03d}.mp3"
        narration = tts.synthesize(line.text, raw, voice,
                                   lambda m, level="info": log(str(m), level),
                                   language=language)
        seconds = narration.duration
        line.audio, line.seconds = narration.audio_path, seconds

        if seconds > line.slot * TEMPO_FLOOR:
            tempo = min(seconds / line.slot, MAX_TEMPO)
            fitted = work / f"line_{index:03d}_fit.mp3"
            _speed_up(narration.audio_path, tempo, fitted)
            line.audio, line.tempo = fitted, tempo
            line.seconds = tts.audio_duration(fitted)
            stretched += 1
            if line.seconds > line.slot + 0.25:
                overflowed += 1

    if stretched:
        log(f"{stretched} line(s) sped up to fit the original timing "
            f"(up to {MAX_TEMPO:.2f}x)")
    if overflowed:
        log(f"{overflowed} line(s) still run past their slot — the translation "
            f"needs more words than the original. They overlap slightly rather "
            f"than being rushed past understanding.", "warn")


def _speed_up(source: Path, tempo: float, dest: Path) -> Path:
    """atempo, which changes speed without touching pitch — a resample would
    turn the voice into a chipmunk."""
    render._run([  # noqa: SLF001 — the project's one ffmpeg runner
        "ffmpeg", "-y", "-i", str(source), "-filter:a", f"atempo={tempo:.3f}",
        "-c:a", "libmp3lame", "-q:a", "4", str(dest)])
    return dest


def caption_words(lines: list[Line]) -> list[dict]:
    """Words for the captions, spread inside each line's own span.

    Estimated within the line, never across the video: the error stays in the
    tenths of a second instead of accumulating.
    """
    out: list[dict] = []
    for line in lines:
        if not line.text.strip():
            continue
        span = max(line.seconds or line.slot, 0.3)
        for word in tts.estimate_words(line.text, span):
            out.append({"word": word["word"],
                        "start": round(line.start + word["start"], 3),
                        "end": round(line.start + word["end"], 3)})
    return out


def build_timeline(job_dir: Path, dubbed: Dubbed, job) -> Timeline:
    """The original picture, the new voice on top, the old one underneath."""
    video = [VideoClip(id=timeline_mod._new_id("v"),  # noqa: SLF001
                       source=dubbed.source.name, in_point=0.0,
                       out_point=round(dubbed.duration, 3), start=0.0,
                       kind="video", mute=True)]

    audio: list[AudioClip] = []
    if job.keep_audio:
        # The original, ducked: the music, the laugh and the room are what
        # makes this a dub of something real.
        audio.append(AudioClip(
            id=timeline_mod._new_id("a"),  # noqa: SLF001
            source=dubbed.source.name, in_point=0.0,
            out_point=round(dubbed.duration, 3), start=0.0,
            gain=ORIGINAL_GAIN, role="original"))

    for line in dubbed.lines:
        if line.audio is None:
            continue
        audio.append(AudioClip(
            id=timeline_mod._new_id("a"),  # noqa: SLF001
            source=str(Path(line.audio).relative_to(job_dir)),
            in_point=0.0, out_point=round(line.seconds, 3),
            start=round(line.start, 3), gain=1.0, role="narration"))

    cues = [CaptionCue(id=timeline_mod._new_id("c"),  # noqa: SLF001
                       text=line.text,
                       start=round(line.start, 3),
                       end=round(line.start + max(line.seconds, line.slot), 3))
            for line in dubbed.lines if line.text.strip()]

    return Timeline(
        duration=round(dubbed.duration, 3), video=video, audio=audio,
        captions=cues, media=[],
        caption_style=job.caption_style, caption_position=job.caption_position,
        watermark=job.watermark, watermark_position=job.watermark_position,
        watermark_size=job.watermark_size, watermark_opacity=job.watermark_opacity,
    ).normalize()


def run(job_id: str, job, job_dir: Path, log, stage) -> dict:
    """Transcribe, translate, speak, render. Same artifacts as every other
    job, so the editor, QA, the cover and publishing all work unchanged."""
    from .. import db
    from . import qa as qa_mod

    render.ensure_ffmpeg()

    stage("ingest")
    source, fetched_title = reels.fetch_source(job, job_dir, log)
    duration = render.probe_duration(source)
    if duration <= 0:
        raise RuntimeError("The video has no readable duration — the download "
                           "may be incomplete.")

    stage("legendas")
    lines, language, _words = transcribe(source, log)
    if not lines:
        raise RuntimeError(
            "Nothing was transcribed from this video, so there is nothing to "
            "dub. A clip with no speech is a job for the ordinary narration "
            "mode, which writes a script instead of translating one.")
    log(f"{len(lines)} spoken line(s) in {language or 'unknown'} — "
        f"dubbing into {job.language}")

    if language and job.language.lower().startswith(language.lower()[:2]):
        log(f"The original is already in {job.language}; the lines are being "
            f"rewritten rather than translated.", "warn")

    translate(lines, job.language, log)

    stage("voz")
    voice = db.get_voice(job.voice_id) if job.voice_id else None
    synthesize(lines, job_dir, voice, job.language, log)

    dubbed = Dubbed(source=source, duration=duration, language=language,
                    lines=lines, words=caption_words(lines),
                    title=((db.get_job(job_id) or {}).get("title") or "").strip()
                          or fetched_title,
                    url=job.source.strip())

    ass_path = captions_mod.build_ass(
        dubbed.words, job_dir / "captions.ass",
        style=job.caption_style, position=job.caption_position,
        title="", watermark=job.watermark,
        watermark_position=job.watermark_position,
        watermark_size=job.watermark_size,
        watermark_opacity=job.watermark_opacity)
    captions_mod.build_srt(dubbed.words, job_dir / "captions.srt")

    edl = build_timeline(job_dir, dubbed, job)
    timeline_mod.save(job_dir, edl)

    stage("render")
    final = job_dir / "short.mp4"
    timeline_render.render_timeline(job_dir, edl, final, log=lambda m: log(m))
    render.make_thumbnail(final, job_dir / "thumb.jpg",
                          at=min(1.0, edl.duration / 4))

    stage("qa")
    report = qa_mod.audit(final, ass_path, expected_duration=duration)
    log(f"QA: score {report.score}/100 — "
        f"{'PASSED' if report.passed else 'FAILED'}")

    shutil.copy(final, settings.outputs_dir / f"{job_id}.mp4")

    result = {
        "mode": MODE,
        "title": dubbed.title,
        # The source travels with the result: crediting whoever made the
        # original is the one thing that turns "recycled" into "republished
        # with attribution", and it has to be one copy away.
        "description": f"Original: {dubbed.url}" if dubbed.url else "",
        "hashtags": [],
        "duration": round(edl.duration, 2),
        "video": f"/api/jobs/{job_id}/file/short.mp4",
        "thumbnail": f"/api/jobs/{job_id}/file/thumb.jpg",
        "captions_srt": f"/api/jobs/{job_id}/file/captions.srt",
        "words": dubbed.words,
        "transcript_language": language,
        "dubbed_into": job.language,
        "source_url": dubbed.url,
        "source_kind": "video",
        "edit_mode": job.edit_mode,
        "lines": [{"start": line.start, "end": line.end,
                   "original": line.original, "text": line.text,
                   "tempo": round(line.tempo, 3)} for line in lines],
    }
    (job_dir / "dub.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    db.update_job(job_id, status="done", stage="qa", progress=1.0,
                  title=dubbed.title,
                  result_json=json.dumps(result, ensure_ascii=False),
                  qa_json=report.model_dump_json())
    notify.job_done(job_id, dubbed.title, edl.duration, report.score,
                    report.passed)
    return result
