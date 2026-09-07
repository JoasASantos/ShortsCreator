"""Shared fixtures.

Every test runs against a temporary DATA_DIR: the database, the jobs and the
uploads all land in a throwaway directory, so nothing touches a developer's
real data. `settings` is an lru_cache singleton, so the env has to be in place
BEFORE the config module is first imported — hence the session-scoped autouse.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

_TMP = tempfile.mkdtemp(prefix="shortscreator-tests-")
os.environ["DATA_DIR"] = _TMP
os.environ.setdefault("LLM_PROVIDER", "chain")


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return Path(_TMP)


@pytest.fixture(autouse=True)
def fresh_db():
    """Clean database per test — the queue and metrics tests count rows."""
    from app import db

    db.init_db()
    with db.connect() as conn:
        for table in ("jobs", "job_events", "voices", "accounts", "clip_plans",
                      "connector_credentials", "schedules", "llm_calls", "metrics",
                      "films", "longform_projects"):
            conn.execute(f"DELETE FROM {table}")
    yield


def has_ffmpeg() -> bool:
    from shutil import which

    return bool(which("ffmpeg") and which("ffprobe"))


needs_ffmpeg = pytest.mark.skipif(not has_ffmpeg(),
                                  reason="FFmpeg is not on the PATH")


@pytest.fixture(scope="session")
def sample_video(data_dir: Path) -> Path:
    """A 4s 9:16 MP4 with audio: color bars plus a sine tone.

    A real file matters here — QA audits the final file with ffprobe/ffmpeg,
    so a mock would not exercise any of what actually counts.
    """
    if not has_ffmpeg():
        pytest.skip("FFmpeg is not on the PATH")
    out = data_dir / "sample_9x16.mp4"
    if out.exists():
        return out
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
         "-t", "4", str(out)],
        check=True, capture_output=True,
    )
    return out


@pytest.fixture
def words() -> list[dict]:
    """Word timings the way TTS returns them — the basis for the captions."""
    return [
        {"word": "Ninguém", "start": 0.10, "end": 0.55},
        {"word": "avisou", "start": 0.55, "end": 1.00},
        {"word": "que", "start": 1.00, "end": 1.18},
        {"word": "isso", "start": 1.18, "end": 1.52},
        {"word": "ia", "start": 1.52, "end": 1.70},
        {"word": "vazar", "start": 1.70, "end": 2.30},
    ]
