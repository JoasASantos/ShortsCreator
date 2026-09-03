"""Resuming an interrupted job — without spending LLM and TTS all over again."""
from __future__ import annotations

import json

from app.config import settings
from app.pipeline import orchestrator
from app.schemas import ShortScript

SCRIPT = ShortScript(
    title="Short de teste", description="", hashtags=[],
    segments=[{"kind": "hook", "text": "Ninguém avisou disso"},
              {"kind": "cta", "text": "Segue pra mais."}],
)


def _prepare(job_id: str, with_script=True, with_voice=True, with_background=True):
    job_dir = settings.job_dir(job_id)
    if with_script:
        (job_dir / "script.json").write_text(SCRIPT.model_dump_json(), encoding="utf-8")
    if with_voice:
        (job_dir / "narration.mp3").write_bytes(b"fake-audio")
        (job_dir / "narration.json").write_text(json.dumps(
            {"duration": 8.5, "words": [{"word": "oi", "start": 0.0, "end": 0.4}]}),
            encoding="utf-8")
    if with_background:
        bg = job_dir / "background.mp4"
        bg.write_bytes(b"fake-video")
        (job_dir / "background.json").write_text(json.dumps({"path": str(bg)}),
                                                 encoding="utf-8")
    return job_dir


def test_resumable_stage_reflects_what_exists_on_disk():
    empty = _prepare("job_vazio", with_script=False, with_voice=False, with_background=False)
    assert orchestrator.resumable_stage(empty) is None

    script_only = _prepare("job_roteiro", with_voice=False, with_background=False)
    assert orchestrator.resumable_stage(script_only) == "voz"

    # with a script and a voice but no background, resuming from captions would
    # leave the render with no picture: the furthest possible stage is the background
    without_background = _prepare("job_sem_fundo", with_background=False)
    assert orchestrator.resumable_stage(without_background) == "fundo"

    complete = _prepare("job_completo")
    assert orchestrator.resumable_stage(complete) == "legendas"


def test_load_resume_without_a_request_runs_everything():
    job_dir = _prepare("job_a")
    dirty, script, narration, background = orchestrator._load_resume(  # noqa: SLF001
        "job_a", job_dir, lambda *a, **k: None)
    assert dirty == set(orchestrator.CASCADE["script"])
    assert script is None and narration is None and background is None


def test_resume_from_captions_reuses_script_voice_and_background():
    job_dir = _prepare("job_b")
    orchestrator.request_resume("job_b", "legendas")

    dirty, script, narration, background = orchestrator._load_resume(  # noqa: SLF001
        "job_b", job_dir, lambda *a, **k: None)
    assert "script" not in dirty and "voz" not in dirty and "fundo" not in dirty
    assert "legendas" in dirty and "render" in dirty
    assert script.title == "Short de teste"
    assert narration is not None and narration.duration == 8.5
    assert narration.words[0]["word"] == "oi"
    # the background has to come back loaded: the render is handed this path and
    # without it the composition broke with "NoneType has no attribute name"
    assert background is not None and background.exists()
    # the marker is consumed: the next run goes through normally
    assert not (job_dir / "resume.json").exists()


def test_resume_without_a_saved_background_falls_back_to_the_background_stage():
    job_dir = _prepare("job_bg", with_background=False)
    orchestrator.request_resume("job_bg", "legendas")

    warnings: list[str] = []
    dirty, script, narration, background = orchestrator._load_resume(  # noqa: SLF001
        "job_bg", job_dir, lambda m, level="info": warnings.append(m))
    assert dirty == set(orchestrator.CASCADE["fundo"])
    assert background is None
    assert narration is not None, "the narration is still reused"
    assert any("background" in w for w in warnings)


def test_saved_background_prefers_the_recorded_path():
    job_dir = settings.job_dir("job_bgmeta")
    recorded = job_dir / "background_scroll_padded.mp4"
    recorded.write_bytes(b"x")
    (job_dir / "background.mp4").write_bytes(b"y")
    (job_dir / "background.json").write_text(json.dumps({"path": str(recorded)}),
                                             encoding="utf-8")
    assert orchestrator.saved_background(job_dir) == recorded


def test_saved_background_falls_back_to_known_filenames_without_metadata():
    """Jobs rendered before background.json existed must not turn into a dead
    end."""
    job_dir = settings.job_dir("job_bgvelho")
    (job_dir / "background.mp4").write_bytes(b"y")
    assert orchestrator.saved_background(job_dir) == job_dir / "background.mp4"


def test_saved_background_ignores_metadata_pointing_at_a_vanished_file():
    job_dir = settings.job_dir("job_bgquebrado")
    (job_dir / "background.json").write_text(
        json.dumps({"path": str(job_dir / "nao_existe.mp4")}), encoding="utf-8")
    assert orchestrator.saved_background(job_dir) is None


def test_resume_from_voice_keeps_the_script_and_redoes_the_narration():
    job_dir = _prepare("job_c")
    orchestrator.request_resume("job_c", "voz")

    dirty, script, narration, background = orchestrator._load_resume(  # noqa: SLF001
        "job_c", job_dir, lambda *a, **k: None)
    assert "script" not in dirty and "voz" in dirty
    assert script is not None
    assert narration is None, "the narration has to be synthesized again"


def test_load_resume_prefers_the_hand_edited_script():
    job_dir = _prepare("job_d")
    edited = SCRIPT.model_copy(update={"title": "Título editado"})
    (job_dir / "script_override.json").write_text(edited.model_dump_json(), encoding="utf-8")
    orchestrator.request_resume("job_d", "legendas")

    _, script, _, _ = orchestrator._load_resume(  # noqa: SLF001
        "job_d", job_dir, lambda *a, **k: None)
    assert script.title == "Título editado"


def test_load_resume_without_any_artifact_falls_back_to_the_start():
    job_dir = _prepare("job_e", with_script=False, with_voice=False,
                       with_background=False)
    orchestrator.request_resume("job_e", "legendas")

    warnings: list[str] = []
    dirty, script, _, _ = orchestrator._load_resume(  # noqa: SLF001
        "job_e", job_dir, lambda m, level="info": warnings.append(m))
    assert dirty == set(orchestrator.CASCADE["script"])
    assert script is None
    assert any("script" in w for w in warnings)


def test_load_resume_without_a_narration_falls_back_to_the_voice_stage():
    job_dir = _prepare("job_f", with_voice=False)
    orchestrator.request_resume("job_f", "legendas")

    dirty, script, narration, background = orchestrator._load_resume(  # noqa: SLF001
        "job_f", job_dir, lambda *a, **k: None)
    assert dirty == set(orchestrator.CASCADE["voz"])
    assert script is not None and narration is None
    assert background is None


def test_request_resume_rejects_an_unknown_stage():
    import pytest

    with pytest.raises(ValueError):
        orchestrator.request_resume("job_g", "etapa_que_nao_existe")


def test_cascade_invalidates_every_dependent_stage():
    """Redoing a stage has to invalidate everything that depends on it."""
    assert orchestrator.CASCADE["script"] >= {"voz", "legendas", "fundo", "render"}
    assert orchestrator.CASCADE["voz"] >= {"legendas", "render"}
    assert orchestrator.CASCADE["render"] == {"render"}
    # editing the narration on disk does not re-synthesize the voice
    assert "voz" not in orchestrator.CASCADE["narracao_editada"]
    assert "legendas" in orchestrator.CASCADE["narracao_editada"]
