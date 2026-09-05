"""Orchestrates the full pipeline: ingest -> roteiro -> voz -> legenda -> fundo
-> render -> QA. When QA fails a video, an autofix loop tries to correct the
problem (duration, caption position, loudness, background) and redoes only the
affected stages — not the whole pipeline — until it passes or runs out of tries.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import db
from ..config import settings
from ..schemas import JobInput, ShortScript
from . import (avatar, broll, captions, cover as cover_mod, highlights, ingest, llm,
               notify, overlays as overlay_mod, qa, reels, render,
               script as script_mod, timeline as timeline_mod, tts)

STAGES = [
    ("ingest", 0.08),
    ("roteiro", 0.22),
    ("voz", 0.40),
    ("legendas", 0.50),
    ("fundo", 0.68),
    ("render", 0.86),
    ("qa", 1.00),
]

# stages affected when a stage is redone — the invalidation cascade
CASCADE = {
    "script": {"script", "voz", "legendas", "fundo", "render"},
    "voz": {"voz", "legendas", "fundo", "render"},
    # the narration has already been edited on disk (e.g. silence trimming):
    # everything that depends on the timings is redone, but without
    # re-synthesizing the audio.
    "narracao_editada": {"legendas", "fundo", "render"},
    "legendas": {"legendas", "render"},
    "fundo": {"fundo", "render"},
    "render": {"render"},
}

# stage name in the UI -> root of the cascade to redo when resuming from there.
# The order matters: RESUMABLE on the frontend slices this list.
RESUME_STAGES = {
    "roteiro": "script",
    "voz": "voz",
    "fundo": "fundo",
    "legendas": "legendas",
    "render": "render",
}


def request_resume(job_id: str, stage: str) -> None:
    """Marks where the next run should resume from. Consumed (and deleted) by
    run_job; if the required artifacts do not exist, the pipeline runs in full
    as it always does."""
    if stage not in RESUME_STAGES:
        raise ValueError(f"Invalid stage to resume from: {stage}")
    (settings.job_dir(job_id) / "resume.json").write_text(
        json.dumps({"from": stage}), encoding="utf-8")


def resumable_stage(job_dir: Path) -> str | None:
    """The furthest stage we can resume from with what is on disk."""
    has_script = (job_dir / "script.json").exists() or (job_dir / "script_override.json").exists()
    has_voice = (job_dir / "narration.json").exists() and (job_dir / "narration.mp3").exists()
    if not has_script:
        return None
    if not has_voice:
        return "voz"
    # without the background on disk, resuming from captions would leave the
    # render with no image
    return "legendas" if saved_background(job_dir) is not None else "fundo"


def _load_resume(job_id: str, job_dir: Path, log) -> tuple[set[str], ShortScript | None,
                                                          tts.Narration | None, Path | None]:
    """Reads resume.json and returns (dirty stages, script, narration,
    background) already loaded from disk. With no resume request, or without
    enough artifacts for the requested stage, it returns everything dirty and
    the pipeline runs in full."""
    everything = (set(CASCADE["script"]), None, None, None)
    marker = job_dir / "resume.json"
    if not marker.exists():
        return everything
    try:
        stage = json.loads(marker.read_text(encoding="utf-8")).get("from", "")
    except json.JSONDecodeError:
        stage = ""
    marker.unlink(missing_ok=True)
    root = RESUME_STAGES.get(stage)
    if root is None or root == "script":
        return everything

    override = job_dir / "script_override.json"
    script_file = override if override.exists() else job_dir / "script.json"
    if not script_file.exists():
        log("Resume requested, but there is no saved script — running from the start",
            "warn")
        return everything
    short = ShortScript(**json.loads(script_file.read_text(encoding="utf-8")))

    narration = None
    if root != "voz":
        meta = job_dir / "narration.json"
        audio = job_dir / "narration.mp3"
        if not (meta.exists() and audio.exists()):
            log("Resume requested with no saved narration — redoing from the voice "
                "stage", "warn")
            return set(CASCADE["voz"]), short, None, None
        data = json.loads(meta.read_text(encoding="utf-8"))
        narration = tts.Narration(audio, float(data["duration"]), data["words"])

    # Resuming from "legendas" or "render" does not rebuild the background, but
    # the render needs it: without recovering the file, the composition was
    # getting None.
    background = None
    if root in ("legendas", "render"):
        background = saved_background(job_dir)
        if background is None:
            log("Resume requested with no saved background — redoing from the "
                "background stage", "warn")
            return set(CASCADE["fundo"]), short, narration, None

    log(f"Resuming from: {stage}")
    return set(CASCADE[root]), short, narration, background


def saved_background(job_dir: Path) -> Path | None:
    """Background from the previous render. The name varies (scroll, padding),
    so the effective path is noted in background.json when the stage runs."""
    meta = job_dir / "background.json"
    if meta.exists():
        try:
            candidate = Path(json.loads(meta.read_text(encoding="utf-8"))["path"])
        except (json.JSONDecodeError, KeyError, TypeError):
            candidate = None
        if candidate is not None and candidate.exists():
            return candidate
    # jobs rendered before this record existed: look for the known names
    for name in ("background_scroll_padded.mp4", "background_scroll.mp4",
                 "background_padded.mp4", "background.mp4"):
        if (job_dir / name).exists():
            return job_dir / name
    return None


def run_job(job_id: str) -> dict:
    row = db.get_job(job_id)
    if row is None:
        raise RuntimeError(f"Job {job_id} does not exist")

    job = JobInput(**json.loads(row["input_json"]))
    job_dir = settings.job_dir(job_id)
    llm.current_job.set(job_id)   # every LLM call gets attributed to this job

    def log(message: str, level: str = "info") -> None:
        db.log_event(job_id, message, level)

    def stage(name: str) -> None:
        progress = dict(STAGES).get(name, 0.0)
        db.update_job(job_id, stage=name, progress=progress, status="running")
        log(f"Stage: {name}")

    try:
        # A recording of your own has neither a script to write nor a voice to
        # synthesize: the audio and the words are already in the file. It is a
        # different pipeline, not a variation of this one — but it writes the
        # same artifacts, so the editor, QA, the cover and publishing all work
        # on its output unchanged.
        if job.edit_mode == reels.MODE:
            return reels.run(job_id, job, job_dir, log, stage)

        # An avatar video is the same idea from the other side: the provider
        # renders both the picture and the voice, so there is no script stage
        # and no TTS stage here either — but it writes the same artifacts.
        if job.edit_mode == avatar.MODE:
            return avatar.run(job_id, job, job_dir, log, stage)

        render.ensure_ffmpeg()

        # 1. Ingestion — happens only once, even across QA retries
        stage("ingest")
        material = ingest.ingest(job, job_dir, log)
        log(f"Source: {material.kind} — "
            f"{len(material.context())} characters of context")

        if (material.kind == "github" and job.background == "auto"
                and job.scroll == "nenhum"):
            job.scroll = "codigo"
            log("Repository with no background preference — enabling code scroll")

        max_attempts = max(1, job.qa_max_attempts) if job.qa_autofix else 1

        ass_path = None
        overlays_list: list = []
        final = job_dir / "short.mp4"
        duration = 0.0
        report: qa.QAReport | None = None
        attempts: list[dict] = []
        # first pass: everything has to run — unless resuming with artifacts
        dirty, short, narration, background = _load_resume(job_id, job_dir, log)

        attempt = 1
        while True:
            if attempt > 1:
                log(f"Autofix attempt {attempt}/{max_attempts}")

            if "script" in dirty:
                stage("roteiro")
                override = job_dir / "script_override.json"
                if override.exists():
                    # script hand-edited in the editor: honor the user's text
                    # instead of generating another one through the LLM
                    short = ShortScript(**json.loads(override.read_text(encoding="utf-8")))
                    log(f"Manually edited script: {len(short.segments)} segments")
                else:
                    short = script_mod.build_script(job, material)
                    log(f"Script: '{short.title}' with {len(short.segments)} segments")
                (job_dir / "script.json").write_text(short.model_dump_json(indent=2),
                                                     encoding="utf-8")

            if "voz" in dirty:
                stage("voz")
                voice = db.get_voice(job.voice_id) if job.voice_id else None
                narration_text = script_mod.full_narration(short)
                narration = tts.synthesize(narration_text, job_dir / "narration.mp3",
                                          voice, log)
                log(f"Narration: {narration.duration:.1f}s, "
                    f"{len(narration.words)} timed words")
                # timings on disk: this is what allows resuming from "legendas"
                # without synthesizing again after a crash
                (job_dir / "narration.json").write_text(json.dumps(
                    {"duration": narration.duration, "words": narration.words}),
                    encoding="utf-8")

            duration = min(narration.duration + 0.35, float(settings.max_short_seconds))

            if "legendas" in dirty:
                stage("legendas")
                caption_words = _shift_words(narration.words, job.caption_offset)
                if job.caption_offset:
                    log(f"Manual sync adjustment: {job.caption_offset:+.2f}s")
                ass_path = captions.build_ass(
                    caption_words, job_dir / "captions.ass",
                    style=job.caption_style, position=job.caption_position,
                    title=short.title if job.title_overlay else "",
                    watermark=job.watermark,
                    watermark_position=job.watermark_position,
                    watermark_size=job.watermark_size,
                    watermark_opacity=job.watermark_opacity,
                )
                captions.build_srt(caption_words, job_dir / "captions.srt")

                overlays_list = []
                if not render.has_filter("ass"):
                    overlay_dir = job_dir / "overlays"
                    overlays_list = overlay_mod.render_captions(
                        caption_words, overlay_dir,
                        style=job.caption_style, position=job.caption_position)
                    title_ov = (overlay_mod.render_title(short.title, overlay_dir)
                                if job.title_overlay else None)
                    mark_ov = overlay_mod.render_watermark(
                        job.watermark, overlay_dir, duration,
                        position=job.watermark_position, size=job.watermark_size,
                        opacity=job.watermark_opacity)
                    overlays_list = [o for o in (title_ov, mark_ov) if o] + overlays_list
                    (job_dir / "overlays.json").write_text(json.dumps([
                        {"file": o.path.name, "start": o.start, "end": o.end,
                         "x": o.x, "y": o.y, "kind": o.kind} for o in overlays_list],
                        indent=2), encoding="utf-8")
                    log(f"{len(overlays_list)} overlays generated")

            if "fundo" in dirty:
                stage("fundo")
                background = _build_background(job, short, narration, material,
                                               job_dir, duration, log)
                # the final name varies (scroll, padding): noted for the resume
                (job_dir / "background.json").write_text(
                    json.dumps({"path": str(background)}), encoding="utf-8")

            if "render" in dirty:
                stage("render")
                if background is None:
                    raise RuntimeError(
                        "No background available for the composition. Reprocess "
                        "the job from the start or from the 'fundo' stage.")
                render.compose(job_dir, background, job_dir / "narration.mp3",
                               final, job, duration, overlays=overlays_list,
                               subtitles=ass_path)
                render.make_thumbnail(final, job_dir / "thumb.jpg",
                                      at=min(1.0, duration / 4))
                log(f"Video ready: {final.name}")

            stage("qa")
            report = qa.audit(final, ass_path, expected_duration=narration.duration)
            log(f"QA: score {report.score}/100 — {'PASSED' if report.passed else 'FAILED'}",
                "info" if report.passed else "warn")
            for issue in report.issues:
                log(f"[{issue.severity}] {issue.check}: {issue.message}", issue.severity)

            if report.passed or attempt >= max_attempts:
                break

            codes = {i.check for i in report.issues if i.severity in ("fatal", "erro")}
            fix = None
            if "silencio_inicial" in codes:
                trimmed = tts.trim_leading_silence(narration, job_dir / "narration_trim.mp3")
                if trimmed is not narration:
                    narration = trimmed
                    shutil.copy(narration.audio_path, job_dir / "narration.mp3")
                    narration.audio_path = job_dir / "narration.mp3"
                    fix = ("trimming silence at the start of the narration", job,
                           "narracao_editada")
            if fix is None:
                fix = qa.suggest_fix(report, job)

            if fix is None:
                log("No automatic fix applicable — keeping the current result", "warn")
                break

            action, job, root_stage = fix
            # redoing a stage invalidates every stage that depends on it
            dirty = CASCADE[root_stage]
            attempts.append({"attempt": attempt, "action": action,
                             "report": report.model_dump()})
            log(f"Automatic fix: {action}")
            attempt += 1

        published_copy = settings.outputs_dir / f"{job_id}.mp4"
        shutil.copy(final, published_copy)

        # The stock footage did its job in the render above. Keeping it would
        # grow the disk by tens of MB per short, without bound.
        broll.cleanup(job_dir, log)

        # Cover and animated preview are cosmetic: a failure here does not fail
        # the job.
        cover_at = None
        try:
            _, cover_at = cover_mod.build(final, short.title, job.niche,
                                          job_dir / "cover.jpg", duration, job_dir)
            (job_dir / "cover.json").write_text(json.dumps({"at": cover_at}),
                                                encoding="utf-8")
            log(f"Cover generated from the frame at {cover_at:.1f}s")
        except Exception as exc:  # noqa: BLE001
            log(f"Cover not generated: {exc}", "warn")
        try:
            render.make_preview_gif(final, job_dir / "preview.gif")
        except Exception as exc:  # noqa: BLE001
            log(f"Animated preview not generated: {exc}", "warn")

        # Describes the result as an editable timeline, so the video editor can
        # cut/move/rewrite without redoing the pipeline.
        try:
            parts = sorted(job_dir.glob("hl_*.mp4")) or sorted(job_dir.glob("kb_*.mp4")) \
                or sorted(job_dir.glob("bgpart_*.mp4")) or [background]
            music_file = next(iter(job_dir.glob("music.*")), None)
            edl = timeline_mod.build_from_job(
                job_dir, narration.words, narration.duration, parts,
                job.caption_style, job.caption_position, job.watermark,
                music=music_file, music_gain=job.music_volume,
            )
            timeline_mod.save(job_dir, edl)
            log(f"Timeline: {len(edl.video)} clip(s), {len(edl.captions)} caption(s)")
        except Exception as exc:  # noqa: BLE001 — editor is optional, never fatal
            log(f"Could not assemble the timeline: {exc}", "warn")

        result = {
            "title": short.title,
            "description": short.description,
            "hashtags": short.hashtags,
            "duration": round(duration, 2),
            "video": f"/api/jobs/{job_id}/file/short.mp4",
            "thumbnail": f"/api/jobs/{job_id}/file/thumb.jpg",
            "captions_srt": f"/api/jobs/{job_id}/file/captions.srt",
            "script": short.model_dump(),
            "words": narration.words,
            "source_kind": material.kind,
            "edit_mode": job.edit_mode,
            "qa_attempts": attempts,
            "cover": (f"/api/jobs/{job_id}/file/cover.jpg"
                      if (job_dir / "cover.jpg").exists() else None),
            "cover_at": cover_at,
            "preview_gif": (f"/api/jobs/{job_id}/file/preview.gif"
                            if (job_dir / "preview.gif").exists() else None),
        }
        # keeps what was produced after the previous render (hooks, post copy)
        previous = json.loads(row["result_json"] or "{}")
        for key in ("hook_variants", "caption"):
            if key in previous and key not in result:
                result[key] = previous[key]

        db.update_job(job_id, status="done", stage="qa", progress=1.0,
                      title=short.title,
                      result_json=json.dumps(result),
                      qa_json=report.model_dump_json())
        notify.job_done(job_id, short.title, duration, report.score, report.passed)
        return result

    except Exception as exc:  # noqa: BLE001 — the error has to reach the UI
        db.log_event(job_id, f"Failure: {exc}", "error")
        db.update_job(job_id, status="error", error=str(exc))
        notify.job_failed(job_id, row.get("title") or "", str(exc))
        raise


def _shift_words(words: list[dict], offset: float) -> list[dict]:
    """Shifts every caption timing — the editor's manual fine-tuning, for when
    the provider's voice has a noticeable attack delay."""
    if not offset:
        return words
    return [{"word": w["word"],
             "start": round(max(w["start"] + offset, 0.0), 3),
             "end": round(max(w["end"] + offset, 0.0), 3)} for w in words]


def _build_background(job: JobInput, short, narration, material, job_dir: Path,
                      duration: float, log) -> Path:
    out = job_dir / "background.mp4"
    mode = job.background

    if mode == "auto":
        if material.kind == "imagem":
            mode = "imagem_kenburns"
        elif material.kind == "video" and material.video_path:
            mode = "video_fonte"
        elif broll.providers_ready():
            mode = "broll"
        else:
            mode = "gradiente"
            # The gradient is what is left when there is nothing to show, and
            # it looks identical whether it was chosen or merely settled for.
            # Saying so is the difference between a deliberate look and a user
            # wondering why the video they pasted never appeared.
            log("No footage for the background: the source produced no video "
                "and no stock bank is configured. Falling back to a gradient — "
                "paste a video link, upload a file, or register a Pexels/"
                "Pixabay/Coverr key to get real footage.", "warn")

    # "pan" is consumed inside the background functions (animated crop); "texto"
    # and "codigo" are panels overlaid later, so they survive past this point.
    applied_scroll = "nenhum" if job.scroll == "pan" else job.scroll

    if mode == "imagem_kenburns":
        if not material.image_paths:
            raise RuntimeError("Image mode selected with no image uploaded.")
        durations = script_mod.segment_durations_covering(
            short, narration.words, duration) or [duration]
        images = material.image_paths
        cycled = [images[i % len(images)] for i in range(len(durations))]
        log(f"Background: {len(images)} image(s) with the Ken Burns effect")
        render.background_from_images_kenburns(cycled, durations, out, job_dir)
        applied_scroll = "nenhum"  # scrolling text over Ken Burns makes no sense

    elif mode == "video_fonte":
        # Asked for the source video and there is none. The image branch above
        # already refuses in this situation; this one used to fall through to
        # the gradient at the bottom, so a short came out looking finished with
        # none of the footage the user pasted a link for, and nothing said why.
        if not material.video_path:
            raise RuntimeError(
                "The background was set to the source video, but no video was "
                "downloaded from this job's input. Paste a video link (YouTube, "
                "Twitch, Vimeo…) or upload a file, or pick another background.")
        if job.edit_mode == "resumo":
            seg_durations = script_mod.segment_durations_covering(
                short, narration.words, duration)
            sources = material.video_paths or [material.video_path]
            windows = highlights.windows_across_sources(
                sources, len(seg_durations), seg_durations, log)
            log(f"Background: {len(windows)} highlight(s) from {len(sources)} video(s)")
            render.background_from_multi_highlights(windows, out, job_dir,
                                                    durations=seg_durations)
        elif len(material.video_paths) > 1:
            log(f"Background: {len(material.video_paths)} videos in sequence, 9:16")
            render.background_from_clips(material.video_paths, duration, out, job_dir)
        else:
            log(f"Background: source video framed to 9:16 ({job.background_fill})")
            render.background_from_video(material.video_path, duration, out,
                                         scroll=job.scroll,
                                         fill=job.background_fill,
                                         log=lambda m: log(m))

    elif mode == "broll":
        queries = [job.background_query] if job.background_query else \
            [s.broll_query for s in short.segments if s.broll_query]

        # A single clip stretched over a whole short reads as a static image.
        # Aim for roughly one scene every 6 seconds, pulling extra clips per
        # query when the script has fewer segments than the length calls for.
        wanted = max(2, min(int(duration // 6) + 1, 8))
        per_query = max(1, -(-wanted // max(len(queries[:5]), 1)))
        log(f"Background: B-roll from {', '.join(broll.providers_ready())} "
            f"for {queries[:4]} (~{wanted} scenes)")

        clips = broll.fetch_for_queries(queries[:5], log, job_dir=job_dir,
                                        per_query=per_query)
        if clips:
            log(f"{len(clips)} stock clip(s) downloaded")
            render.background_from_clips(clips, duration, out, job_dir)
        else:
            log("No B-roll found; falling back to a gradient", "warn")
            render.background_gradient(duration, job.niche, out, scroll=job.scroll)

    elif mode == "codigo_scroll":
        log("Background: gradient with code scroll")
        render.background_gradient(duration, job.niche, out, scroll="nenhum")
        applied_scroll = "codigo"

    elif mode == "ia_video":
        from . import videogen
        prompt = job.background_query or short.segments[0].broll_query or short.title
        videogen.generate_clip(prompt, duration, out, log=log)

    else:
        log("Background: generated gradient")
        render.background_gradient(duration, job.niche, out, scroll=job.scroll)

    if applied_scroll in ("texto", "codigo"):
        # For a repository, scroll the file contents (actual code) instead of
        # context(), which starts with the directory tree.
        if applied_scroll == "codigo" and material.text:
            source_text = material.text[:4000]
        else:
            source_text = material.context(limit=4000) or script_mod.full_narration(short)
        log(f"Applying {'code' if applied_scroll == 'codigo' else 'text'} scroll")
        panel = overlay_mod.render_scroll_panel(
            source_text, job_dir, mono=(applied_scroll == "codigo"))
        scrolled = job_dir / "background_scroll.mp4"
        render.add_scroll_panel(out, panel, duration, scrolled)
        return render.ensure_min_duration(scrolled, duration, job_dir)

    return render.ensure_min_duration(out, duration, job_dir)
