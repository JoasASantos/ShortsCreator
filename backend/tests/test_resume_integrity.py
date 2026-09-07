"""Resuming onto artifacts that a restart left half-written.

The pipeline writes its intermediates to disk so a crash costs minutes instead
of a whole job. That bargain only holds if "the file is there" means "the file
can be read": an MP4 keeps its index in the `moov` atom at the END, so one
interrupted mid-write is present, plausibly sized, and unopenable. Resuming
past it turns a recoverable interruption into a failed job — which is exactly
what happened.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.pipeline import orchestrator

from conftest import needs_ffmpeg


def _truncated_mp4(path: Path) -> Path:
    """What a restart during the background stage leaves behind: an ftyp
    header and no moov."""
    path.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 4000)
    return path


def _truncated_mp3(path: Path) -> Path:
    path.write_bytes(b"\xff\xfb" + b"\x00" * 2000)
    return path


def _real_mp4(path: Path, seconds: float = 1.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c=black:s=64x64:d={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(path)], check=True, capture_output=True)
    return path


def _real_mp3(path: Path, seconds: float = 1.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}",
         "-c:a", "libmp3lame", str(path)], check=True, capture_output=True)
    return path


def _job(tmp_path: Path, *, script=True, voice=True) -> Path:
    if script:
        (tmp_path / "script.json").write_text("{}", encoding="utf-8")
    if voice:
        (tmp_path / "narration.json").write_text("{}", encoding="utf-8")
    return tmp_path


# ------------------------------------------------------- the background

@needs_ffmpeg
def test_a_half_written_background_is_not_offered_as_saved(tmp_path):
    """Regression: `background.mp4` existed and `saved_background` returned it,
    so the resume promised a background the render then could not open —
    `moov atom not found`, job failed."""
    _truncated_mp4(tmp_path / "background.mp4")
    assert orchestrator.saved_background(tmp_path) is None


@needs_ffmpeg
def test_a_complete_background_is_still_reused(tmp_path):
    """The check must not throw away work that is genuinely usable — that is
    the whole point of keeping intermediates."""
    _real_mp4(tmp_path / "background.mp4")
    found = orchestrator.saved_background(tmp_path)
    assert found is not None and found.name == "background.mp4"


@needs_ffmpeg
def test_a_pointer_to_a_half_written_file_is_not_trusted_either(tmp_path):
    """background.json names the effective path; the file it names gets the
    same scrutiny as one found by its known name."""
    broken = _truncated_mp4(tmp_path / "background_scroll.mp4")
    (tmp_path / "background.json").write_text(
        f'{{"path": "{broken}"}}', encoding="utf-8")
    assert orchestrator.saved_background(tmp_path) is None


@needs_ffmpeg
def test_the_pointer_wins_when_the_file_it_names_is_good(tmp_path):
    real = _real_mp4(tmp_path / "background_scroll.mp4")
    (tmp_path / "background.json").write_text(
        f'{{"path": "{real}"}}', encoding="utf-8")
    assert orchestrator.saved_background(tmp_path) == real


# ---------------------------------------------------------- the narration

@needs_ffmpeg
def test_a_half_written_narration_sends_the_resume_back_to_the_voice(tmp_path):
    """Resuming from the captions stage times every word against the audio.
    Against a truncated file there is nothing to time."""
    job = _job(tmp_path)
    _truncated_mp3(job / "narration.mp3")
    _real_mp4(job / "background.mp4")
    assert orchestrator.resumable_stage(job) == "voz"


@needs_ffmpeg
def test_an_unprobeable_file_answers_the_question_instead_of_raising(tmp_path):
    """`tts.audio_duration` raises on a corrupt file rather than returning 0.
    Letting that escape would replace a recoverable stage with a crash — the
    very failure this code exists to prevent."""
    job = _job(tmp_path)
    (job / "narration.mp3").write_bytes(b"not audio at all")
    _real_mp4(job / "background.mp4")
    assert orchestrator.resumable_stage(job) == "voz"   # no exception


# -------------------------------------------------- what the resume decides

@needs_ffmpeg
def test_everything_readable_resumes_from_the_captions(tmp_path):
    job = _job(tmp_path)
    _real_mp3(job / "narration.mp3")
    _real_mp4(job / "background.mp4")
    assert orchestrator.resumable_stage(job) == "legendas"


@needs_ffmpeg
def test_a_good_narration_without_a_background_resumes_from_the_background(tmp_path):
    job = _job(tmp_path)
    _real_mp3(job / "narration.mp3")
    assert orchestrator.resumable_stage(job) == "fundo"


@needs_ffmpeg
def test_a_good_narration_with_a_broken_background_redoes_the_background(tmp_path):
    """The exact shape of the reported failure: the voice survived the restart,
    the background did not."""
    job = _job(tmp_path)
    _real_mp3(job / "narration.mp3")
    _truncated_mp4(job / "background.mp4")
    assert orchestrator.resumable_stage(job) == "fundo"


def test_no_script_means_no_resume(tmp_path):
    assert orchestrator.resumable_stage(_job(tmp_path, script=False)) is None
