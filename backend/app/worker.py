"""Worker em thread: fila de jobs + scheduler de publicações."""
from __future__ import annotations

import queue
import threading
import traceback
from datetime import datetime, timezone

from . import db
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
    """Fila separada: analisar um vídeo longo leva minutos e não pode
    bloquear a renderização dos shorts já enfileirados."""
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
    from .pipeline.publishers import dispatch

    while True:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            for schedule in db.due_schedules(now_iso):
                db.update_schedule(schedule["id"], status="publishing")
                try:
                    result = dispatch(schedule)
                    db.update_schedule(schedule["id"], status="published",
                                       result_json=result)
                except Exception as exc:  # noqa: BLE001
                    db.update_schedule(schedule["id"], status="error", error=str(exc))
        except Exception:
            pass
        threading.Event().wait(30)


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

    # Requeue de jobs interrompidos por restart
    for job in db.list_jobs(limit=200):
        if job["status"] in ("queued", "running"):
            _queue.put(job["id"])
    for plan in db.list_clip_plans(limit=50):
        if plan["status"] in ("queued", "analisando"):
            _clip_queue.put(plan["id"])


def queue_size() -> int:
    return _queue.qsize()
