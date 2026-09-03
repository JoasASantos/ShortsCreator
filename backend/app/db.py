"""Data layer on plain sqlite3 — no ORM, zero fragile dependencies."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT,
    status TEXT NOT NULL,
    stage TEXT,
    progress REAL DEFAULT 0,
    input_json TEXT NOT NULL,
    result_json TEXT,
    qa_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_voice_id TEXT,
    sample_path TEXT,
    settings_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    display_name TEXT NOT NULL,
    credentials_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clip_plans (
    id TEXT PRIMARY KEY,
    attachment_id TEXT NOT NULL,
    status TEXT NOT NULL,
    requested INTEGER NOT NULL,
    target_seconds INTEGER NOT NULL,
    options_json TEXT,
    clips_json TEXT,
    jobs_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connector_credentials (
    connector_id TEXT PRIMARY KEY,
    values_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    publish_at TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    seconds REAL NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    schedule_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL,
    url TEXT,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    avg_view_seconds REAL,
    avg_view_pct REAL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    error TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


# ---------- jobs ----------

def create_job(input_data: dict, title: str = "") -> str:
    job_id = new_id("job")
    ts = now()
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id,title,status,stage,progress,input_json,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (job_id, title, "queued", "queued", 0.0, json.dumps(input_data), ts, ts),
        )
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, connect() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def get_job(job_id: str) -> dict | None:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def list_jobs(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_job(job_id: str) -> None:
    with _lock, connect() as conn:
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.execute("DELETE FROM job_events WHERE job_id=?", (job_id,))


def log_event(job_id: str, message: str, level: str = "info") -> None:
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO job_events (job_id,level,message,created_at) VALUES (?,?,?,?)",
            (job_id, level, message, now()),
        )


def get_events(job_id: str, after_id: int = 0) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM job_events WHERE job_id=? AND id>? ORDER BY id", (job_id, after_id)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- voices ----------

def create_voice(name: str, provider: str, provider_voice_id: str = "",
                 sample_path: str = "", settings_json: dict | None = None) -> str:
    vid = new_id("voice")
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO voices (id,name,provider,provider_voice_id,sample_path,settings_json,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (vid, name, provider, provider_voice_id, sample_path,
             json.dumps(settings_json or {}), now()),
        )
    return vid


def list_voices() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM voices ORDER BY created_at DESC")]


def get_voice(voice_id: str) -> dict | None:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM voices WHERE id=?", (voice_id,)).fetchone())


def delete_voice(voice_id: str) -> None:
    with _lock, connect() as conn:
        conn.execute("DELETE FROM voices WHERE id=?", (voice_id,))


# ---------- accounts ----------

def create_account(platform: str, display_name: str, credentials: dict) -> str:
    aid = new_id("acct")
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO accounts (id,platform,display_name,credentials_json,created_at)"
            " VALUES (?,?,?,?,?)",
            (aid, platform, display_name, json.dumps(credentials), now()),
        )
    return aid


def upsert_account_for_platform(platform: str, display_name: str,
                                credentials: dict) -> str:
    """Fixed-token platforms (Instagram, LinkedIn) have exactly one account per
    connector: update the existing one instead of piling up duplicates."""
    with _lock, connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE platform=?",
                           (platform,)).fetchone()
        if row:
            conn.execute("UPDATE accounts SET display_name=?, credentials_json=? WHERE id=?",
                         (display_name, json.dumps(credentials), row["id"]))
            return row["id"]
        aid = new_id("acct")
        conn.execute(
            "INSERT INTO accounts (id,platform,display_name,credentials_json,created_at)"
            " VALUES (?,?,?,?,?)",
            (aid, platform, display_name, json.dumps(credentials), now()),
        )
        return aid


def delete_accounts_for_platform(platform: str) -> None:
    with _lock, connect() as conn:
        conn.execute("DELETE FROM accounts WHERE platform=?", (platform,))


def list_accounts() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY created_at DESC")]


def get_account(account_id: str) -> dict | None:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone())


def delete_account(account_id: str) -> None:
    with _lock, connect() as conn:
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))


# ---------- connector credentials ----------

def get_connector(connector_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT values_json FROM connector_credentials WHERE connector_id=?",
            (connector_id,),
        ).fetchone()
    return json.loads(row["values_json"]) if row else None


def save_connector(connector_id: str, values: dict) -> None:
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO connector_credentials (connector_id, values_json, updated_at)"
            " VALUES (?,?,?)"
            " ON CONFLICT(connector_id) DO UPDATE SET values_json=excluded.values_json,"
            " updated_at=excluded.updated_at",
            (connector_id, json.dumps(values), now()),
        )


def delete_connector(connector_id: str) -> None:
    with _lock, connect() as conn:
        conn.execute("DELETE FROM connector_credentials WHERE connector_id=?",
                    (connector_id,))


# ---------- clip plans ----------

def create_clip_plan(attachment_id: str, requested: int, target_seconds: int,
                     options: dict) -> str:
    plan_id = new_id("plan")
    ts = now()
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO clip_plans (id,attachment_id,status,requested,target_seconds,"
            "options_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (plan_id, attachment_id, "queued", requested, target_seconds,
             json.dumps(options), ts, ts),
        )
    return plan_id


def update_clip_plan(plan_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, connect() as conn:
        conn.execute(f"UPDATE clip_plans SET {cols} WHERE id=?",
                     (*fields.values(), plan_id))


def get_clip_plan(plan_id: str) -> dict | None:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM clip_plans WHERE id=?",
                                 (plan_id,)).fetchone())


def list_clip_plans(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clip_plans ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- schedules ----------

def create_schedule(job_id: str, account_id: str, platform: str,
                    publish_at: str, payload: dict) -> str:
    sid = new_id("sched")
    ts = now()
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO schedules (id,job_id,account_id,platform,publish_at,status,payload_json,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, job_id, account_id, platform, publish_at, "pending",
             json.dumps(payload), ts, ts),
        )
    return sid


def update_schedule(schedule_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, connect() as conn:
        conn.execute(f"UPDATE schedules SET {cols} WHERE id=?", (*fields.values(), schedule_id))


def list_schedules() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM schedules ORDER BY publish_at")]


def due_schedules(now_iso: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE status='pending' AND publish_at<=?", (now_iso,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_schedule(schedule_id: str) -> None:
    with _lock, connect() as conn:
        conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))


def get_schedule(schedule_id: str) -> dict | None:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM schedules WHERE id=?",
                                 (schedule_id,)).fetchone())


def published_schedules(since_iso: str) -> list[dict]:
    """Publications completed since a given date — the basis of metric collection."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE status='published' AND updated_at>=?"
            " ORDER BY updated_at DESC", (since_iso,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- LLM calls ----------

def log_llm_call(job_id: str | None, purpose: str, provider: str, model: str,
                 seconds: float, ok: bool, error: str = "") -> None:
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO llm_calls (job_id,purpose,provider,model,seconds,ok,error,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (job_id, purpose, provider, model, round(seconds, 2), int(ok),
             error[:400], now()),
        )


def llm_calls_for_job(job_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM llm_calls WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def llm_call_summary(limit_days: int = 30) -> list[dict]:
    """Aggregate per provider/model: how many calls, success rate, latency."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT provider, model, COUNT(*) AS calls, SUM(ok) AS ok,"
            " ROUND(AVG(seconds),1) AS avg_seconds, ROUND(SUM(seconds)) AS total_seconds"
            " FROM llm_calls WHERE created_at >= datetime('now', ?)"
            " GROUP BY provider, model ORDER BY calls DESC",
            (f"-{limit_days} days",),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- publication metrics ----------

def upsert_metrics(schedule_id: str, job_id: str, platform: str, video_id: str,
                   url: str, data: dict, error: str = "") -> None:
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO metrics (schedule_id,job_id,platform,video_id,url,views,likes,"
            "comments,shares,avg_view_seconds,avg_view_pct,published_at,fetched_at,error)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(schedule_id) DO UPDATE SET"
            " views=excluded.views, likes=excluded.likes, comments=excluded.comments,"
            " shares=excluded.shares, avg_view_seconds=excluded.avg_view_seconds,"
            " avg_view_pct=excluded.avg_view_pct, fetched_at=excluded.fetched_at,"
            " error=excluded.error, url=excluded.url",
            (schedule_id, job_id, platform, video_id, url,
             int(data.get("views", 0) or 0), int(data.get("likes", 0) or 0),
             int(data.get("comments", 0) or 0), int(data.get("shares", 0) or 0),
             data.get("avg_view_seconds"), data.get("avg_view_pct"),
             data.get("published_at"), now(), error[:400]),
        )


def list_metrics() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT m.*, j.title, j.input_json FROM metrics m"
            " LEFT JOIN jobs j ON j.id = m.job_id ORDER BY m.views DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def metrics_for_job(job_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM metrics WHERE job_id=? ORDER BY fetched_at DESC", (job_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def metrics_by_jobs(job_ids: list[str]) -> dict[str, dict]:
    """Aggregate summary (all platforms) per job — for the dashboard list."""
    if not job_ids:
        return {}
    marks = ",".join("?" * len(job_ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT job_id, SUM(views) AS views, SUM(likes) AS likes,"
            f" MAX(avg_view_pct) AS avg_view_pct, COUNT(*) AS platforms"
            f" FROM metrics WHERE job_id IN ({marks}) GROUP BY job_id",
            job_ids,
        ).fetchall()
    return {r["job_id"]: dict(r) for r in rows}
