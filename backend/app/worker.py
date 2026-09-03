"""Threaded worker: job queue + publication scheduler + metrics collection."""
from __future__ import annotations

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
_started = False
_lock = threading.Lock()


def enqueue(job_id: str) -> None:
    db.update_job(job_id, status="queued", stage="queued", progress=0.0)
    _queue.put(job_id)


def enqueue_clip_plan(plan_id: str) -> None:
    db.update_clip_plan(plan_id, status="queued")
    _clip_queue.put(plan_id)


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
    from .pipeline import clipper_jobs

    while True:
        plan_id = _clip_queue.get()
        try:
            clipper_jobs.analyze_plan(plan_id)
        except Exception as exc:  # noqa: BLE001
            db.update_clip_plan(plan_id, status="error", error=str(exc))
        finally:
            _clip_queue.task_done()


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
                    import json

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


def queue_size() -> int:
    return _queue.qsize()
