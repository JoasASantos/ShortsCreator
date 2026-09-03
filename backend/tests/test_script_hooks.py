"""Script: splitting pasted text, swapping the hook, insights and clips."""
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


def _fail_if_llm_called():
    raise AssertionError("source_type='roteiro' should not call the LLM")


def test_a_pasted_script_does_not_call_the_llm(monkeypatch):
    monkeypatch.setattr(script_mod.llm, "complete_json",
                        lambda *a, **k: _fail_if_llm_called())
    material = SourceMaterial(kind="roteiro", title="Meu roteiro",
                              text="Primeira frase. Segunda frase. Terceira frase.")
    result = script_mod.build_script(JOB, material)
    assert result.segments[0].kind == "hook"
    assert result.segments[-1].kind == "cta"
    assert "Primeira frase" in script_mod.full_narration(result)


def test_a_pasted_script_appends_the_cta_when_it_is_missing():
    material = SourceMaterial(kind="roteiro", text="Só uma frase aqui.")
    result = script_mod.build_script(JOB, material)
    assert result.segments[-1].text == "Segue pra mais."


def test_with_hook_replaces_only_the_first_segment():
    updated = script_mod.with_hook(SCRIPT, "  Gancho novo e agressivo  ")
    assert updated.segments[0].text == "Gancho novo e agressivo"
    assert updated.segments[0].kind == "hook"
    assert [s.text for s in updated.segments[1:]] == [s.text for s in SCRIPT.segments[1:]]
    # the original script is not mutated
    assert SCRIPT.segments[0].text == "Gancho original"


def test_with_hook_marks_the_first_segment_of_a_script_that_has_no_hook():
    without_hook = SCRIPT.model_copy(update={"segments": [
        s.model_copy(update={"kind": "corpo"}) for s in SCRIPT.segments]})
    updated = script_mod.with_hook(without_hook, "Agora tem gancho")
    assert updated.segments[0].kind == "hook"
    assert updated.segments[0].text == "Agora tem gancho"


def test_build_hook_variants_uses_the_body_and_asks_for_mechanisms(monkeypatch):
    captured = {}

    def fake(system, prompt, schema=None, max_tokens=8000, purpose=""):
        captured["prompt"] = prompt
        captured["purpose"] = purpose
        return {"hooks": [
            {"text": "Você está fazendo isso errado", "mechanism": "afirmação polêmica", "why": "provoca"},
            {"text": "70% das empresas caem nisso", "mechanism": "número", "why": "concreto"},
        ]}

    monkeypatch.setattr(script_mod.llm, "complete_json", fake)
    hooks = script_mod.build_hook_variants(SCRIPT, JOB, count=2)
    assert len(hooks) == 2
    assert captured["purpose"] == "hooks"
    assert "Gancho original" in captured["prompt"]
    assert "Corpo do roteiro" in captured["prompt"]


def test_build_hook_variants_fails_clearly_when_the_model_answers_nothing(monkeypatch):
    import pytest

    monkeypatch.setattr(script_mod.llm, "complete_json", lambda *a, **k: {"hooks": []})
    with pytest.raises(RuntimeError, match="hooks"):
        script_mod.build_hook_variants(SCRIPT, JOB)


def test_segment_durations_cover_the_whole_timeline():
    words = [{"word": f"p{i}", "start": i * 0.4, "end": i * 0.4 + 0.35} for i in range(12)]
    script = ShortScript(title="t", description="", segments=[
        {"kind": "hook", "text": "a b c d"},
        {"kind": "corpo", "text": "e f g h"},
        {"kind": "cta", "text": "i j k l"},
    ])
    total = 6.0
    durations = script_mod.segment_durations_covering(script, words, total)
    assert len(durations) == 3
    # the sum covers the entire video: without it the background ends before the audio
    assert abs(sum(durations) - total) < 0.01


def test_insights_is_empty_without_metrics():
    assert metrics.insights("tecnologia") == ""


def test_insights_lists_hooks_ordered_by_retention():
    def create(name: str, views: int, retention: float | None) -> None:
        job = JobInput(source_type="tema", source=name, niche="tecnologia")
        job_id = db.create_job(job.model_dump(), name)
        db.update_job(job_id, status="done", result_json=json.dumps({
            "title": name, "script": {"segments": [
                {"kind": "hook", "text": f"gancho de {name}"}]}}))
        acc = db.create_account("youtube", "Canal", {"token": "x"})
        sched = db.create_schedule(job_id, acc, "youtube", "2026-01-01T00:00:00+00:00", {})
        db.upsert_metrics(sched, job_id, "youtube", f"v-{name}", "",
                          {"views": views, "avg_view_pct": retention})

    create("fraco", 50000, 20.0)
    create("forte", 1000, 85.0)

    briefing = metrics.insights("tecnologia")
    assert "gancho de forte" in briefing
    # retention wins: the 85% one shows up before the one with 50k views
    assert briefing.index("gancho de forte") < briefing.index("gancho de fraco")
    assert "85%" in briefing


def test_insights_filters_by_niche():
    job = JobInput(source_type="tema", source="x", niche="cinema")
    job_id = db.create_job(job.model_dump(), "filme")
    db.update_job(job_id, status="done", result_json=json.dumps({
        "title": "filme", "script": {"segments": [{"kind": "hook", "text": "gancho de cinema"}]}}))
    acc = db.create_account("youtube", "Canal", {"token": "x"})
    sched = db.create_schedule(job_id, acc, "youtube", "2026-01-01T00:00:00+00:00", {})
    db.upsert_metrics(sched, job_id, "youtube", "v1", "", {"views": 9999, "avg_view_pct": 70.0})

    assert "gancho de cinema" in metrics.insights("cinema")
    assert "gancho de cinema" not in metrics.insights("ciberseguranca")


def test_pick_clips_discards_overlapping_and_too_short_clips(monkeypatch):
    monkeypatch.setattr(clipper.llm, "complete_json", lambda *a, **k: {"clipes": [
        {"inicio": 10, "fim": 55, "titulo": "Bom", "motivo": "tem virada", "assunto": "a"},
        {"inicio": 30, "fim": 70, "titulo": "Sobreposto", "motivo": "", "assunto": "b"},
        {"inicio": 100, "fim": 103, "titulo": "Curto", "motivo": "", "assunto": "c"},
        {"inicio": 200, "fim": 245, "titulo": "Outro bom", "motivo": "dado forte", "assunto": "d"},
    ]})
    clips = clipper.pick_clips("[00:10] fala\n[03:20] outra fala", 600.0, 5, 45)
    titles = [c["titulo"] for c in clips]
    assert titles == ["Bom", "Outro bom"]


def test_pick_clips_respects_the_requested_count(monkeypatch):
    monkeypatch.setattr(clipper.llm, "complete_json", lambda *a, **k: {"clipes": [
        {"inicio": i * 100, "fim": i * 100 + 45, "titulo": f"c{i}", "motivo": "", "assunto": ""}
        for i in range(6)
    ]})
    assert len(clipper.pick_clips("transcrição", 1000.0, 2, 45)) == 2


def test_pick_clips_without_a_transcript_guides_the_user():
    import pytest

    with pytest.raises(RuntimeError, match="whisper"):
        clipper.pick_clips("   ", 600.0, 3, 45)


def test_transcript_with_timestamps_formats_as_mm_ss():
    lines = clipper.transcript_with_timestamps([
        {"start": 5, "text": " começo "}, {"start": 125, "text": "depois"}])
    assert lines.splitlines() == ["[00:05] começo", "[02:05] depois"]


# ---------------------------------------------------------------- language

def test_language_name_translates_the_known_tags():
    assert script_mod.language_name("pt-BR") == "português do Brasil"
    assert "English" in script_mod.language_name("en-US")
    assert "español" in script_mod.language_name("es")
    assert "русский" in script_mod.language_name("ru-RU")
    assert "简体中文" in script_mod.language_name("zh-CN")


def test_language_name_returns_an_unknown_tag_unchanged():
    """It is still a useful instruction for the model — better than falling
    back to Portuguese."""
    assert script_mod.language_name("sw-KE") == "sw-KE"


def test_language_name_falls_back_to_portuguese_when_empty():
    assert script_mod.language_name("") == "português do Brasil"
    assert script_mod.language_name(None) == "português do Brasil"  # type: ignore[arg-type]


def test_the_script_prompt_asks_for_the_language_of_the_job(monkeypatch):
    """The prompt used to hardcode 'português do Brasil' and ignore
    job.language: a Spanish UI kept producing Portuguese narration."""
    captured = {}

    def fake(system, prompt, schema=None, max_tokens=8000, purpose=""):
        captured["system"] = system
        return {"title": "t", "description": "d", "hashtags": [],
                "estimated_seconds": 20,
                "segments": [{"kind": "hook", "text": "hola", "broll_query": "x",
                              "on_screen": "y"}]}

    monkeypatch.setattr(script_mod.llm, "complete_json", fake)
    job = JobInput(source_type="tema", source="tema", language="es-ES")
    script_mod.build_script(job, SourceMaterial(kind="tema", title="t", text="t"))

    assert "español" in captured["system"]
    assert "em português do Brasil" not in captured["system"]


def test_the_hook_caption_and_refine_prompts_also_follow_the_language(monkeypatch):
    seen: list[str] = []

    def fake(system, prompt, schema=None, max_tokens=8000, purpose=""):
        seen.append(system)
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

    assert len(seen) == 3
    assert all("русский" in system for system in seen)
