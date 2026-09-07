"""Threaded worker: job queue + publication scheduler + metrics collection."""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from datetime import datetime, timezone

from . import db
from .config import settings
from .pipeline import orchestrator

_queue: "queue.Queue[str]" = queue.Queue()
_clip_queue: "queue.Queue[str]" = queue.Queue()
_film_queue: "queue.Queue[str]" = queue.Queue()
_longform_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
_started = False
_lock = threading.Lock()


def enqueue(job_id: str) -> None:
    db.update_job(job_id, status="queued", stage="queued", progress=0.0)
    _queue.put(job_id)


def enqueue_clip_plan(plan_id: str) -> None:
    db.update_clip_plan(plan_id, status="queued")
    _clip_queue.put(plan_id)


def enqueue_film(film_id: str) -> None:
    db.update_film(film_id, status="queued", error=None)
    _film_queue.put(film_id)


def enqueue_longform(project_id: str, action: str = "assemble") -> None:
    """Two kinds of work share this queue: ingesting the material (downloads
    and transcription) and assembling the production. The status says which
    is pending, so a restart knows what to requeue."""
    status = "ingesting" if action == "ingest" else "queued"
    db.update_longform(project_id, status=status, error=None)
    _longform_queue.put((action, project_id))


def _worker_loop() -> None:
    while True:
        job_id = _queue.get()
        try:
            orchestrator.run_job(job_id)
        except Exception:
            db.log_event(job_id, traceback.format_exc()[-2000:], "error")
        finally:
            _queue.task_done()


def _clip_loop() -> None:
    """Separate queue: analyzing a long video takes minutes and must not
    block the rendering of shorts already in the queue."""
    while True:
        plan_id = _clip_queue.get()
        try:
            _analyze_plan(plan_id)
        except Exception as exc:  # noqa: BLE001
            db.update_clip_plan(plan_id, status="error", error=str(exc))
        finally:
            _clip_queue.task_done()


def _film_loop() -> None:
    """Third queue: generating a film is many minutes of paid AI video, and it
    must not sit in front of the shorts already waiting to render."""
    while True:
        film_id = _film_queue.get()
        try:
            from .pipeline import story

            story.generate(film_id)
        except Exception as exc:  # noqa: BLE001
            db.update_film(film_id, status="error", error=str(exc))
        finally:
            _film_queue.task_done()


def _longform_loop() -> None:
    """Fourth queue: a documentary is forty minutes of transcription and then
    minutes of TTS, downloads and render. It waits behind other productions,
    never in front of a short or a film."""
    while True:
        action, project_id = _longform_queue.get()
        try:
            from .pipeline import longform

            longform.run(action, project_id)
        except Exception as exc:  # noqa: BLE001
            db.update_longform(project_id, status="error", error=str(exc))
        finally:
            _longform_queue.task_done()


def _analyze_plan(plan_id: str) -> None:
    """A livestream plan shares this queue and the `clip_plans` table with the
    long-video clipper, but needs the windowed analysis — a live is hours long
    and its transcript does not fit in a single prompt. `options_json.mode` is
    what tells the two apart."""
    from .pipeline import clipper_jobs, livecuts

    row = db.get_clip_plan(plan_id) or {}
    options = json.loads(row.get("options_json") or "{}")
    if options.get("mode") == livecuts.MODE:
        livecuts.analyze_plan(plan_id)
    else:
        clipper_jobs.analyze_plan(plan_id)


def _scheduler_loop() -> None:
    from .pipeline import notify
    from .pipeline.publishers import PLATFORM_LABEL, dispatch

    while True:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            for schedule in db.due_schedules(now_iso):
                job = db.get_job(schedule["job_id"])
                # Scheduled publication of a short that is still rendering
                # (clip batch): wait for the job to finish instead of failing.
                if job is not None and job["status"] in ("queued", "running"):
                    continue
                if job is None or job["status"] != "done":
                    db.update_schedule(schedule["id"], status="error",
                                       error="Short was not completed")
                    continue
                db.update_schedule(schedule["id"], status="publishing")
                label = PLATFORM_LABEL.get(schedule["platform"], schedule["platform"])
                try:
                    result = dispatch(schedule)
                    db.update_schedule(schedule["id"], status="published",
                                       result_json=result)
                    notify.published(schedule["job_id"], label,
                                     json.loads(result).get("url", ""))
                except Exception as exc:  # noqa: BLE001
                    db.update_schedule(schedule["id"], status="error", error=str(exc))
                    notify.publish_failed(schedule["job_id"], label, str(exc))
        except Exception:
            pass
        threading.Event().wait(30)


def _metrics_loop() -> None:
    """Pulls views/retention of the publications every few hours. The first
    round happens right after startup, so the dashboard is not empty until the
    next window."""
    from .pipeline import metrics

    time.sleep(20)
    while True:
        try:
            metrics.refresh_all()
        except Exception:
            pass
        time.sleep(metrics.REFRESH_EVERY_HOURS * 3600)


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    for _ in range(2):
        threading.Thread(target=_worker_loop, daemon=True).start()
    threading.Thread(target=_clip_loop, daemon=True).start()
    threading.Thread(target=_film_loop, daemon=True).start()
    threading.Thread(target=_longform_loop, daemon=True).start()
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    threading.Thread(target=_metrics_loop, daemon=True).start()

    # Requeue jobs interrupted by a restart. When the disk already has the
    # script and the narration, resume from there instead of spending LLM and
    # TTS again.
    for job in db.list_jobs(limit=200):
        if job["status"] in ("queued", "running"):
            job_dir = settings.jobs_dir / job["id"]
            stage = orchestrator.resumable_stage(job_dir) if job_dir.exists() else None
            if job["status"] == "running":
                db.log_event(job["id"],
                             "Server restarted mid-run"
                             + (f" — resuming from {stage}" if stage else ""), "warn")
            if stage:
                orchestrator.request_resume(job["id"], stage)
            _queue.put(job["id"])
    for plan in db.list_clip_plans(limit=50):
        if plan["status"] in ("queued", "analisando"):
            _clip_queue.put(plan["id"])
    # A film interrupted mid-run resumes from its per-shot ledger: whatever was
    # already generated is on disk and is not paid for a second time.
    for film in db.list_films(limit=50):
        if film["status"] in ("queued", "generating"):
            _film_queue.put(film["id"])
    # A production interrupted mid-run resumes from its per-block ledger; one
    # interrupted while ingesting starts the ingestion over (a half-transcribed
    # interview is not a catalog).
    for project in db.list_longform(limit=50):
        if project["status"] == "ingesting":
            _longform_queue.put(("ingest", project["id"]))
        elif project["status"] in ("queued", "assembling"):
            _longform_queue.put(("assemble", project["id"]))


def queue_size() -> int:
    return _queue.qsize()
