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
from ..schemas import JobInput, ScriptEdit, ScriptSegment, ShortScript

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

# hook previews: hook_0.mp3, hook_1.mp3...
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
        raise HTTPException(400, "Upload at least one image before creating the job.")
    has_source = bool(job.source.strip()) or bool(job.attachments)
    if job.source_type != "imagem" and not has_source:
        raise HTTPException(400, "Provide a URL, topic, text or repository, or upload a file.")
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
        raise HTTPException(404, "Job not found")
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
    """Reprocess. With `?from=voz|legendas|fundo|render`, resumes from the
    given stage reusing the script/narration already on disk — without
    spending LLM or TTS again."""
    if db.get_job(job_id) is None:
        raise HTTPException(404, "Job not found")
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
    return {"job_id": job_id, "status": "queued", "from": from_stage or "start"}


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
def get_file(job_id: str, filename: str,
             download: bool = Query(False,
                                    description="Serve as a download instead "
                                                "of inline")):
    """Serve one of the job's files.

    Shown inline by default, because the same URLs feed the `<img>` and
    `<video>` on the job screen and an attachment is wrong for something being
    displayed. `download=1` switches it to an attachment.

    Either way the response carries a filename. Serving the bare URL with no
    name at all means a browser that decides to save it invents one, and the
    file lands in the downloads folder as an extensionless uuid — which is
    exactly what happened when this route only named the file for explicit
    downloads.
    """
    if filename in ALLOWED_FILES:
        media = ALLOWED_FILES[filename]
    elif _HOOK_FILE.match(filename):
        media = "audio/mpeg"
    else:
        raise HTTPException(404, "File not available")
    path: Path = settings.jobs_dir / job_id / filename
    if not path.exists():
        raise HTTPException(404, "File not generated yet")

    stem, _, ext = filename.rpartition(".")
    return FileResponse(
        path, media_type=media, filename=f"{stem}-{job_id}.{ext}",
        content_disposition_type="attachment" if download else "inline")


# ------------------------------------------------------------------- A/B hooks

def _current_script(job_id: str) -> tuple[dict, JobInput, ShortScript, Path]:
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job not found")
    job_dir = settings.job_dir(job_id)
    override = job_dir / "script_override.json"
    source = override if override.exists() else job_dir / "script.json"
    if not source.exists():
        raise HTTPException(400, "This job has no script yet.")
    job = JobInput(**json.loads(row["input_json"]))
    script = ShortScript(**json.loads(source.read_text(encoding="utf-8")))
    return row, job, script, job_dir


class HooksRequest(BaseModel):
    count: int = 3
    preview_audio: bool = True


@router.post("/{job_id}/hooks")
def build_hooks(job_id: str, request: HooksRequest | None = None):
    """Generates alternative hooks for the current script, with an audio
    preview of each in the job's voice — just the hook, a few seconds of TTS
    per variant."""
    from ..pipeline import script as script_mod, tts as tts_mod

    request = request or HooksRequest()
    row, job, script, job_dir = _current_script(job_id)
    llm.current_job.set(job_id)

    try:
        hooks = script_mod.build_hook_variants(script, job, max(2, min(request.count, 5)))
    except Exception as exc:
        raise HTTPException(502, f"Failed to generate hooks: {exc}")

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
        except Exception as exc:  # noqa: BLE001 — the preview is optional
            hook["audio_error"] = str(exc)[:160]

    current = next((s.text for s in script.segments if s.kind == "hook"), "")
    result = json.loads(row["result_json"] or "{}")
    result["hook_variants"] = {"current": current, "options": hooks}
    db.update_job(job_id, result_json=json.dumps(result))
    db.log_event(job_id, f"{len(hooks)} alternative hook(s) generated")
    return result["hook_variants"]


class HookChoice(BaseModel):
    text: str
    render: bool = True


@router.post("/{job_id}/hooks/apply")
def apply_hook(job_id: str, choice: HookChoice):
    """Swaps the script's hook for the chosen one and re-renders this job."""
    from ..pipeline import script as script_mod

    if not choice.text.strip():
        raise HTTPException(400, "Empty hook.")
    row, job, script, job_dir = _current_script(job_id)
    updated = script_mod.with_hook(script, choice.text)
    (job_dir / "script_override.json").write_text(updated.model_dump_json(indent=2),
                                                  encoding="utf-8")
    result = json.loads(row["result_json"] or "{}")
    result["script"] = updated.model_dump()
    db.update_job(job_id, result_json=json.dumps(result), error=None,
                  **({"qa_json": None} if choice.render else {}))
    db.log_event(job_id, f"Hook swapped: {choice.text[:100]}")
    if choice.render:
        orchestrator.request_resume(job_id, "voz")   # script ready: skip the LLM
        worker.enqueue(job_id)
    return {"job_id": job_id, "script": updated.model_dump(), "rendering": choice.render}


@router.post("/{job_id}/hooks/fork")
def fork_with_hook(job_id: str, choice: HookChoice):
    """Creates a NEW identical job with a different hook — the A/B pair.
    Publish both and compare them under Performance."""
    from ..pipeline import script as script_mod

    if not choice.text.strip():
        raise HTTPException(400, "Empty hook.")
    row, job, script, job_dir = _current_script(job_id)
    variant = script_mod.with_hook(script, choice.text)

    new_id = db.create_job(job.model_dump(), f"{variant.title} (B)")
    new_dir = settings.job_dir(new_id)
    (new_dir / "script_override.json").write_text(variant.model_dump_json(indent=2),
                                                  encoding="utf-8")
    # reuse the video already downloaded (source.<ext> + source.info.json)
    # instead of pulling it from YouTube all over again
    for src in job_dir.glob("source.*"):
        shutil.copy(src, new_dir / src.name)
    db.log_event(new_id, f"A/B variant of {job_id} with hook: {choice.text[:100]}")
    db.log_event(job_id, f"A/B variant created: {new_id}")
    worker.enqueue(new_id)
    return {"job_id": new_id, "parent": job_id}


# --------------------------------------------------------------------- cover

class CoverRequest(BaseModel):
    title: str = ""
    at: float | None = None      # frame's second; None = pick automatically


@router.post("/{job_id}/cover")
def rebuild_cover(job_id: str, request: CoverRequest | None = None):
    from ..pipeline import cover as cover_mod, render as render_mod

    request = request or CoverRequest()
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job not found")
    job_dir = settings.job_dir(job_id)
    video = job_dir / "short.mp4"
    if not video.exists():
        raise HTTPException(400, "Video not rendered yet")

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
        raise HTTPException(500, f"Failed to generate the cover: {exc}")

    (job_dir / "cover.json").write_text(json.dumps({"at": at}), encoding="utf-8")
    result["cover"] = f"/api/jobs/{job_id}/file/cover.jpg"
    result["cover_at"] = at
    db.update_job(job_id, result_json=json.dumps(result))
    db.log_event(job_id, f"Cover rebuilt (frame at {at:.1f}s)")
    return {"cover": result["cover"], "at": at}


# Which pipeline stage each editable field actually invalidates. Changing the
# watermark has no business re-running the scriptwriter: it used to spend an
# LLM call and, because the model is not deterministic, could hand back a
# different script than the one the user had approved.
#
# `legendas` is the cheapest safe resume point, even for music-only changes:
# resuming straight at `render` would leave the subtitle file unloaded and
# burn a video with no captions at all.
FIELD_STAGE = {
    "voice_id": "voz",
    "background": "fundo",
    "background_query": "fundo",
    "scroll": "fundo",
    "caption_style": "legendas",
    "caption_position": "legendas",
    "caption_offset": "legendas",
    "watermark": "legendas",
    "watermark_position": "legendas",
    "watermark_size": "legendas",
    "watermark_opacity": "legendas",
    "music": "legendas",
    "music_track": "legendas",
    "music_volume": "legendas",
}

# earliest (most expensive) first: a change set redoes from the earliest stage
# any of its fields touches
STAGE_ORDER = ["voz", "fundo", "legendas"]


def _earliest_stage(fields) -> str | None:
    stages = {FIELD_STAGE[f] for f in fields if f in FIELD_STAGE}
    if not stages:
        return None
    return min(stages, key=STAGE_ORDER.index)


@router.post("/{job_id}/edit")
def edit_job(job_id: str, edit: ScriptEdit):
    """Applies manual edits and re-renders.

    The edited script is written as an override, so the re-render does not
    call the LLM again — the text you wrote is exactly what gets narrated.

    Only the stages the change actually touches are redone: swapping the
    watermark rebuilds captions and video, while a new voice also re-runs the
    TTS. Neither spends the scriptwriter.
    """
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job not found")

    job = JobInput(**json.loads(row["input_json"]))
    changes = edit.model_dump(exclude_none=True)

    if edit.segments is not None:
        if not edit.segments:
            raise HTTPException(400, "The script needs at least one segment.")
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
                  "watermark_position", "watermark_size", "watermark_opacity",
                  "background", "background_query", "scroll"):
        if field in changes:
            setattr(job, field, changes[field])

    db.update_job(job_id, input_json=json.dumps(job.model_dump()),
                  error=None, qa_json=None)
    # what was a draft has just become the official version
    (settings.job_dir(job_id) / "draft.json").unlink(missing_ok=True)

    # An edited script is already on disk as an override, so the narration is
    # the only thing that has to be redone; anything else resumes from the
    # earliest stage its fields touch. Falls back to the full pipeline when
    # the artifacts needed to resume are not there.
    job_dir = settings.job_dir(job_id)
    wanted = "voz" if edit.segments is not None else _earliest_stage(changes)
    resumed_from = None
    if wanted:
        available = orchestrator.resumable_stage(job_dir)
        if available in STAGE_ORDER:
            # never resume later than what is actually on disk
            resumed_from = min(wanted, available, key=STAGE_ORDER.index)
            orchestrator.request_resume(job_id, resumed_from)

    worker.enqueue(job_id)
    return {"job_id": job_id, "status": "queued", "applied": sorted(changes),
            "resumed_from": resumed_from}


@router.delete("/{job_id}/edit")
def reset_edit(job_id: str):
    """Discards the edited script and goes back to generating it with the LLM."""
    job_dir = settings.job_dir(job_id)
    (job_dir / "script_override.json").unlink(missing_ok=True)
    (job_dir / "draft.json").unlink(missing_ok=True)
    return {"job_id": job_id, "reset": True}


# --------------------------------------------------------------------- draft
# The editor keeps whatever is being typed here, without rendering anything.
# That is what lets you close the tab (or switch machines) and come back where
# you left off — before, the text only lived in React state and disappeared
# along with the screen.

@router.get("/{job_id}/draft")
def get_draft(job_id: str):
    if db.get_job(job_id) is None:
        raise HTTPException(404, "Job not found")
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
        raise HTTPException(404, "Job not found")
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
    """Rewrites the current script from a natural-language instruction.

    E.g. "make the hook more aggressive", "cut it in half", "drop the
    technical jargon". The result becomes the job's manual script, so the next
    render uses exactly that text.
    """
    from ..pipeline import script as script_mod

    if not request.instruction.strip():
        raise HTTPException(400, "Write what you want to change in the script.")

    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job not found")

    job_dir = settings.job_dir(job_id)
    override = job_dir / "script_override.json"
    base = job_dir / "script.json"
    source = override if override.exists() else base
    if not source.exists():
        raise HTTPException(400, "This job has no script yet.")

    job = JobInput(**json.loads(row["input_json"]))
    current = ShortScript(**json.loads(source.read_text(encoding="utf-8")))
    llm.current_job.set(job_id)

    try:
        updated = script_mod.refine_script(current, request.instruction, job)
    except Exception as exc:
        raise HTTPException(502, f"Failed to refine the script: {exc}")

    override.write_text(updated.model_dump_json(indent=2), encoding="utf-8")

    # The editor and the UI read the script from result_json. Without updating
    # it here, the refined text only lived on disk: any page reload went back
    # to showing the old script — and saving over it would discard the
    # refinement.
    result = json.loads(row["result_json"] or "{}")
    result["script"] = updated.model_dump()
    result["title"] = updated.title
    result["description"] = updated.description
    result["hashtags"] = updated.hashtags

    db.update_job(job_id, title=updated.title,
                  result_json=json.dumps(result),
                  error=None, **({"qa_json": None} if request.render else {}))
    db.log_event(job_id, f"Script refined by prompt: {request.instruction[:120]}")

    if request.render:
        worker.enqueue(job_id)

    return {"job_id": job_id, "script": updated.model_dump(),
            "rendering": request.render}


class CaptionRequest(BaseModel):
    instruction: str = ""


def _script_from_transcript(row: dict, job: JobInput) -> ShortScript | None:
    """Stand in for the script using what the person actually said.

    The post-text builder reads a `ShortScript`; a recording of your own has
    timed words instead. Wrapping those as one segment gives the builder the
    same thing it always gets — the words that are spoken in the video.
    """
    result = json.loads(row["result_json"] or "{}")
    spoken = " ".join(w.get("word", "") for w in result.get("words") or []).strip()
    if not spoken:
        return None
    return ShortScript(
        title=row.get("title") or "",
        description="",
        hashtags=[],
        segments=[ScriptSegment(kind="corpo", text=spoken)],
        estimated_seconds=int(result.get("duration") or job.duration),
    )


@router.post("/{job_id}/caption")
def build_caption(job_id: str, request: CaptionRequest | None = None):
    """Generates the post text (title, description, hashtags) from the script."""
    from ..pipeline import script as script_mod

    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job not found")
    if not row["result_json"]:
        raise HTTPException(400, "The short has not been generated yet.")

    job = JobInput(**json.loads(row["input_json"]))
    job_dir = settings.job_dir(job_id)
    override = job_dir / "script_override.json"
    source = override if override.exists() else job_dir / "script.json"

    if source.exists():
        script = ShortScript(**json.loads(source.read_text(encoding="utf-8")))
    else:
        # A recording of your own never had a script written for it, but it
        # does have the transcript of what was said — which is the same thing
        # for this purpose. Refusing here would mean the one kind of video the
        # user has to publish by hand is the one with no caption to paste.
        script = _script_from_transcript(row, job)
        if script is None:
            raise HTTPException(
                400, "This job has neither a script nor a transcript to write "
                     "the post text from.")

    llm.current_job.set(job_id)

    try:
        caption = script_mod.build_post_caption(
            script, job, (request.instruction if request else ""))
    except Exception as exc:
        raise HTTPException(502, f"Failed to generate the post caption: {exc}")

    result = json.loads(row["result_json"])
    result["caption"] = caption
    db.update_job(job_id, result_json=json.dumps(result))
    db.log_event(job_id, "Post caption generated")
    return caption


@router.get("/{job_id}/timeline")
def get_timeline(job_id: str):
    """The short's editable timeline (clips, audio and captions)."""
    from ..pipeline import timeline as timeline_mod

    job_dir = settings.job_dir(job_id)
    edl = timeline_mod.load(job_dir)
    if edl is None:
        # Jobs rendered before the editor existed have no timeline.json. We
        # rebuild it from the files already on disk so that older productions
        # don't turn into a dead end.
        edl = _rebuild_timeline(job_id, job_dir)
        if edl is None:
            raise HTTPException(404, "This job has no timeline yet.")
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
    """Saves the timeline without rendering — used by the editor's autosave."""
    from ..pipeline import timeline as timeline_mod

    job_dir = settings.job_dir(job_id)
    edl = timeline_mod.Timeline.from_dict(data).normalize()
    timeline_mod.save(job_dir, edl)
    return edl.to_dict()


@router.post("/{job_id}/timeline/render")
def render_timeline(job_id: str, data: dict | None = None):
    """Recompiles the video from the timeline and re-audits it in QA.

    It goes through neither the LLM nor TTS: it uses exactly the files that
    already exist, trimmed and positioned as set in the editor.
    """
    from ..pipeline import qa as qa_mod, render as render_mod
    from ..pipeline import timeline as timeline_mod, timeline_render

    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, "Job not found")

    job_dir = settings.job_dir(job_id)
    edl = (timeline_mod.Timeline.from_dict(data) if data
           else timeline_mod.load(job_dir))
    if edl is None:
        raise HTTPException(400, "No timeline to render.")
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
        raise HTTPException(500, f"Failed to render the timeline: {exc}")

    for message in events:
        db.log_event(job_id, message)
    db.log_event(job_id, "Video reassembled by the timeline editor")

    report = qa_mod.audit(final, None)
    db.log_event(job_id,
                 f"QA: score {report.score}/100 — "
                 f"{'PASSED' if report.passed else 'FAILED'}")

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
        raise HTTPException(400, "Video not rendered yet")
    report = qa_mod.audit(video, job_dir / "captions.ass")
    db.update_job(job_id, qa_json=report.model_dump_json())
    return report
