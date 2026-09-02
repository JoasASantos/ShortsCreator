"""Fixtures compartilhadas.

Cada teste roda contra um DATA_DIR temporário: o banco, os jobs e os uploads
saem num diretório descartável, então nada toca os dados reais de quem
desenvolve. `settings` é um singleton com lru_cache, por isso o env precisa
estar no lugar ANTES do primeiro import do módulo de config — daí o
autouse com escopo de sessão.
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
    """Banco limpo por teste — os testes de fila e métricas contam registros."""
    from app import db

    db.init_db()
    with db.connect() as conn:
        for table in ("jobs", "job_events", "voices", "accounts", "clip_plans",
                      "connector_credentials", "schedules", "llm_calls", "metrics"):
            conn.execute(f"DELETE FROM {table}")
    yield


def has_ffmpeg() -> bool:
    from shutil import which

    return bool(which("ffmpeg") and which("ffprobe"))


needs_ffmpeg = pytest.mark.skipif(not has_ffmpeg(),
                                  reason="FFmpeg não está no PATH")


@pytest.fixture(scope="session")
def sample_video(data_dir: Path) -> Path:
    """MP4 9:16 de 4s com áudio: barras coloridas + tom senoidal.

    Um arquivo de verdade importa aqui — o QA audita o arquivo final com
    ffprobe/ffmpeg, então um mock não exercitaria nada do que interessa.
    """
    if not has_ffmpeg():
        pytest.skip("FFmpeg não está no PATH")
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
    """Timings de palavra como o TTS devolve — base das legendas."""
    return [
        {"word": "Ninguém", "start": 0.10, "end": 0.55},
        {"word": "avisou", "start": 0.55, "end": 1.00},
        {"word": "que", "start": 1.00, "end": 1.18},
        {"word": "isso", "start": 1.18, "end": 1.52},
        {"word": "ia", "start": 1.52, "end": 1.70},
        {"word": "vazar", "start": 1.70, "end": 2.30},
    ]
