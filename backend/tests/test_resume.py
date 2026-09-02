"""Retomada de job interrompido — sem gastar LLM e TTS de novo."""
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


def _prepare(job_id: str, with_script=True, with_voice=True):
    job_dir = settings.job_dir(job_id)
    if with_script:
        (job_dir / "script.json").write_text(SCRIPT.model_dump_json(), encoding="utf-8")
    if with_voice:
        (job_dir / "narration.mp3").write_bytes(b"fake-audio")
        (job_dir / "narration.json").write_text(json.dumps(
            {"duration": 8.5, "words": [{"word": "oi", "start": 0.0, "end": 0.4}]}),
            encoding="utf-8")
    return job_dir


def test_resumable_stage_reflete_o_que_existe_no_disco():
    vazio = _prepare("job_vazio", with_script=False, with_voice=False)
    assert orchestrator.resumable_stage(vazio) is None

    so_roteiro = _prepare("job_roteiro", with_voice=False)
    assert orchestrator.resumable_stage(so_roteiro) == "voz"

    completo = _prepare("job_completo")
    assert orchestrator.resumable_stage(completo) == "legendas"


def test_load_resume_sem_pedido_roda_tudo():
    job_dir = _prepare("job_a")
    dirty, script, narration = orchestrator._load_resume(  # noqa: SLF001
        "job_a", job_dir, lambda *a, **k: None)
    assert dirty == set(orchestrator.CASCADE["script"])
    assert script is None and narration is None


def test_load_resume_de_legendas_reaproveita_roteiro_e_voz():
    job_dir = _prepare("job_b")
    orchestrator.request_resume("job_b", "legendas")

    dirty, script, narration = orchestrator._load_resume(  # noqa: SLF001
        "job_b", job_dir, lambda *a, **k: None)
    assert "script" not in dirty and "voz" not in dirty
    assert "legendas" in dirty and "render" in dirty
    assert script.title == "Short de teste"
    assert narration is not None and narration.duration == 8.5
    assert narration.words[0]["word"] == "oi"
    # o marcador é consumido: a execução seguinte roda normal
    assert not (job_dir / "resume.json").exists()


def test_load_resume_de_voz_mantem_o_roteiro_e_refaz_a_narracao():
    job_dir = _prepare("job_c")
    orchestrator.request_resume("job_c", "voz")

    dirty, script, narration = orchestrator._load_resume(  # noqa: SLF001
        "job_c", job_dir, lambda *a, **k: None)
    assert "script" not in dirty and "voz" in dirty
    assert script is not None
    assert narration is None, "a narração precisa ser sintetizada de novo"


def test_load_resume_prefere_o_roteiro_editado_a_mao():
    job_dir = _prepare("job_d")
    editado = SCRIPT.model_copy(update={"title": "Título editado"})
    (job_dir / "script_override.json").write_text(editado.model_dump_json(), encoding="utf-8")
    orchestrator.request_resume("job_d", "legendas")

    _, script, _ = orchestrator._load_resume(  # noqa: SLF001
        "job_d", job_dir, lambda *a, **k: None)
    assert script.title == "Título editado"


def test_load_resume_sem_artefato_cai_para_o_inicio():
    job_dir = _prepare("job_e", with_script=False, with_voice=False)
    orchestrator.request_resume("job_e", "legendas")

    avisos: list[str] = []
    dirty, script, _ = orchestrator._load_resume(  # noqa: SLF001
        "job_e", job_dir, lambda m, level="info": avisos.append(m))
    assert dirty == set(orchestrator.CASCADE["script"])
    assert script is None
    assert any("roteiro" in a for a in avisos)


def test_load_resume_sem_narracao_volta_para_a_voz():
    job_dir = _prepare("job_f", with_voice=False)
    orchestrator.request_resume("job_f", "legendas")

    dirty, script, narration = orchestrator._load_resume(  # noqa: SLF001
        "job_f", job_dir, lambda *a, **k: None)
    assert dirty == set(orchestrator.CASCADE["voz"])
    assert script is not None and narration is None


def test_request_resume_recusa_etapa_desconhecida():
    import pytest

    with pytest.raises(ValueError):
        orchestrator.request_resume("job_g", "etapa_que_nao_existe")


def test_cascade_invalidacao_de_etapas():
    """Refazer uma etapa precisa invalidar tudo que depende dela."""
    assert orchestrator.CASCADE["script"] >= {"voz", "legendas", "fundo", "render"}
    assert orchestrator.CASCADE["voz"] >= {"legendas", "render"}
    assert orchestrator.CASCADE["render"] == {"render"}
    # editar a narração no disco não re-sintetiza a voz
    assert "voz" not in orchestrator.CASCADE["narracao_editada"]
    assert "legendas" in orchestrator.CASCADE["narracao_editada"]
