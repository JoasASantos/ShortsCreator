"""Which background a job ends up with, and whether it says so.

The gradient looks the same whether it was chosen or merely settled for. These
tests are about the difference: a short must never come out looking finished
with none of the footage the user asked for and nothing explaining why.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline import orchestrator
from app.pipeline.ingest import SourceMaterial
from app.schemas import JobInput, ScriptSegment, ShortScript


def _script() -> ShortScript:
    return ShortScript(
        title="t", description="d", hashtags=[],
        segments=[ScriptSegment(kind="corpo", text="uma frase narrada")],
        estimated_seconds=20)


class _Narration:
    duration = 20.0
    words = [{"word": "uma", "start": 0.0, "end": 0.4}]


def _run(job: JobInput, material: SourceMaterial, tmp_path: Path,
         monkeypatch) -> tuple[str, list[tuple[str, str]]]:
    """Run the background stage with the renderers stubbed out, and report
    which one was called plus everything that was logged."""
    called: list[str] = []
    events: list[tuple[str, str]] = []

    from app.pipeline import broll, render

    def record(name: str):
        return lambda *a, **k: called.append(name)

    for name in ("background_gradient", "background_from_video",
                 "background_from_clips", "background_from_images_kenburns",
                 "background_from_multi_highlights"):
        monkeypatch.setattr(render, name, record(name))
    monkeypatch.setattr(render, "ensure_min_duration", lambda p, d, w: p)
    monkeypatch.setattr(broll, "providers_ready", lambda: [])

    def log(message: str, level: str = "info") -> None:
        events.append((level, message))

    orchestrator._build_background(  # noqa: SLF001
        job, _script(), _Narration(), material, tmp_path, 20.0, log)
    return (called[0] if called else ""), events


# ------------------------------------------------------- the silent fallback

def test_a_gradient_settled_for_says_so(tmp_path, monkeypatch):
    """Nothing to show and no stock bank: the gradient is the only option left,
    but the user has to learn that from the job and not from staring at a
    finished-looking short wondering where their video went."""
    job = JobInput(source_type="tema", source="um tema qualquer", background="auto")
    material = SourceMaterial(kind="tema", title="x", text="x")

    used, events = _run(job, material, tmp_path, monkeypatch)

    assert used == "background_gradient"
    warnings = [m for level, m in events if level == "warn"]
    assert warnings, "settling for a gradient has to be reported"
    assert "gradient" in warnings[0].lower()
    # and it has to say what to do about it
    assert "link" in warnings[0].lower() or "upload" in warnings[0].lower()


def test_a_gradient_asked_for_is_not_a_warning(tmp_path, monkeypatch):
    """Choosing the gradient deliberately is a normal thing to do."""
    job = JobInput(source_type="tema", source="tema", background="gradiente")
    material = SourceMaterial(kind="tema", title="x", text="x")

    used, events = _run(job, material, tmp_path, monkeypatch)

    assert used == "background_gradient"
    assert not [m for level, m in events if level == "warn"]


# --------------------------------------------------- the source video branch

def test_the_source_video_is_used_when_there_is_one(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fake")
    job = JobInput(source_type="video", source="https://youtu.be/x",
                   background="auto")
    material = SourceMaterial(kind="video", title="x", video_path=video)

    used, _ = _run(job, material, tmp_path, monkeypatch)
    assert used == "background_from_video"


def test_asking_for_the_source_video_without_one_refuses(tmp_path, monkeypatch):
    """Regression: this used to skip the branch entirely and fall through to
    the gradient, so a job asked to use its own footage came out with none of
    it and reported success. The image branch has always refused here; this
    one only pretended to work."""
    job = JobInput(source_type="tema", source="um tema", background="video_fonte")
    material = SourceMaterial(kind="tema", title="x", text="x")

    with pytest.raises(RuntimeError, match="no video was downloaded"):
        _run(job, material, tmp_path, monkeypatch)


def test_the_refusal_says_what_to_do(tmp_path, monkeypatch):
    job = JobInput(source_type="tema", source="um tema", background="video_fonte")
    material = SourceMaterial(kind="tema", title="x", text="x")

    with pytest.raises(RuntimeError) as caught:
        _run(job, material, tmp_path, monkeypatch)

    message = str(caught.value)
    assert "upload" in message or "Paste" in message
    assert "background" in message


def test_asking_for_images_without_one_still_refuses(tmp_path, monkeypatch):
    """The behaviour the video branch now matches."""
    job = JobInput(source_type="imagem", background="imagem_kenburns")
    material = SourceMaterial(kind="imagem", title="x")

    with pytest.raises(RuntimeError, match="no image"):
        _run(job, material, tmp_path, monkeypatch)


# ------------------------------------------------------------- what auto does

@pytest.mark.parametrize("kind,expected", [
    ("video", "background_from_video"),
    ("tema", "background_gradient"),
    ("artigo", "background_gradient"),
])
def test_auto_follows_the_material(kind, expected, tmp_path, monkeypatch):
    """A downloaded video is always preferred over a gradient — that is the
    whole reason someone pastes a link."""
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fake")
    material = SourceMaterial(kind=kind, title="x",
                              video_path=video if kind == "video" else None)
    job = JobInput(source_type="url", source="https://x.test/a", background="auto")

    used, _ = _run(job, material, tmp_path, monkeypatch)
    assert used == expected
