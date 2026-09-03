"""Roteiro: divisão de texto colado, troca de gancho, insights e cortes."""
from __future__ import annotations

import json

from app import db
from app.pipeline import clipper, metrics, script as script_mod
from app.pipeline.ingest import SourceMaterial
from app.schemas import JobInput, ShortScript

JOB = JobInput(source_type="roteiro", source="", duration=40, cta="Segue pra mais.")

SCRIPT = ShortScript(
    title="Vazamento", description="", hashtags=["#seg"],
    segments=[{"kind": "hook", "text": "Gancho original"},
              {"kind": "corpo", "text": "Corpo do roteiro com o conteúdo."},
              {"kind": "cta", "text": "Segue pra mais."}],
)


def test_roteiro_colado_nao_chama_llm(monkeypatch):
    monkeypatch.setattr(script_mod.llm, "complete_json",
                        lambda *a, **k: pytest_fail())
    material = SourceMaterial(kind="roteiro", title="Meu roteiro",
                              text="Primeira frase. Segunda frase. Terceira frase.")
    result = script_mod.build_script(JOB, material)
    assert result.segments[0].kind == "hook"
    assert result.segments[-1].kind == "cta"
    assert "Primeira frase" in script_mod.full_narration(result)


def pytest_fail():
    raise AssertionError("source_type='roteiro' não deveria chamar o LLM")


def test_roteiro_colado_acrescenta_cta_quando_falta():
    material = SourceMaterial(kind="roteiro", text="Só uma frase aqui.")
    result = script_mod.build_script(JOB, material)
    assert result.segments[-1].text == "Segue pra mais."


def test_with_hook_troca_so_o_primeiro_segmento():
    novo = script_mod.with_hook(SCRIPT, "  Gancho novo e agressivo  ")
    assert novo.segments[0].text == "Gancho novo e agressivo"
    assert novo.segments[0].kind == "hook"
    assert [s.text for s in novo.segments[1:]] == [s.text for s in SCRIPT.segments[1:]]
    # o roteiro original não é mutado
    assert SCRIPT.segments[0].text == "Gancho original"


def test_with_hook_em_roteiro_sem_hook_marca_o_primeiro():
    sem_hook = SCRIPT.model_copy(update={"segments": [
        s.model_copy(update={"kind": "corpo"}) for s in SCRIPT.segments]})
    novo = script_mod.with_hook(sem_hook, "Agora tem gancho")
    assert novo.segments[0].kind == "hook"
    assert novo.segments[0].text == "Agora tem gancho"


def test_build_hook_variants_usa_o_corpo_e_pede_mecanismos(monkeypatch):
    capturado = {}

    def fake(system, prompt, schema=None, max_tokens=8000, purpose=""):
        capturado["prompt"] = prompt
        capturado["purpose"] = purpose
        return {"hooks": [
            {"text": "Você está fazendo isso errado", "mechanism": "afirmação polêmica", "why": "provoca"},
            {"text": "70% das empresas caem nisso", "mechanism": "número", "why": "concreto"},
        ]}

    monkeypatch.setattr(script_mod.llm, "complete_json", fake)
    hooks = script_mod.build_hook_variants(SCRIPT, JOB, count=2)
    assert len(hooks) == 2
    assert capturado["purpose"] == "hooks"
    assert "Gancho original" in capturado["prompt"]
    assert "Corpo do roteiro" in capturado["prompt"]


def test_build_hook_variants_sem_resposta_falha_claro(monkeypatch):
    import pytest

    monkeypatch.setattr(script_mod.llm, "complete_json", lambda *a, **k: {"hooks": []})
    with pytest.raises(RuntimeError, match="ganchos"):
        script_mod.build_hook_variants(SCRIPT, JOB)


def test_segment_durations_cobrem_a_timeline_inteira():
    words = [{"word": f"p{i}", "start": i * 0.4, "end": i * 0.4 + 0.35} for i in range(12)]
    script = ShortScript(title="t", description="", segments=[
        {"kind": "hook", "text": "a b c d"},
        {"kind": "corpo", "text": "e f g h"},
        {"kind": "cta", "text": "i j k l"},
    ])
    total = 6.0
    durations = script_mod.segment_durations_covering(script, words, total)
    assert len(durations) == 3
    # a soma cobre o vídeo todo: sem isso o fundo acaba antes do áudio
    assert abs(sum(durations) - total) < 0.01


def test_insights_vazio_sem_metricas():
    assert metrics.insights("tecnologia") == ""


def test_insights_lista_hooks_ordenados_por_retencao():
    def criar(nome: str, views: int, retencao: float | None) -> None:
        job = JobInput(source_type="tema", source=nome, niche="tecnologia")
        job_id = db.create_job(job.model_dump(), nome)
        db.update_job(job_id, status="done", result_json=json.dumps({
            "title": nome, "script": {"segments": [
                {"kind": "hook", "text": f"gancho de {nome}"}]}}))
        acc = db.create_account("youtube", "Canal", {"token": "x"})
        sched = db.create_schedule(job_id, acc, "youtube", "2026-01-01T00:00:00+00:00", {})
        db.upsert_metrics(sched, job_id, "youtube", f"v-{nome}", "",
                          {"views": views, "avg_view_pct": retencao})

    criar("fraco", 50000, 20.0)
    criar("forte", 1000, 85.0)

    briefing = metrics.insights("tecnologia")
    assert "gancho de forte" in briefing
    # retenção manda: o de 85% aparece antes do de 50k views
    assert briefing.index("gancho de forte") < briefing.index("gancho de fraco")
    assert "85%" in briefing


def test_insights_filtra_por_nicho():
    job = JobInput(source_type="tema", source="x", niche="cinema")
    job_id = db.create_job(job.model_dump(), "filme")
    db.update_job(job_id, status="done", result_json=json.dumps({
        "title": "filme", "script": {"segments": [{"kind": "hook", "text": "gancho de cinema"}]}}))
    acc = db.create_account("youtube", "Canal", {"token": "x"})
    sched = db.create_schedule(job_id, acc, "youtube", "2026-01-01T00:00:00+00:00", {})
    db.upsert_metrics(sched, job_id, "youtube", "v1", "", {"views": 9999, "avg_view_pct": 70.0})

    assert "gancho de cinema" in metrics.insights("cinema")
    assert "gancho de cinema" not in metrics.insights("ciberseguranca")


def test_pick_clips_descarta_sobreposto_e_curto(monkeypatch):
    monkeypatch.setattr(clipper.llm, "complete_json", lambda *a, **k: {"clipes": [
        {"inicio": 10, "fim": 55, "titulo": "Bom", "motivo": "tem virada", "assunto": "a"},
        {"inicio": 30, "fim": 70, "titulo": "Sobreposto", "motivo": "", "assunto": "b"},
        {"inicio": 100, "fim": 103, "titulo": "Curto", "motivo": "", "assunto": "c"},
        {"inicio": 200, "fim": 245, "titulo": "Outro bom", "motivo": "dado forte", "assunto": "d"},
    ]})
    clips = clipper.pick_clips("[00:10] fala\n[03:20] outra fala", 600.0, 5, 45)
    titulos = [c["titulo"] for c in clips]
    assert titulos == ["Bom", "Outro bom"]


def test_pick_clips_respeita_a_quantidade_pedida(monkeypatch):
    monkeypatch.setattr(clipper.llm, "complete_json", lambda *a, **k: {"clipes": [
        {"inicio": i * 100, "fim": i * 100 + 45, "titulo": f"c{i}", "motivo": "", "assunto": ""}
        for i in range(6)
    ]})
    assert len(clipper.pick_clips("transcrição", 1000.0, 2, 45)) == 2


def test_pick_clips_sem_transcricao_orienta_o_usuario():
    import pytest

    with pytest.raises(RuntimeError, match="whisper"):
        clipper.pick_clips("   ", 600.0, 3, 45)


def test_transcript_with_timestamps_formata_mm_ss():
    linhas = clipper.transcript_with_timestamps([
        {"start": 5, "text": " começo "}, {"start": 125, "text": "depois"}])
    assert linhas.splitlines() == ["[00:05] começo", "[02:05] depois"]


# ---------------------------------------------------------------- idioma

def test_language_name_traduz_as_tags_conhecidas():
    assert script_mod.language_name("pt-BR") == "português do Brasil"
    assert "English" in script_mod.language_name("en-US")
    assert "español" in script_mod.language_name("es")
    assert "русский" in script_mod.language_name("ru-RU")
    assert "简体中文" in script_mod.language_name("zh-CN")


def test_language_name_tag_desconhecida_volta_como_esta():
    """Ainda é instrução útil para o modelo — melhor que cair no português."""
    assert script_mod.language_name("sw-KE") == "sw-KE"


def test_language_name_vazio_cai_no_portugues():
    assert script_mod.language_name("") == "português do Brasil"
    assert script_mod.language_name(None) == "português do Brasil"  # type: ignore[arg-type]


def test_prompt_do_roteiro_pede_o_idioma_do_job(monkeypatch):
    """Antes o prompt fixava 'português do Brasil' e ignorava job.language:
    a interface em espanhol continuava produzindo narração em português."""
    capturado = {}

    def fake(system, prompt, schema=None, max_tokens=8000, purpose=""):
        capturado["system"] = system
        return {"title": "t", "description": "d", "hashtags": [],
                "estimated_seconds": 20,
                "segments": [{"kind": "hook", "text": "hola", "broll_query": "x",
                              "on_screen": "y"}]}

    monkeypatch.setattr(script_mod.llm, "complete_json", fake)
    job = JobInput(source_type="tema", source="tema", language="es-ES")
    script_mod.build_script(job, SourceMaterial(kind="tema", title="t", text="t"))

    assert "español" in capturado["system"]
    assert "em português do Brasil" not in capturado["system"]


def test_prompts_de_hooks_legenda_e_refino_tambem_seguem_o_idioma(monkeypatch):
    vistos: list[str] = []

    def fake(system, prompt, schema=None, max_tokens=8000, purpose=""):
        vistos.append(system)
        if purpose == "hooks":
            return {"hooks": [{"text": "a", "mechanism": "m", "why": "w"}]}
        if purpose == "legenda_post":
            return {"youtube_titulo": "t", "youtube_descricao": "d",
                    "tiktok_legenda": "l", "instagram_legenda": "i", "hashtags": []}
        return {"title": "t", "description": "d", "hashtags": [],
                "estimated_seconds": 20,
                "segments": [{"kind": "hook", "text": "x", "broll_query": "",
                              "on_screen": ""}]}

    monkeypatch.setattr(script_mod.llm, "complete_json", fake)
    job = JobInput(source_type="tema", source="x", language="ru-RU")

    script_mod.build_hook_variants(SCRIPT, job, count=1)
    script_mod.build_post_caption(SCRIPT, job)
    script_mod.refine_script(SCRIPT, "encurta", job)

    assert len(vistos) == 3
    assert all("русский" in system for system in vistos)
