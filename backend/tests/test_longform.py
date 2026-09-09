"""Long-form productions: the stages and their prompts, the timestamp guarantee,
the edit plan validated in code, an assembly that survives a missing stock
clip, the resume, the cost estimate and the routes."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, worker
from app.config import settings
from app.main import app
from app.pipeline import formats, longform, script as script_mod
from app.schemas import QAReport

from conftest import needs_ffmpeg

client = TestClient(app)

PROMPT = "Os principais golpes digitais no Brasil em 2026 e quem está por trás deles."
INSTRUCTION = "Tom investigativo, sem sensacionalismo."


# Captured before any fixture patches it: the assembly fixture replaces
# `_translate_cues` with a pass-through so no test spends an LLM request, and
# the tests about translation put the real one back.
_REAL_TRANSLATE = longform._translate_cues


def no_cutaways(monkeypatch):
    """No unused window in the material, so the placeholder card is the floor.

    The cover-with-your-own-footage fallback sits between stock and the card,
    and a test about the card has to reach it: the fixture's material has
    plenty of unused minutes, so without this the shot is covered instead."""
    monkeypatch.setattr(longform._Cutaways, "take", lambda self, seconds: None)


@pytest.fixture(autouse=True)
def no_image_generator(monkeypatch):
    """What happens to a stock shot with no stock bank depends on whether an
    image generator is keyed, so a developer's own OPENAI_API_KEY must not
    decide these. Tests about the drawn fallback opt in explicitly."""
    monkeypatch.setattr(longform.imagegen, "providers_ready", lambda *a, **k: False)


def _segments(total: float, every: float = 6.0, text: str = "fala") -> list[dict]:
    out, t, n = [], 0.0, 0
    while t < total - 1:
        out.append({"start": round(t, 2), "end": round(min(t + every, total - 1), 2),
                    "text": f"{text} {n} sobre golpes e vítimas."})
        t += every
        n += 1
    return out


MATERIAL = {"items": [
    {"id": "m01", "kind": "entrevista", "reference": False, "title": "Ana Souza — delegada",
     "url": "", "file": "m01.mp4", "words_file": "", "duration": 120.0,
     "segments": _segments(120.0), "transcript": "...", "text": "",
     "description": "Delegada da divisão de crimes cibernéticos", "status": "ok",
     "error": ""},
    {"id": "m02", "kind": "link", "reference": False, "title": "Reportagem sobre o golpe do PIX",
     "url": "https://youtube.com/watch?v=x", "file": "m02/source.mp4", "words_file": "",
     "duration": 90.0, "segments": _segments(90.0, text="repórter"), "transcript": "...",
     "text": "", "description": "", "status": "ok", "error": ""},
    {"id": "m03", "kind": "artigo", "reference": False, "title": "Febraban: golpes 2026",
     "url": "https://exemplo.com/golpes", "file": "", "words_file": "", "duration": 0.0,
     "segments": [], "transcript": "", "text": "Os golpes cresceram 40% em 2026. " * 20,
     "description": "", "status": "ok", "error": ""},
    {"id": "m04", "kind": "imagem", "reference": False, "title": "grafico-golpes",
     "url": "", "file": "m04.png", "words_file": "", "duration": 0.0, "segments": [],
     "transcript": "", "text": "", "description": "", "status": "ok", "error": ""},
    {"id": "m05", "kind": "link", "reference": True, "title": "Documentário de referência",
     "url": "https://youtube.com/watch?v=ref", "file": "m05/source.mp4", "words_file": "",
     "duration": 600.0, "segments": _segments(600.0, 30.0, "referência"), "transcript": "...",
     "text": "", "description": "", "status": "ok", "error": ""},
    {"id": "m06", "kind": "link", "reference": False, "title": "", "url": "https://youtube.com/x",
     "file": "", "words_file": "", "duration": 0.0, "segments": [], "transcript": "",
     "text": "", "description": "", "status": "failed", "error": "yt-dlp failed: 403"},
], "ingested_at": "2026-09-06T00:00:00+00:00"}

RAW_BRIEFING = {
    "title": "A Fábrica de Golpes",
    "logline": "Como o golpe do PIX virou indústria no Brasil de 2026.",
    "angle": "seguir o dinheiro",
    "facts": [
        {"fact": "Os golpes cresceram 40% em 2026.", "source": "m03", "why": "dado da Febraban"},
        {"fact": "A engenharia social é a porta de entrada.", "source": "m01", "why": "a delegada"},
        {"fact": "Fato de fonte inexistente.", "source": "m77", "why": "?"},
        {"fact": "Fato tirado da referência.", "source": "m05", "why": "copiado"},
    ],
    "themes": ["engenharia social", "lavagem via PIX"],
    "moments": [
        {"material": "m01", "start": 10, "end": 30, "quote": "fala 2 sobre golpes",
         "why": "a delegada explica o esquema"},
        # past the end of a two-minute interview: a made-up timestamp
        {"material": "m01", "start": 500, "end": 520, "quote": "inventada", "why": "x"},
        # a reference example is never content
        {"material": "m05", "start": 30, "end": 60, "quote": "da referência", "why": "x"},
        # a material that does not exist
        {"material": "m99", "start": 0, "end": 10, "quote": "?", "why": "x"},
        {"material": "m02", "start": 12, "end": 200, "quote": "repórter 2", "why": "o repórter"},
    ],
    "gaps": ["imagens de agências bancárias", "telas de celular com PIX"],
    "stock_queries": ["bank branch exterior", "hands typing on phone", "Bank Branch Exterior"],
    "structure": [{"part": "Abertura", "purpose": "gancho", "minutes": 1},
                  {"part": "Ato 1", "purpose": "o esquema", "minutes": 8}],
    "episodes": [],
}

# Exactly 30 words, counted the way the pipeline counts them (`str.split()`),
# so the budget arithmetic below stays legible: ceil(30 / 1.6) = 19 seconds.
NARRATION_30_WORDS = ("Em dois mil e vinte e seis o golpe do PIX abandonou o improviso "
                      "e virou uma linha de produção com gerentes metas e turnos de "
                      "trabalho como qualquer empresa.")

RAW_SCRIPT = {"episodes": [{"episode": 1, "title": "A Fábrica de Golpes", "blocks": [
    {"kind": "abertura", "title": "Gancho", "narration": NARRATION_30_WORDS,
     "seconds": 10, "visual": "cidade à noite, telas de celular", "moment": None},
    {"kind": "entrevista", "title": "A delegada", "narration": "",
     "seconds": 20, "visual": "Ana em quadro",
     "moment": {"material": "m01", "start": 10, "end": 30}},
    {"kind": "ato", "title": "O esquema", "narration": "O dinheiro sai em segundos. "
     "E some em minutos.", "seconds": 24, "visual": "agência, telas",
     "moment": {"material": "m01", "start": 500, "end": 520}},
    {"kind": "conclusao", "title": "Fecho", "narration": "Ninguém está imune.",
     "seconds": 8, "visual": "cartela final", "moment": None},
]}]}

RAW_PLAN = {"episodes": [{"episode": 1, "blocks": [
    {"key": "e01_b01", "shots": [
        {"kind": "stock", "material": "", "start": 0, "end": 0,
         "query": "city skyline at night", "text": "", "seconds": 6, "lower_third": ""},
        {"kind": "cartela", "material": "", "start": 0, "end": 0, "query": "",
         "text": "A Fábrica de Golpes", "seconds": 4, "lower_third": ""},
        # an avatar shot in voice-over mode: refused
        {"kind": "avatar", "material": "", "start": 0, "end": 0, "query": "", "text": "",
         "seconds": 5, "lower_third": ""},
    ]},
    {"key": "e01_b02", "shots": [
        {"kind": "entrevista", "material": "m01", "start": 10, "end": 30, "query": "",
         "text": "", "seconds": 0, "lower_third": "Ana Souza, delegada"},
    ]},
    {"key": "e01_b03", "shots": [
        {"kind": "stock", "material": "", "start": 0, "end": 0,
         "query": "hands typing on phone", "text": "", "seconds": 8, "lower_third": ""},
        # end past the material's 90s: clamped, not refused
        {"kind": "link", "material": "m02", "start": 80, "end": 200, "query": "",
         "text": "", "seconds": 0, "lower_third": ""},
        # a material that does not exist: refused
        {"kind": "entrevista", "material": "m99", "start": 0, "end": 10, "query": "",
         "text": "", "seconds": 0, "lower_third": ""},
        # a reference example on screen: refused
        {"kind": "link", "material": "m05", "start": 0, "end": 10, "query": "",
         "text": "", "seconds": 0, "lower_third": ""},
        {"kind": "imagem", "material": "m04", "start": 0, "end": 0, "query": "",
         "text": "", "seconds": 0, "lower_third": ""},
    ]},
    # e01_b04 is missing on purpose: a title card stands in
]}]}

BY_PURPOSE = {
    "longform_briefing": RAW_BRIEFING,
    "longform_roteiro": RAW_SCRIPT,
    "longform_plano": RAW_PLAN,
    "clipes": {"clipes": [{"inicio": 600, "fim": 650, "titulo": "O esquema",
                           "motivo": "", "assunto": ""},
                          {"inicio": 2400, "fim": 2450, "titulo": "A vítima",
                           "motivo": "", "assunto": ""}]},
}


class _FakeLLM(list):
    """The captured calls, plus the canned answers a test can rewrite.

    A plain list cannot carry the `answers` attribute — it has no `__dict__` —
    so the two travel together in one object that still behaves as the list of
    calls the assertions iterate over.
    """
    answers: dict


@pytest.fixture
def fake_llm(monkeypatch):
    """Every stage answered from a canned document. Never a real LLM in a test,
    and the calls are captured so the prompts can be inspected."""
    calls = _FakeLLM()
    calls.answers = {k: v for k, v in BY_PURPOSE.items()}

    def fake(system, prompt, schema=None, max_tokens=8000, purpose=""):
        calls.append({"system": system, "prompt": prompt, "schema": schema,
                      "purpose": purpose})
        return json.loads(json.dumps(calls.answers[purpose]))

    monkeypatch.setattr(longform.llm, "complete_json", fake)
    return calls


def _options(**overrides) -> dict:
    kind = overrides.pop("type", "documentario")
    return longform.options_of({"type": kind, "options_json": json.dumps(overrides)})


def _row(kind: str = "documentario", **options) -> dict:
    return {"id": "doc_x", "type": kind, "prompt": PROMPT, "instruction": INSTRUCTION,
            "options_json": json.dumps(options)}


def _project(stages=("material", "briefing", "roteiro", "plano_de_edicao"),
             kind: str = "documentario", material: dict | None = None,
             script: dict | None = None, plan: dict | None = None, **options) -> dict:
    """A production row with the requested stages already developed, and the
    material files present on disk (empty stand-ins) so shots can find them."""
    project_id = db.create_longform(kind, PROMPT, INSTRUCTION, "", options)
    material = material or MATERIAL
    work = longform.project_dir(project_id)
    for item in material["items"]:
        if item.get("file"):
            path = work / item["file"]
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(b"fake-material")
    row = db.get_longform(project_id)
    opts = longform.options_of(row)
    docs = {}
    for stage in stages:
        row = db.get_longform(project_id)
        if stage == "material":
            doc = material
        elif stage == "briefing":
            doc = longform._normalize_briefing(RAW_BRIEFING, material, opts)  # noqa: SLF001
        elif stage == "roteiro":
            doc = longform._normalize_script(script or RAW_SCRIPT, material, opts)  # noqa: SLF001
        else:
            doc = longform._normalize_plan(plan or RAW_PLAN, material,               # noqa: SLF001
                                           docs["roteiro"], opts)
        docs[stage] = doc
        longform.save_stage(row, stage, doc)
    return db.get_longform(project_id)


# ------------------------------- the prompts -------------------------------

@pytest.mark.parametrize("kind", list(longform.TYPES))
def test_each_types_structure_reaches_the_briefing_prompt(fake_llm, kind):
    options = _options(type=kind, episodes=3, target_minutes=longform.TYPES[kind]["default_minutes"])
    longform.develop_briefing(_row(kind), MATERIAL, INSTRUCTION, options)

    call = fake_llm[0]
    assert call["purpose"] == "longform_briefing"
    assert longform.TYPES[kind]["structure"] in call["prompt"]
    assert longform.TYPES[kind]["label"] in call["prompt"]
    assert PROMPT in call["prompt"] and INSTRUCTION in call["prompt"]
    assert longform.TONE_PRESETS["investigativo"] in call["prompt"]
    if kind == "mini_serie":
        assert "3 episódios" in call["prompt"]


def test_the_reference_rule_is_in_the_prompt_and_on_the_item(fake_llm):
    """A reference example is structure and rhythm only — the prompt says so
    in the rules and again on the item itself, and the language marker is
    replaced, never formatted (the prompt carries literal JSON)."""
    longform.develop_briefing(_row(), MATERIAL, "", _options(language="es-ES"))
    call = fake_llm[0]
    assert "REFERÊNCIA" in call["system"] and "proibido" in call["system"]
    assert "[m05] LINK [REFERÊNCIA — só estrutura, nunca conteúdo]" in call["prompt"]
    assert "español" in call["system"] and "{language}" not in call["system"]
    # a failed item is not offered to the model at all
    assert "[m06]" not in call["prompt"]
    # transcripts arrive stamped, which is what makes a moment verifiable
    assert "[00:06] fala 1" in call["prompt"]


def test_the_briefing_rejects_a_moment_outside_the_transcript(fake_llm):
    briefing = longform.develop_briefing(_row(), MATERIAL, "", _options())

    kept = [(m["material"], m["start"], m["end"]) for m in briefing["moments"]]
    assert kept == [("m01", 10.0, 30.0), ("m02", 12.0, 90.0)], \
        "500s into a 120s interview, a reference, and m99 are all refused; " \
        "an end past the material is clamped to it"
    reasons = " | ".join(r["reason"] for r in briefing["rejected"])
    assert "past the end" in reasons
    assert "reference example" in reasons
    assert "does not exist" in reasons
    # a fact from a source that does not exist keeps the fact, loses the source;
    # a fact from a reference is dropped
    facts = {f["fact"]: f["source"] for f in briefing["facts"]}
    assert facts["Fato de fonte inexistente."] == ""
    assert "Fato tirado da referência." not in facts
    # duplicate stock queries collapse case-insensitively
    assert briefing["stock_queries"] == ["bank branch exterior", "hands typing on phone"]
    assert briefing["episodes"] == []


def test_a_long_interview_is_condensed_by_the_windowed_clipper_not_truncated(fake_llm):
    """Forty minutes of transcript does not fit the prompt. Truncating it would
    make every quote come from the first ten minutes; the windowed clipper
    picks stretches across the whole thing."""
    long_item = dict(MATERIAL["items"][0], id="m01", duration=3000.0,
                     segments=_segments(3000.0, 4.0, "uma frase bem mais comprida da entrevista"))
    material = {"items": [long_item]}
    longform.develop_briefing(_row(), material, "", _options())

    purposes = [c["purpose"] for c in fake_llm]
    assert "clipes" in purposes, "the long transcript went through pick_clips"
    prompt = [c for c in fake_llm if c["purpose"] == "longform_briefing"][0]["prompt"]
    assert "trechos selecionados" in prompt
    assert "[40:00]" in prompt, "a stretch from deep in the interview is shown"


def test_a_briefing_with_no_logline_fails_with_a_clear_reason(monkeypatch):
    monkeypatch.setattr(longform.llm, "complete_json",
                        lambda *a, **k: {"title": "x", "moments": []})
    with pytest.raises(RuntimeError, match="logline"):
        longform.develop_briefing(_row(), MATERIAL, "", _options())


def test_the_script_is_written_to_the_documentary_word_budget(fake_llm):
    """Documentary narration breathes: the budget is below the short's rate,
    the prompt says the number, and a block with more words than its seconds
    can hold is lengthened rather than read without breathing."""
    assert longform.NARRATION_WORDS_PER_SECOND < script_mod.WORDS_PER_SECOND
    options = _options()
    briefing = longform._normalize_briefing(RAW_BRIEFING, MATERIAL, options)  # noqa: SLF001
    doc = longform.write_script(_row(), MATERIAL, briefing, "", options)

    call = fake_llm[0]
    assert call["purpose"] == "longform_roteiro"
    assert f"{longform.NARRATION_WORDS_PER_SECOND:.1f} palavras" in call["system"]
    assert str(int(30 * longform.NARRATION_WORDS_PER_SECOND)) in call["system"]
    assert longform.NARRATOR_DESCRIPTION["oculto"] in call["system"]
    assert "[m01] ENTREVISTA" in call["prompt"]

    blocks = doc["episodes"][0]["blocks"]
    assert [b["key"] for b in blocks] == ["e01_b01", "e01_b02", "e01_b03", "e01_b04"]
    opening = blocks[0]
    assert opening["words"] == 30
    assert opening["adjusted"] is True
    assert opening["seconds"] == opening["budget_seconds"] == 19   # ceil(30 / 1.6)
    assert blocks[2]["adjusted"] is False and blocks[2]["seconds"] == 24


def test_the_script_drops_a_moment_at_a_second_nobody_speaks(fake_llm):
    options = _options()
    doc = longform._normalize_script(RAW_SCRIPT, MATERIAL, options)  # noqa: SLF001
    blocks = doc["episodes"][0]["blocks"]
    assert blocks[1]["moment"] == {"material": "m01", "start": 10.0, "end": 30.0}
    assert blocks[2]["moment"] is None, "500s into a 120s interview"
    assert any("past the end" in r["reason"] for r in doc["rejected"])


def test_sem_narracao_strips_every_narration():
    doc = longform._normalize_script(RAW_SCRIPT, MATERIAL,  # noqa: SLF001
                                     _options(narrator="sem_narracao"))
    assert all(b["narration"] == "" for b in longform.script_blocks(doc))


def test_a_series_gets_the_recap_and_the_cliffhanger_it_is_missing():
    """The recap and the hook are the series' structure. A missing one is
    inserted empty where the user can see and fill it."""
    raw = {"episodes": [
        {"episode": 1, "title": "Ep 1", "blocks": [
            {"kind": "abertura", "title": "a", "narration": "Começa aqui.", "seconds": 10,
             "visual": "x", "moment": None}]},
        {"episode": 2, "title": "Ep 2", "blocks": [
            {"kind": "ato", "title": "b", "narration": "Continua.", "seconds": 10,
             "visual": "x", "moment": None}]},
    ]}
    doc = longform._normalize_script(raw, MATERIAL,  # noqa: SLF001
                                     _options(type="mini_serie", episodes=2))
    first, second = doc["episodes"]
    assert first["blocks"][-1]["kind"] == "gancho"
    assert second["blocks"][0]["kind"] == "recap"
    assert first["blocks"][0]["kind"] != "recap", "the first episode has nothing to recap"
    assert second["blocks"][-1]["kind"] != "gancho", "the last episode has no next one"
    assert [b["key"] for b in second["blocks"]] == ["e02_b01", "e02_b02"]
    assert len(doc["notes"]) == 2


def test_a_documentary_that_came_back_in_two_episodes_is_one_film():
    raw = {"episodes": [
        {"episode": 1, "title": "x", "blocks": RAW_SCRIPT["episodes"][0]["blocks"][:2]},
        {"episode": 2, "title": "y", "blocks": RAW_SCRIPT["episodes"][0]["blocks"][2:]},
    ]}
    doc = longform._normalize_script(raw, MATERIAL, _options())  # noqa: SLF001
    assert len(doc["episodes"]) == 1
    assert len(doc["episodes"][0]["blocks"]) == 4


def test_a_script_with_no_blocks_fails(monkeypatch):
    with pytest.raises(RuntimeError, match="blocks"):
        longform._normalize_script({"episodes": [{"episode": 1, "blocks": []}]},  # noqa: SLF001
                                   MATERIAL, _options())


# ---------------------- the edit plan, validated in code ----------------------

def test_the_plan_refuses_what_does_not_exist_and_clamps_what_runs_past_the_end(fake_llm):
    options = _options()
    script = longform._normalize_script(RAW_SCRIPT, MATERIAL, options)  # noqa: SLF001
    logged: list[str] = []
    plan = longform._normalize_plan(RAW_PLAN, MATERIAL, script, options,  # noqa: SLF001
                                    log=logged.append)
    blocks = {b["key"]: b for b in longform.plan_blocks(plan)}

    # block 1: the avatar shot is refused in voice-over mode
    assert [s["kind"] for s in blocks["e01_b01"]["shots"]] == ["stock", "cartela"]
    # block 3: m99 and the reference are gone, the link is clamped to 90s
    kinds = [(s["kind"], s["material"]) for s in blocks["e01_b03"]["shots"]]
    assert kinds == [("stock", ""), ("link", "m02"), ("imagem", "m04")]
    link = blocks["e01_b03"]["shots"][1]
    assert (link["start"], link["end"], link["seconds"]) == (80.0, 90.0, 10.0)
    # block 4 was missing from the plan: a card stands in
    assert [s["kind"] for s in blocks["e01_b04"]["shots"]] == ["cartela"]
    assert blocks["e01_b04"]["shots"][0]["text"] == "Fecho"

    reasons = [r["reason"] for r in plan["rejected"]]
    assert any("m99" in r and "does not exist" in r for r in reasons)
    assert any("m05" in r and "reference" in r for r in reasons)
    assert any("avatar shot" in r for r in reasons)
    assert any("no entry" in r for r in reasons)
    # every refusal was also logged — nothing is dropped silently
    assert len(logged) == len(plan["rejected"])
    assert all("dropped" in line for line in logged)


def test_a_block_carried_by_a_moment_gets_its_interview_shot_even_if_the_plan_forgot():
    options = _options()
    script = longform._normalize_script(RAW_SCRIPT, MATERIAL, options)  # noqa: SLF001
    raw = {"episodes": [{"episode": 1, "blocks": [
        {"key": "e01_b02", "shots": [{"kind": "stock", "material": "", "start": 0, "end": 0,
                                      "query": "police station", "text": "", "seconds": 5,
                                      "lower_third": ""}]}]}]}
    plan = longform._normalize_plan(raw, MATERIAL, script, options)  # noqa: SLF001
    block = [b for b in longform.plan_blocks(plan) if b["key"] == "e01_b02"][0]
    assert block["shots"][0]["kind"] == "entrevista"
    assert (block["shots"][0]["start"], block["shots"][0]["end"]) == (10.0, 30.0)
    assert block["shots"][0]["lower_third"] == "Ana Souza — delegada"


def test_shots_left_without_a_length_share_what_the_block_has_left():
    shots = [longform._shot("entrevista", material="m01", start=0, end=10, seconds=10),  # noqa: SLF001
             longform._shot("stock", query="a"), longform._shot("cartela", text="b")]  # noqa: SLF001
    longform._fill_seconds(shots, 30.0)  # noqa: SLF001
    assert [s["seconds"] for s in shots] == [10.0, 10.0, 10.0]


def test_the_plan_is_asked_once_more_when_references_were_refused(fake_llm):
    """A model told exactly which second does not exist usually fixes it — so
    one retry with the reasons, never a loop."""
    options = _options()
    briefing = longform._normalize_briefing(RAW_BRIEFING, MATERIAL, options)  # noqa: SLF001
    script = longform._normalize_script(RAW_SCRIPT, MATERIAL, options)  # noqa: SLF001
    longform.plan_edit(_row(), MATERIAL, briefing, script, "", options)

    plan_calls = [c for c in fake_llm if c["purpose"] == "longform_plano"]
    assert len(plan_calls) == 2
    assert "CORREÇÕES OBRIGATÓRIAS" in plan_calls[1]["prompt"]
    assert "m99" in plan_calls[1]["prompt"]
    # the catalog shows the transcript around the chosen moments, stamped
    assert "[00:12] fala 2" in plan_calls[0]["prompt"]
    assert "REFERÊNCIA" in plan_calls[0]["system"]

    clean = {"episodes": [{"episode": 1, "blocks": [
        {"key": b["key"], "shots": [{"kind": "cartela", "material": "", "start": 0, "end": 0,
                                     "query": "", "text": b["title"], "seconds": 5,
                                     "lower_third": ""}]}
        for b in longform.script_blocks(script)]}]}
    fake_llm.answers["longform_plano"] = clean
    fake_llm.clear()
    longform.plan_edit(_row(), MATERIAL, briefing, script, "", options)
    assert len([c for c in fake_llm if c["purpose"] == "longform_plano"]) == 1


def test_a_plan_that_covers_no_block_fails():
    with pytest.raises(RuntimeError, match="no block"):
        longform._normalize_plan({"episodes": []}, MATERIAL, {"episodes": []},  # noqa: SLF001
                                 _options())


# ------------------------- stage order and cascade -------------------------

def test_a_stage_refuses_to_run_before_the_stage_it_is_built_on():
    project = _project(stages=("material",))
    with pytest.raises(longform.StageNotReady, match="briefing"):
        longform.develop_stage(project, "roteiro")

    empty = db.get_longform(db.create_longform("documentario", PROMPT, "", "", {}))
    with pytest.raises(longform.StageNotReady, match="material"):
        longform.develop_stage(empty, "briefing")
    with pytest.raises(ValueError, match="ingested"):
        longform.develop_stage(project, "material")
    with pytest.raises(ValueError):
        longform.develop_stage(project, "trailer")


def test_rewriting_a_stage_drops_the_stages_derived_from_it():
    project = _project()
    assert project["status"] == "ready" and project["stage"] == "plano_de_edicao"
    assert project["title"] == RAW_BRIEFING["title"]

    invalidated = longform.save_stage(project, "briefing", json.loads(project["briefing_json"]))
    assert invalidated == ["roteiro", "plano_de_edicao"]
    row = db.get_longform(project["id"])
    assert row["roteiro_json"] is None and row["plano_json"] is None
    assert row["material_json"] is not None, "the material is upstream, it stays"
    assert row["status"] == "developing"

    assert longform.save_stage(row, "material", MATERIAL) == ["briefing"]
    assert db.get_longform(project["id"])["briefing_json"] is None


def test_editing_the_material_by_hand_keeps_what_was_measured():
    """Titles, kinds and the reference flag are the user's; durations and
    transcripts were measured and stay. New items go through ingestion."""
    project = _project(stages=("material",))
    edited = {"items": [
        {"id": "m01", "title": "Ana Souza (delegada)", "kind": "link", "duration": 9999,
         "segments": [], "description": "entrevista gravada em SP"},
        {"id": "m05", "reference": False},
    ]}
    doc = longform.accept_stage(project, "material", edited)
    first = doc["items"][0]
    assert first["title"] == "Ana Souza (delegada)" and first["kind"] == "link"
    assert first["duration"] == 120.0 and len(first["segments"]) == 20
    assert doc["items"][1]["reference"] is False
    assert len(doc["items"]) == 2, "dropping an item is allowed"

    with pytest.raises(ValueError, match="not in the catalog"):
        longform.accept_stage(project, "material", {"items": [{"id": "m42"}]})
    with pytest.raises(ValueError, match="file is what it is"):
        longform.accept_stage(project, "material", {"items": [{"id": "m04", "kind": "link"}]})
    with pytest.raises(ValueError, match="At least one"):
        longform.accept_stage(project, "material", {"items": [{"id": "m05"}]})


# ------------------------------ cost estimate ------------------------------

def test_the_estimate_matches_the_plan(monkeypatch):
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    project = _project()
    plan = json.loads(project["plano_json"])
    blocks = longform.plan_blocks(plan)
    report = longform.estimate(project)

    words = sum(len(b["narration"].split()) for b in blocks if b["narration"])
    assert report["blocks"] == 4 and report["episodes"] == 1
    assert report["narration"]["blocks"] == 3
    assert report["narration"]["words"] == words == 30 + 9 + 3
    assert report["narration"]["seconds"] == pytest.approx(words / 1.6, abs=0.1)
    assert report["narration"]["mode"] == "oculto"
    assert report["stock"]["clips"] == 2
    assert report["stock"]["provider"]["configured"] is False
    assert report["interview"]["cuts"] == 2
    assert report["interview"]["seconds"] == 30.0          # 20s of m01 + 10s of m02
    assert report["images"] == 1 and report["cards"] == 2   # the title card + the stand-in
    assert report["avatar"]["seconds"] == 0.0
    # nothing prepared yet: everything has to be paid for
    assert report["narration_to_synthesize"] == 3
    assert report["shots_to_prepare"] == 7
    assert report["reused"] == 0
    assert any("placeholder" in w for w in report["warnings"])


# ------------------------------- assembly -------------------------------

@pytest.fixture
def fake_assembly(monkeypatch):
    """Assembly with the expensive parts replaced: TTS, stock, FFmpeg, QA.

    Everything the production's own logic decides — the ledger, the
    placeholders, the timeline, the jobs — stays real; the cards and lower
    thirds are real PNGs.
    """
    calls = {"tts": [], "stock": [], "qa": []}

    def synthesize(text, out_path, voice=None, log=lambda m, level="info": None):
        calls["tts"].append(text)
        out_path.write_bytes(b"fake-audio")
        return longform.tts.Narration(out_path, 7.5, longform.tts.estimate_words(text, 7.5))

    def fetch(queries, log=lambda m: None, job_dir=None, per_query=1, landscape=False):
        calls["stock"].append((queries[0], landscape))
        scratch = job_dir / "broll"
        scratch.mkdir(exist_ok=True)
        path = scratch / f"broll_{len(calls['stock'])}.mp4"
        path.write_bytes(b"fake-stock")
        return [path]

    def audit(video, ass_path=None, expected_duration=None, fmt=None):
        calls["qa"].append(fmt)
        return QAReport(passed=True, score=90)

    monkeypatch.setattr(longform.render, "ensure_ffmpeg", lambda: None)
    monkeypatch.setattr(longform.render, "probe_duration", lambda p: 0.0)
    monkeypatch.setattr(longform, "_cut_segment",
                        lambda src, start, end, dest: dest.write_bytes(b"fake-cut") or dest)
    monkeypatch.setattr(longform, "_has_audio", lambda p: True)
    monkeypatch.setattr(longform, "_thumbnail",
                        lambda video, out, at=1.0: out.write_bytes(b"jpg") or out)
    monkeypatch.setattr(longform.tts, "synthesize", synthesize)
    # Subtitles for a cut are translated through the LLM. Without this every
    # assembly test would spend a real request per cut — the tests hung on the
    # CLI instead of running. The translation tests patch this themselves.
    monkeypatch.setattr(longform, "_translate_cues",
                        lambda segments, language, log: segments)
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: ["pexels"])
    monkeypatch.setattr(longform.broll, "fetch_for_queries", fetch)
    monkeypatch.setattr(longform.timeline_render, "render_timeline",
                        lambda job_dir, timeline, out, log=lambda m: None:
                        out.write_bytes(b"fake-film") or out)
    monkeypatch.setattr(longform.qa_mod, "audit", audit)
    monkeypatch.setattr(longform.notify, "job_done", lambda *a, **k: None)
    return calls


def _timeline_of(job_id: str) -> dict:
    return json.loads((settings.jobs_dir / job_id / "timeline.json").read_text(encoding="utf-8"))


def test_assembly_produces_a_horizontal_job_with_the_narration_at_each_blocks_start(fake_assembly):
    project = _project()
    result = longform.assemble(project["id"])

    row = db.get_longform(project["id"])
    assert row["status"] == "done"
    job_id = json.loads(row["jobs_json"])["1"]
    job = db.get_job(job_id)
    assert job["status"] == "done"
    body = json.loads(job["result_json"])
    assert body["mode"] == longform.MODE
    assert body["format"] == "horizontal", "the preview draws a 16:9 frame from this"
    assert body["video"].endswith("short.mp4") and result["jobs"] == {"1": job_id}
    assert fake_assembly["qa"] == [formats.HORIZONTAL], "judged as a documentary"
    assert fake_assembly["stock"] and all(landscape for _, landscape in fake_assembly["stock"])

    edl = _timeline_of(job_id)
    assert edl["format"] == "horizontal"
    # block 1: stock 6s + card 4s = 10s, narration 7.5s at 0.0
    # block 2: the interview cut, 20s, its own audio; no narration
    # block 3: stock 8 (30->38) + link 10 (38->48) + image 24-18=6 (48->54),
    #          narration at 30.0
    # block 4: card 8s, narration at 54.0
    narration = [a for a in edl["audio"] if a["role"] == "narration"]
    assert [a["start"] for a in narration] == [0.0, 30.0, 54.0]
    assert all(a["source"].startswith("nar_") for a in narration)
    interviews = [a for a in edl["audio"] if a["role"] == "entrevista"]
    assert [(a["start"], round(a["out_point"], 1)) for a in interviews] == [(10.0, 20.0), (38.0, 10.0)]
    assert edl["duration"] == pytest.approx(62.0, abs=0.05)
    # the interview is heard, everything else is muted footage
    by_source = {v["source"]: v for v in edl["video"]}
    assert by_source["cut_shot_e01_b02_00.mp4"]["mute"] is False
    assert by_source["stock_shot_e01_b01_00.mp4"]["mute"] is True
    assert by_source["card_shot_e01_b01_01.png"]["kind"] == "image"
    assert by_source["img_shot_e01_b03_02.png"]["kind"] == "image"
    # the interviewee's name strip rides as an overlay the editor can move
    assert len(edl["media"]) == 1 and edl["media"][0]["source"] == "lt_shot_e01_b02_00.png"
    # subtitles: narration words plus the interview's own transcript
    texts = " ".join(c["text"] for c in edl["captions"])
    assert "fala 2 sobre golpes" in texts and "linha de produção" in texts

    job_dir = settings.jobs_dir / job_id
    assert (job_dir / "captions.srt").exists() and (job_dir / "thumb.jpg").exists()
    assert (settings.outputs_dir / f"{job_id}.mp4").exists()
    assert client.get(f"/api/jobs/{job_id}/timeline").json()["format"] == "horizontal"


def test_a_stock_query_that_finds_nothing_leaves_a_placeholder_and_the_film_assembles(
        fake_assembly, monkeypatch):
    def flaky(queries, log=lambda m: None, job_dir=None, per_query=1, landscape=False):
        if "phone" in queries[0]:
            return []
        scratch = job_dir / "broll"
        scratch.mkdir(exist_ok=True)
        path = scratch / "broll_ok.mp4"
        path.write_bytes(b"fake-stock")
        return [path]

    no_cutaways(monkeypatch)
    monkeypatch.setattr(longform.broll, "fetch_for_queries", flaky)
    project = _project()
    longform.assemble(project["id"])

    row = db.get_longform(project["id"])
    assert row["status"] == "done", "the production is still assembled"
    ledger = {e["key"]: e for e in json.loads(row["progress_json"])}
    missing = ledger["shot_e01_b03_00"]
    assert missing["status"] == "placeholder" and "no stock footage found" in missing["error"]
    assert missing["file"] == "ph_shot_e01_b03_00.png"
    assert ledger["shot_e01_b01_00"]["status"] == "ok"

    job_id = json.loads(row["jobs_json"])["1"]
    assert (settings.jobs_dir / job_id / missing["file"]).exists()
    edl = _timeline_of(job_id)
    assert any(v["source"] == missing["file"] for v in edl["video"]), "the slot is held"
    report = json.loads(db.get_job(job_id)["result_json"])["blocks"]
    assert [b["status"] for b in report] == ["ok", "ok", "partial", "ok"]


def test_no_stock_provider_turns_every_stock_shot_into_a_card_without_searching(
        fake_assembly, monkeypatch):
    no_cutaways(monkeypatch)
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    project = _project()
    longform.assemble(project["id"])

    assert fake_assembly["stock"] == [], "no provider, no search"
    ledger = json.loads(db.get_longform(project["id"])["progress_json"])
    stock = [e for e in ledger if e["kind"] == "shot" and e["shot"] == "stock"]
    assert len(stock) == 2 and all(e["status"] == "placeholder" for e in stock)
    assert all("Pexels" in e["error"] for e in stock)


def test_a_resume_redoes_only_what_failed(fake_assembly, monkeypatch):
    """A placeholder is a failure marker, not a result. Everything that
    succeeded — narration, cuts, the other stock clip — is not paid for twice."""
    no_cutaways(monkeypatch)
    failing = {"on": True}
    real_fetch = longform.broll.fetch_for_queries

    def flaky(queries, *args, **kwargs):
        """The fixture's stock bank, minus the one query that finds nothing.

        `_fetch_stock` passes `log` positionally, exactly as `broll` declares
        it, so the signature has to accept it — swallowing a TypeError here
        would turn BOTH shots into placeholders and quietly hide the resume.
        The search that finds nothing is recorded too: it was still an attempt.
        """
        if failing["on"] and "phone" in queries[0]:
            fake_assembly["stock"].append((queries[0], kwargs.get("landscape", False)))
            return []
        return real_fetch(queries, *args, **kwargs)

    monkeypatch.setattr(longform.broll, "fetch_for_queries", flaky)
    project = _project()
    longform.assemble(project["id"])
    assert len(fake_assembly["tts"]) == 3 and len(fake_assembly["stock"]) == 2

    failing["on"] = False
    fake_assembly["tts"].clear()
    fake_assembly["stock"].clear()
    longform.assemble(project["id"])

    assert fake_assembly["tts"] == [], "narration was on disk"
    assert [q for q, _ in fake_assembly["stock"]] == ["hands typing on phone"]
    ledger = {e["key"]: e for e in json.loads(db.get_longform(project["id"])["progress_json"])}
    assert ledger["shot_e01_b03_00"]["status"] == "ok"
    # the estimate agrees: nothing left to pay for
    report = longform.estimate(db.get_longform(project["id"]))
    assert report["narration_to_synthesize"] == 0 and report["shots_to_prepare"] == 0
    assert report["reused"] == 3 + 7


def test_a_rewritten_narration_is_recorded_again_and_the_rest_is_kept(fake_assembly):
    project = _project()
    longform.assemble(project["id"])
    fake_assembly["tts"].clear()

    row = db.get_longform(project["id"])
    script = json.loads(row["roteiro_json"])
    script["episodes"][0]["blocks"][3]["narration"] = "Ninguém está a salvo."
    longform.save_stage(row, "roteiro", longform.accept_stage(row, "roteiro", script))
    row = db.get_longform(project["id"])
    longform.save_stage(row, "plano_de_edicao", longform.accept_stage(row, "plano_de_edicao", RAW_PLAN))

    longform.assemble(project["id"])
    assert fake_assembly["tts"] == ["Ninguém está a salvo."]


def test_narration_that_fails_to_record_stays_as_captions(fake_assembly, monkeypatch):
    def mute(text, out_path, voice=None, log=lambda m, level="info": None):
        raise RuntimeError("tts provider out of credit")

    monkeypatch.setattr(longform.tts, "synthesize", mute)
    project = _project()
    longform.assemble(project["id"])

    row = db.get_longform(project["id"])
    assert row["status"] == "done"
    edl = _timeline_of(json.loads(row["jobs_json"])["1"])
    assert [a["role"] for a in edl["audio"]] == ["entrevista", "entrevista"]
    assert "linha de produção" in " ".join(c["text"] for c in edl["captions"])
    report = json.loads(db.get_job(json.loads(row["jobs_json"])["1"])["result_json"])
    assert report["blocks"][0]["narration"] == "failed"
    assert "out of credit" in report["blocks"][0]["narration_error"]


def test_narration_that_outlasts_the_footage_holds_the_last_shot_or_a_card():
    """Otherwise the picture cuts to black while the narrator is still
    speaking. A stock shot is held (the renderer loops it); an interview is
    not — looping it would replay the quote — so a card holds instead."""
    options = _options()
    catalog = longform._by_id(MATERIAL)  # noqa: SLF001
    work = settings.job_dir("longform_timeline_probe")
    blocks = [
        {"key": "e01_b01", "kind": "abertura", "title": "A", "narration": "x " * 10,
         "narrator": "oculto", "seconds": 5,
         "shots": [longform._shot("stock", query="q", seconds=5)]},  # noqa: SLF001
        {"key": "e01_b02", "kind": "ato", "title": "B", "narration": "y " * 10,
         "narrator": "oculto", "seconds": 5,
         "shots": [longform._shot("entrevista", material="m01", start=10, end=15,  # noqa: SLF001
                                  seconds=5)]},
    ]
    ledger = {}
    for block, seconds in zip(blocks, (12.0, 12.0)):
        nar = work / f"nar_{block['key']}.mp3"
        nar.write_bytes(b"x")
        ledger[f"nar_{block['key']}"] = {"kind": "narration", "key": f"nar_{block['key']}",
                                         "file": nar.name, "words_file": "",
                                         "seconds": seconds, "status": "ok"}
        shot = work / f"s_{block['key']}.mp4"
        shot.write_bytes(b"x")
        ledger[f"shot_{block['key']}_00"] = {"kind": "shot", "key": f"shot_{block['key']}_00",
                                             "file": shot.name, "seconds": 5.0,
                                             "status": "ok", "has_audio": True}
        hold = work / f"hold_{block['key']}.png"
        hold.write_bytes(b"x")
        ledger[f"hold_{block['key']}"] = {"kind": "hold", "key": f"hold_{block['key']}",
                                          "file": hold.name, "status": "ok"}

    edl = longform.build_timeline(work, blocks, ledger, catalog, options)

    assert edl.format == "horizontal"
    # block 1: 5s of stock against 12s of narration — the stock is held
    assert edl.video[0].duration == pytest.approx(12.0, abs=0.05)
    # block 2 starts where block 1's narration ends, and its narration lands there
    assert edl.video[1].start == pytest.approx(12.0, abs=0.05)
    assert [a.start for a in edl.audio if a.role == "narration"] == [0.0, 12.0]
    # the interview keeps its 5s and a card covers the remaining 7s
    assert edl.video[1].duration == pytest.approx(5.0, abs=0.05)
    assert edl.video[2].source == "hold_e01_b02.png"
    assert edl.video[2].duration == pytest.approx(7.0, abs=0.05)
    assert edl.duration == pytest.approx(24.0, abs=0.05)


def test_the_jobs_never_sit_in_the_shorts_queue(fake_assembly, monkeypatch):
    """A job left in `queued`/`running` would be picked up by `worker.start()`
    after a restart and run through the shorts pipeline."""
    monkeypatch.setattr(longform.timeline_render, "render_timeline",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("render interrupted")))
    project = _project()
    with pytest.raises(RuntimeError, match="render interrupted"):
        longform.assemble(project["id"])

    row = db.get_longform(project["id"])
    assert row["status"] == "error" and "interrupted" in row["error"]
    job_id = json.loads(row["jobs_json"])["1"]
    assert db.get_job(job_id)["status"] not in ("queued", "running")
    # what was already prepared stays written down, so the retry is cheap
    assert len(json.loads(row["progress_json"])) == 3 + 7 + 4


def test_an_avatar_narrator_is_refused_clearly_when_heygen_is_not_configured(fake_assembly):
    project = _project(narrator="avatar", avatar_id="a1", avatar_voice_id="v1")
    with pytest.raises(RuntimeError, match="HeyGen"):
        longform.assemble(project["id"])
    assert fake_assembly["tts"] == [], "nothing was started"


def test_a_short_film_assembles_from_real_material_with_no_video_generator(fake_assembly):
    project = _project(kind="curta", tone="terror", target_minutes=6)
    longform.assemble(project["id"])
    row = db.get_longform(project["id"])
    assert row["status"] == "done"
    assert json.loads(db.get_job(json.loads(row["jobs_json"])["1"])["result_json"])["type"] == "curta"


SERIES_SCRIPT = {"episodes": [
    {"episode": 1, "title": "O golpe", "blocks": [
        {"kind": "abertura", "title": "Gancho", "narration": "Tudo começa com uma mensagem.",
         "seconds": 8, "visual": "telefone", "moment": None},
        {"kind": "gancho", "title": "Gancho final", "narration": "No próximo episódio, a delegada.",
         "seconds": 6, "visual": "cartela", "moment": None}]},
    {"episode": 2, "title": "A delegada", "blocks": [
        {"kind": "recap", "title": "Recap", "narration": "No episódio anterior, a mensagem.",
         "seconds": 6, "visual": "cartela", "moment": None},
        {"kind": "entrevista", "title": "Ana", "narration": "", "seconds": 20,
         "visual": "Ana em quadro", "moment": {"material": "m01", "start": 10, "end": 30}}]},
]}

SERIES_PLAN = {"episodes": [
    {"episode": 1, "blocks": [
        {"key": "e01_b01", "shots": [{"kind": "stock", "material": "", "start": 0, "end": 0,
                                      "query": "phone screen message", "text": "",
                                      "seconds": 8, "lower_third": ""}]},
        {"key": "e01_b02", "shots": [{"kind": "cartela", "material": "", "start": 0, "end": 0,
                                      "query": "", "text": "Continua", "seconds": 6,
                                      "lower_third": ""}]}]},
    {"episode": 2, "blocks": [
        {"key": "e02_b01", "shots": [{"kind": "cartela", "material": "", "start": 0, "end": 0,
                                      "query": "", "text": "Anteriormente", "seconds": 6,
                                      "lower_third": ""}]},
        {"key": "e02_b02", "shots": [{"kind": "entrevista", "material": "m01", "start": 10,
                                      "end": 30, "query": "", "text": "", "seconds": 0,
                                      "lower_third": "Ana Souza"}]}]},
]}


def test_a_mini_series_assembles_one_job_per_episode(fake_assembly):
    project = _project(kind="mini_serie", episodes=2, script=SERIES_SCRIPT, plan=SERIES_PLAN)
    script = json.loads(project["roteiro_json"])
    assert [b["kind"] for b in script["episodes"][0]["blocks"]] == ["abertura", "gancho"]
    assert [b["kind"] for b in script["episodes"][1]["blocks"]] == ["recap", "entrevista"]

    result = longform.assemble(project["id"])

    row = db.get_longform(project["id"])
    jobs = json.loads(row["jobs_json"])
    assert set(jobs) == {"1", "2"} and len(set(jobs.values())) == 2
    assert result["jobs"] == jobs
    for number, job_id in jobs.items():
        job = db.get_job(job_id)
        body = json.loads(job["result_json"])
        assert job["status"] == "done"
        assert body["episode"] == int(number) and body["format"] == "horizontal"
        assert f"Ep. {number}" in body["title"]
        assert (settings.jobs_dir / job_id / "timeline.json").exists()
    keys = [b["key"] for b in json.loads(db.get_job(jobs["2"])["result_json"])["blocks"]]
    assert keys == ["e02_b01", "e02_b02"]
    assert longform.estimate(row)["episodes"] == 2


@needs_ffmpeg
def test_a_real_interview_cut_renders_a_real_horizontal_file(monkeypatch):
    """The one end-to-end run with real FFmpeg: a 16:9 recording cut by the
    second with its audio, a title card, a narration file, rendered and judged
    as a documentary. Only the LLM and the TTS voice are faked."""
    monkeypatch.setattr(longform.notify, "job_done", lambda *a, **k: None)
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])

    def synthesize(text, out_path, voice=None, log=lambda m, level="info": None):
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
                        "-c:a", "libmp3lame", str(out_path)], check=True, capture_output=True)
        return longform.tts.Narration(out_path, 2.0, longform.tts.estimate_words(text, 2.0))

    monkeypatch.setattr(longform.tts, "synthesize", synthesize)

    project_id = db.create_longform("mini_documentario", PROMPT, "", "", {"target_minutes": 5})
    work = longform.project_dir(project_id)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-t", "6", str(work / "m01.mp4")],
                   check=True, capture_output=True)
    material = {"items": [dict(MATERIAL["items"][0], duration=6.0,
                               segments=[{"start": 0.5, "end": 5.5, "text": "uma fala real"}])]}
    script = {"episodes": [{"episode": 1, "title": "t", "blocks": [
        {"kind": "abertura", "title": "Abertura", "narration": "Uma frase curta.",
         "seconds": 3, "visual": "cartela", "moment": None},
        {"kind": "entrevista", "title": "Ana", "narration": "", "seconds": 3,
         "visual": "Ana", "moment": {"material": "m01", "start": 1, "end": 4}}]}]}
    plan = {"episodes": [{"episode": 1, "blocks": [
        {"key": "e01_b01", "shots": [{"kind": "cartela", "material": "", "start": 0, "end": 0,
                                      "query": "", "text": "Golpes", "seconds": 3,
                                      "lower_third": ""}]},
        {"key": "e01_b02", "shots": [{"kind": "entrevista", "material": "m01", "start": 1,
                                      "end": 4, "query": "", "text": "", "seconds": 0,
                                      "lower_third": "Ana Souza"}]}]}]}
    row = db.get_longform(project_id)
    opts = longform.options_of(row)
    longform.save_stage(row, "material", material)
    row = db.get_longform(project_id)
    longform.save_stage(row, "briefing", longform._normalize_briefing(  # noqa: SLF001
        dict(RAW_BRIEFING, moments=[]), material, opts))
    row = db.get_longform(project_id)
    script_doc = longform._normalize_script(script, material, opts)  # noqa: SLF001
    longform.save_stage(row, "roteiro", script_doc)
    row = db.get_longform(project_id)
    longform.save_stage(row, "plano_de_edicao",
                        longform._normalize_plan(plan, material, script_doc, opts))  # noqa: SLF001

    longform.assemble(project_id)

    row = db.get_longform(project_id)
    assert row["status"] == "done", row["error"]
    job_id = json.loads(row["jobs_json"])["1"]
    final = settings.jobs_dir / job_id / "short.mp4"
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=s=x:p=0", str(final)],
                         capture_output=True, text=True).stdout.strip()
    assert out == "1920x1080"
    job = db.get_job(job_id)
    qa = json.loads(job["qa_json"])
    assert not {i["check"] for i in qa["issues"]} & {"resolucao", "proporcao", "duracao_maxima"}
    ledger = {e["key"]: e for e in json.loads(row["progress_json"])}
    cut = ledger["shot_e01_b02_00"]
    assert cut["status"] == "ok" and cut["has_audio"] is True
    assert cut["seconds"] == pytest.approx(3.0, abs=0.2)
    edl = _timeline_of(job_id)
    assert edl["format"] == "horizontal" and edl["duration"] == pytest.approx(6.0, abs=0.2)


# ------------------------------- ingestion -------------------------------

def test_ingestion_catalogs_links_uploads_images_and_references(monkeypatch, tmp_path):
    interview = tmp_path / "upl_int.mp4"
    interview.write_bytes(b"v")
    picture = tmp_path / "upl_img.png"
    picture.write_bytes(b"i")

    def download(url, job_dir):
        if "bad" in url:
            raise RuntimeError("yt-dlp failed: 403")
        video = job_dir / "source.mp4"
        video.write_bytes(b"v")
        return video, {"title": f"Vídeo {url[-1]}", "duration": 90, "description": "d"}

    class Article:
        title, text = "Febraban 2026", "Os golpes cresceram."

    words = [{"word": "Olá.", "start": 0.0, "end": 0.4},
             {"word": "Tudo", "start": 0.6, "end": 0.9}, {"word": "bem.", "start": 0.9, "end": 1.2}]
    monkeypatch.setattr(longform.ingest, "download_video", download)
    monkeypatch.setattr(longform.ingest, "whisper_segments",
                        lambda video, log=lambda m: None: [{"start": 0, "end": 5, "text": "oi"}])
    monkeypatch.setattr(longform.ingest, "_ingest_article", lambda url: Article())
    monkeypatch.setattr(longform.reels, "transcribe_words", lambda video, log: (words, "pt"))
    monkeypatch.setattr(longform.render, "probe_duration", lambda p: 42.0)
    def resolve(upload_id):
        """`uploads.resolve` raises FileNotFoundError for an id whose file is
        gone — the fake has to fail the same way, or the ingestion never gets
        to write "no longer on disk" on the item."""
        try:
            return {"upl_int": interview, "upl_img": picture}[upload_id]
        except KeyError:
            raise FileNotFoundError(f"Upload {upload_id} not found") from None

    monkeypatch.setattr(longform.uploads_router, "resolve", resolve)

    project_id = db.create_longform("documentario", PROMPT, "", "", {"sources": {
        "links": ["https://youtube.com/watch?v=a", "https://youtube.com/watch?v=bad",
                  "https://exemplo.com/artigo"],
        "attachments": ["upl_int", "upl_img", "upl_gone"],
        "references": ["https://youtube.com/watch?v=r"]}})
    doc = longform.ingest_material(project_id)

    by_id = {i["id"]: i for i in doc["items"]}
    assert [(i["id"], i["kind"], i["status"]) for i in doc["items"]] == [
        ("m01", "link", "ok"), ("m02", "link", "failed"), ("m03", "artigo", "ok"),
        ("m04", "entrevista", "ok"), ("m05", "imagem", "ok"), ("m06", "entrevista", "failed"),
        ("m07", "link", "ok")]
    assert by_id["m01"]["duration"] == 90 and by_id["m01"]["segments"][0]["text"] == "oi"
    assert "403" in by_id["m02"]["error"]
    assert by_id["m03"]["text"] == "Os golpes cresceram."
    # the interview: word timings on disk, sentences in the catalog
    assert by_id["m04"]["duration"] == 42.0
    assert by_id["m04"]["segments"] == [{"start": 0.0, "end": 0.4, "text": "Olá."},
                                        {"start": 0.6, "end": 1.2, "text": "Tudo bem."}]
    assert (longform.project_dir(project_id) / by_id["m04"]["words_file"]).exists()
    assert "no longer on disk" in by_id["m06"]["error"]
    assert by_id["m07"]["reference"] is True and by_id["m05"]["reference"] is False

    row = db.get_longform(project_id)
    assert row["status"] == "developing" and row["stage"] == "material"
    assert any("failed and was skipped" in e["message"] for e in db.get_events(project_id))


def test_ingestion_with_nothing_usable_is_an_error_that_says_why(monkeypatch):
    monkeypatch.setattr(longform.ingest, "download_video",
                        lambda url, job_dir: (_ for _ in ()).throw(RuntimeError("403")))
    project_id = db.create_longform("documentario", PROMPT, "", "", {"sources": {
        "links": ["https://youtube.com/watch?v=x"], "attachments": [],
        "references": ["https://youtube.com/watch?v=ref"]}})
    with pytest.raises(longform.MaterialUnusable, match="reference example alone"):
        longform.ingest_material(project_id)
    row = db.get_longform(project_id)
    assert row["status"] == "error" and "403" in row["error"]


def test_segments_from_words_break_at_full_stops_and_pauses():
    words = [{"word": "Um", "start": 0.0, "end": 0.3}, {"word": "dois.", "start": 0.3, "end": 0.6},
             {"word": "Três", "start": 0.7, "end": 1.0}, {"word": "quatro", "start": 2.5, "end": 2.8}]
    assert [s["text"] for s in longform._segments_from_words(words)] == [  # noqa: SLF001
        "Um dois.", "Três", "quatro"]


# --------------------------------- routes ---------------------------------

@pytest.fixture
def no_queue(monkeypatch):
    """The work is queued but not done: the route is what is under test."""
    queued: list[tuple[str, str]] = []

    def enqueue(project_id, action="assemble"):
        db.update_longform(project_id, status="ingesting" if action == "ingest" else "queued",
                           error=None)
        queued.append((project_id, action))

    monkeypatch.setattr(worker, "enqueue_longform", enqueue)
    return queued


def _create(**overrides):
    body = {"type": "documentario", "prompt": PROMPT, "instruction": INSTRUCTION,
            "links": ["https://youtube.com/watch?v=a"], "attachments": ["upl_1"],
            "references": ["https://youtube.com/watch?v=ref"], "tone": "terror",
            "target_minutes": 20}
    body.update(overrides)
    return client.post("/api/longform", json=body)


def test_creating_a_production_queues_the_ingestion_and_persists_the_request(no_queue):
    r = _create()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ingesting" and body["type"] == "documentario"
    assert body["material"] is None and body["briefing"] is None
    assert body["options"]["tone"] == "terror" and body["options"]["target_minutes"] == 20
    assert body["sources"] == {"links": ["https://youtube.com/watch?v=a"],
                               "attachments": ["upl_1"],
                               "references": ["https://youtube.com/watch?v=ref"]}
    assert "material_json" not in body
    assert no_queue == [(body["id"], "ingest")]
    assert db.get_longform(body["id"])["prompt"] == PROMPT


def test_creating_a_production_with_bad_input_returns_400_before_anything_runs(no_queue):
    assert _create(prompt="curto").status_code == 400
    r = _create(links=[], attachments=[])
    assert r.status_code == 400 and "Reference examples alone" in r.json()["detail"]
    r = _create(target_minutes=3)
    assert r.status_code == 400 and "15 and 30 minutes" in r.json()["detail"]
    r = _create(type="mini_serie", episodes=1, target_minutes=8)
    assert r.status_code == 400 and "episodes" in r.json()["detail"]
    r = _create(narrator="avatar")
    assert r.status_code == 400 and "HeyGen" in r.json()["detail"]
    assert _create(niche="astrologia").status_code == 400
    assert _create(type="novela").status_code == 422
    assert no_queue == []


def test_the_types_route_describes_what_the_screen_offers():
    body = client.get("/api/longform/types").json()
    assert [t["id"] for t in body["types"]] == list(longform.TYPES)
    assert body["types"][0]["min_minutes"] == 15
    assert {n["id"] for n in body["narrators"]} == set(longform.NARRATORS)
    assert "terror" in {t["id"] for t in body["tones"]}
    assert body["stages"] == list(longform.STAGES)


def test_unknown_production_returns_404():
    assert client.get("/api/longform/doc_inexistente").status_code == 404
    assert client.get("/api/longform/doc_x/events").status_code == 404
    assert client.post("/api/longform/doc_x/stage/briefing").status_code == 404
    assert client.put("/api/longform/doc_x/briefing", json={}).status_code == 404
    assert client.post("/api/longform/doc_x/generate").status_code == 404
    assert client.delete("/api/longform/doc_x").status_code == 404


def test_the_poll_brings_every_stage_and_the_estimate(monkeypatch):
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    project = _project()
    body = client.get(f"/api/longform/{project['id']}").json()

    assert body["status"] == "ready"
    assert {"material", "briefing", "roteiro", "plano_de_edicao", "options", "progress",
            "jobs", "estimate"} <= body.keys()
    assert body["progress"] is None and body["jobs"] is None
    assert body["estimate"]["blocks"] == 4
    assert body["material"]["items"][0]["segments"], "the full catalog on the detail"
    assert body["roteiro"]["episodes"][0]["blocks"][0]["key"] == "e01_b01"

    listed = client.get("/api/longform").json()
    assert [p["id"] for p in listed] == [project["id"]]
    assert "estimate" not in listed[0]
    assert "segments" not in listed[0]["material"]["items"][0], "the listing stays cheap"
    assert listed[0]["briefing"] is True and listed[0]["progress"] is False


def test_developing_a_stage_reports_what_it_invalidated_and_refused(fake_llm):
    project = _project()
    r = client.post(f"/api/longform/{project['id']}/stage/briefing",
                    json={"instruction": "foque no golpe do falso funcionário"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invalidated"] == ["roteiro", "plano_de_edicao"]
    assert body["briefing"]["logline"].startswith("Como o golpe")
    # the five references planted in RAW_BRIEFING that cannot stand: the fact
    # sourced to m77 (not in the catalog), the fact sourced to the reference
    # m05, the moment 500s into a 120s interview, the moment taken from m05,
    # and the moment on m99. The m02 moment ending at 200s is clamped to the
    # material's 90s, which is a correction, not a refusal.
    assert len(body["rejected"]) == 5
    assert "foque no golpe do falso funcionário" in fake_llm[0]["prompt"]
    assert db.get_longform(project["id"])["plano_json"] is None
    # and they are not only in the response: nothing is silently kept, so the
    # same refusals are in the log the screen polls
    messages = [e["message"] for e in db.get_events(project["id"])
                if e["message"].startswith("briefing:")]
    assert len(messages) == 5
    assert any("does not exist" in m for m in messages)
    assert any("reference" in m for m in messages)


def test_developing_material_requeues_the_ingestion_and_says_what_it_will_drop(no_queue):
    project = _project()
    r = client.post(f"/api/longform/{project['id']}/stage/material")
    assert r.status_code == 200
    assert r.json()["queued"] is True
    assert r.json()["will_invalidate"] == ["briefing", "roteiro", "plano_de_edicao"]
    assert no_queue == [(project["id"], "ingest")]
    # busy: nothing else can be developed or edited until the ingestion lands
    assert client.post(f"/api/longform/{project['id']}/stage/briefing").status_code == 400
    assert client.put(f"/api/longform/{project['id']}/roteiro", json={}).status_code == 400
    assert client.delete(f"/api/longform/{project['id']}").status_code == 400


def test_developing_a_stage_out_of_order_or_unknown_returns_400(fake_llm):
    project = _project(stages=("material",))
    r = client.post(f"/api/longform/{project['id']}/stage/plano_de_edicao")
    assert r.status_code == 400 and "briefing" in r.json()["detail"]
    r = client.post(f"/api/longform/{project['id']}/stage/trailer")
    assert r.status_code == 400 and "plano_de_edicao" in r.json()["detail"]
    assert fake_llm == []


def test_a_model_failure_in_a_stage_returns_502_with_the_real_reason(monkeypatch):
    project = _project(stages=("material",))
    monkeypatch.setattr(longform.llm, "complete_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("codex CLI went over 420s")))
    r = client.post(f"/api/longform/{project['id']}/stage/briefing")
    assert r.status_code == 502 and "420s" in r.json()["detail"]
    assert "420s" in db.get_longform(project["id"])["error"]


def test_saving_a_stage_by_hand_normalizes_it_and_refuses_the_unusable():
    project = _project(stages=("material", "briefing"))
    r = client.put(f"/api/longform/{project['id']}/roteiro", json=RAW_SCRIPT)
    assert r.status_code == 200, r.text
    blocks = r.json()["roteiro"]["episodes"][0]["blocks"]
    assert blocks[0]["adjusted"] is True and blocks[2]["moment"] is None
    assert r.json()["invalidated"] == []
    assert db.get_longform(project["id"])["status"] == "developing"

    r = client.put(f"/api/longform/{project['id']}/roteiro", json={"episodes": []})
    assert r.status_code == 400 and "episodes" in r.json()["detail"]
    r = client.put(f"/api/longform/{project['id']}/plano_de_edicao", json=RAW_PLAN)
    assert r.status_code == 200, "the script was just saved, so the plan can follow"
    assert r.json()["rejected"]
    assert db.get_longform(project["id"])["status"] == "ready"


def test_generating_answers_with_the_estimate_and_starts_nothing(no_queue, monkeypatch):
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    project = _project()
    body = client.post(f"/api/longform/{project['id']}/generate", json={}).json()
    assert body["queued"] is False
    assert body["estimate"]["stock"]["clips"] == 2
    assert body["estimate"]["narration"]["words"] == 42
    assert no_queue == [], "nothing paid for before the caller saw the estimate"


def test_generating_without_a_stock_provider_says_what_to_do(no_queue, monkeypatch):
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    project = _project()
    r = client.post(f"/api/longform/{project['id']}/generate", json={"confirm": True})
    assert r.status_code == 400 and "placeholder_ok" in r.json()["detail"]
    assert no_queue == []

    r = client.post(f"/api/longform/{project['id']}/generate",
                    json={"confirm": True, "placeholder_ok": True})
    assert r.status_code == 200 and r.json()["queued"] is True
    assert no_queue == [(project["id"], "assemble")]
    assert client.post(f"/api/longform/{project['id']}/generate",
                       json={"confirm": True}).status_code == 400, "already queued"


def test_generating_with_a_stock_provider_needs_no_placeholder_flag(no_queue, monkeypatch):
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: ["pexels"])
    project = _project()
    r = client.post(f"/api/longform/{project['id']}/generate", json={"confirm": True})
    assert r.status_code == 200 and no_queue == [(project["id"], "assemble")]


def test_generating_without_a_plan_returns_400(no_queue):
    project = _project(stages=("material", "briefing", "roteiro"))
    r = client.post(f"/api/longform/{project['id']}/generate", json={"confirm": True})
    assert r.status_code == 400 and "edit plan" in r.json()["detail"]
    assert no_queue == []


def test_the_events_route_merges_the_project_log_with_its_jobs(fake_assembly):
    project = _project()
    db.log_event(project["id"], "m01: interview transcribed")
    longform.assemble(project["id"])
    events = client.get(f"/api/longform/{project['id']}/events").json()
    messages = [e["message"] for e in events]
    assert "m01: interview transcribed" in messages
    assert any(m.startswith("Timeline:") for m in messages)


def test_deleting_a_production_removes_the_project_and_its_material():
    project = _project()
    work = longform.project_dir(project["id"])
    assert (work / "m01.mp4").exists()
    assert client.delete(f"/api/longform/{project['id']}").status_code == 200
    assert db.get_longform(project["id"]) is None
    assert not work.exists()


def test_a_production_goes_on_its_own_queue_and_not_on_the_shorts_one():
    """It waits behind other productions, never in front of a short that is
    ready to render — and queueing clears the previous attempt's error."""
    project = _project()
    db.update_longform(project["id"], status="error", error="provider was down")
    shorts_waiting = worker.queue_size()

    worker.enqueue_longform(project["id"], "assemble")

    assert worker._longform_queue.get_nowait() == ("assemble", project["id"])  # noqa: SLF001
    assert worker.queue_size() == shorts_waiting
    row = db.get_longform(project["id"])
    assert row["status"] == "queued" and row["error"] is None

    worker.enqueue_longform(project["id"], "ingest")
    assert worker._longform_queue.get_nowait() == ("ingest", project["id"])  # noqa: SLF001
    assert db.get_longform(project["id"])["status"] == "ingesting"


# ------------------------- the drawn stock fallback -------------------------

def _draws(monkeypatch, calls: list[str], fail: bool = False):
    """An image generator that is keyed and either draws or refuses."""
    monkeypatch.setattr(longform.imagegen, "providers_ready", lambda *a, **k: True)

    def draw(prompt, out_path, aspect="16:9", log=None, **kwargs):
        calls.append(prompt)
        if fail:
            raise RuntimeError("out of credit")
        out_path.write_bytes(b"fake-image")
        return out_path

    monkeypatch.setattr(longform.imagegen, "generate_image", draw)


def test_with_no_stock_bank_an_image_generator_draws_the_shot(fake_assembly, monkeypatch):
    """A card with the search terms printed on it is the worst version of this
    shot. A drawn frame is not the recording the query asked for, but it is a
    picture of the idea."""
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    drawn: list[str] = []
    _draws(monkeypatch, drawn)

    project = _project()
    longform.assemble(project["id"])

    assert fake_assembly["stock"] == [], "no provider, no search"
    assert len(drawn) == 2, "both stock shots were drawn"
    ledger = json.loads(db.get_longform(project["id"])["progress_json"])
    stock = [e for e in ledger if e["kind"] == "shot" and e["shot"] == "stock"]
    assert all(e["status"] == "ok" for e in stock)
    assert all(e["file"].endswith(".png") for e in stock)


def test_the_drawing_carries_the_query_and_the_productions_style(fake_assembly, monkeypatch):
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    drawn: list[str] = []
    _draws(monkeypatch, drawn)

    longform.assemble(_project()["id"])

    assert any("phone" in p for p in drawn), "the shot's own query"
    assert all("16:9" in p for p in drawn), "the production's frame"
    assert all("sem texto" in p for p in drawn)


def test_real_stock_still_wins_over_a_drawing(fake_assembly, monkeypatch):
    """Second choice, never first: the query was written to find footage."""
    drawn: list[str] = []
    _draws(monkeypatch, drawn)

    longform.assemble(_project()["id"])

    assert len(fake_assembly["stock"]) == 2
    assert drawn == [], "nothing to draw while the stock bank answers"


def test_a_query_stock_cannot_fill_is_drawn_instead_of_carded(fake_assembly, monkeypatch):
    """The gap this closes: a configured bank that simply has no clip for the
    query used to leave a placeholder card."""
    def nothing(queries, *args, **kwargs):
        fake_assembly["stock"].append((queries[0], kwargs.get("landscape", False)))
        return []

    monkeypatch.setattr(longform.broll, "fetch_for_queries", nothing)
    drawn: list[str] = []
    _draws(monkeypatch, drawn)

    project = _project()
    longform.assemble(project["id"])

    assert len(fake_assembly["stock"]) == 2, "the bank was still asked first"
    assert len(drawn) == 2
    ledger = json.loads(db.get_longform(project["id"])["progress_json"])
    stock = [e for e in ledger if e["kind"] == "shot" and e["shot"] == "stock"]
    assert all(e["status"] == "ok" for e in stock)


def test_a_generator_that_refuses_falls_back_to_the_card(fake_assembly, monkeypatch):
    """The placeholder is still the floor: one shot must not take a half-hour
    production down."""
    no_cutaways(monkeypatch)
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    _draws(monkeypatch, [], fail=True)

    project = _project()
    longform.assemble(project["id"])

    ledger = json.loads(db.get_longform(project["id"])["progress_json"])
    stock = [e for e in ledger if e["kind"] == "shot" and e["shot"] == "stock"]
    assert all(e["status"] == "placeholder" for e in stock)
    assert all("Pexels" in e["error"] for e in stock)


def test_a_drawn_shot_is_not_bought_twice_on_a_resume(fake_assembly, monkeypatch):
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    drawn: list[str] = []
    _draws(monkeypatch, drawn)

    project = _project()
    longform.assemble(project["id"])
    assert len(drawn) == 2

    drawn.clear()
    longform.assemble(project["id"])
    assert drawn == [], "the images were already on disk"


def test_the_estimate_says_the_stock_shots_will_be_drawn(monkeypatch):
    """The estimate is read to decide whether to run at all, so it has to say
    which of the two fates those shots meet."""
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    monkeypatch.setattr(longform.imagegen, "providers_ready", lambda *a, **k: True)

    report = longform.estimate(_project())
    warning = next(w for w in report["warnings"] if "stock shot" in w)
    assert "generated as images" in warning
    assert "not real footage" in warning


# --------------------- cover with the user's own footage ---------------------

def test_a_stock_shot_no_bank_can_fill_is_covered_with_the_users_footage(
        fake_assembly, monkeypatch):
    """Better than a card with the search terms printed on it: the material is
    already on disk, already about the subject, already paid for."""
    monkeypatch.setattr(longform.broll, "providers_ready", lambda: [])
    project = _project()
    longform.assemble(project["id"])

    ledger = json.loads(db.get_longform(project["id"])["progress_json"])
    stock = [e for e in ledger if e["kind"] == "shot" and e["shot"] == "stock"]
    assert stock and all(e["status"] == "ok" for e in stock)
    assert all(e["file"].startswith("cover_") for e in stock)


def test_the_cover_never_replays_a_window_a_quote_already_uses(fake_assembly):
    """Replaying a sentence the film already used reads as a mistake, not as
    b-roll."""
    project = _project()
    plan = json.loads(project["plano_json"])
    blocks = longform.plan_blocks(plan)
    catalog = {i["id"]: i for i in json.loads(project["material_json"])["items"]}
    quotes = [(s["material"], s["start"], s["end"]) for b in blocks
              for s in b["shots"] if s["kind"] in ("entrevista", "link")]
    assert quotes, "the fixture has to use quotes for this to mean anything"

    cutaways = longform._Cutaways(catalog, blocks)  # noqa: SLF001
    for _ in range(6):
        window = cutaways.take(6.0)
        if window is None:
            break
        material, start, end = window
        for quoted_material, quoted_start, quoted_end in quotes:
            if material != quoted_material:
                continue
            assert end <= quoted_start or start >= quoted_end, \
                f"{material} {start}-{end} overlaps the quote {quoted_start}-{quoted_end}"


def test_the_cover_hands_out_each_window_only_once(fake_assembly):
    project = _project()
    blocks = longform.plan_blocks(json.loads(project["plano_json"]))
    catalog = {i["id"]: i for i in json.loads(project["material_json"])["items"]}

    cutaways = longform._Cutaways(catalog, blocks)  # noqa: SLF001
    taken = []
    while len(taken) < 8:
        window = cutaways.take(5.0)
        if window is None:
            break
        taken.append(window)
    assert len(taken) >= 2, "the fixture's material has room for a few covers"
    for index, (material, start, end) in enumerate(taken):
        for other_material, other_start, other_end in taken[index + 1:]:
            if material != other_material:
                continue
            assert end <= other_start or start >= other_end, \
                "two shots were given the same footage"


def test_the_cover_skips_the_intro_and_the_credits(fake_assembly):
    """Where an intro card and end credits live — the two stretches most
    likely to be black or a logo."""
    project = _project()
    blocks = longform.plan_blocks(json.loads(project["plano_json"]))
    catalog = {i["id"]: i for i in json.loads(project["material_json"])["items"]}

    cutaways = longform._Cutaways(catalog, blocks)  # noqa: SLF001
    while True:
        window = cutaways.take(4.0)
        if window is None:
            break
        material, start, end = window
        duration = float(catalog[material]["duration"])
        assert start >= longform._Cutaways.EDGE  # noqa: SLF001
        assert end <= duration - longform._Cutaways.EDGE + 0.01  # noqa: SLF001


def test_a_reference_example_is_never_used_as_cover(fake_assembly):
    """A reference is structure only — putting its footage in the film would
    publish someone else's documentary inside this one."""
    project = _project()
    blocks = longform.plan_blocks(json.loads(project["plano_json"]))
    catalog = {i["id"]: i for i in json.loads(project["material_json"])["items"]}
    assert any(i.get("reference") for i in catalog.values()), "fixture needs one"

    cutaways = longform._Cutaways(catalog, blocks)  # noqa: SLF001
    while True:
        window = cutaways.take(4.0)
        if window is None:
            break
        assert not catalog[window[0]].get("reference")


# ------------------------ subtitles in the target language ------------------

def test_a_quote_in_another_language_is_subtitled_in_the_productions_language(
        fake_assembly, monkeypatch):
    """The viewer reading English subtitles under Portuguese narration is
    being helped with the language they did not need help with."""
    asked: list[str] = []

    def translate(system, prompt, schema=None, max_tokens=8000, purpose=""):
        asked.append(prompt)
        lines = [line for line in prompt.splitlines() if line[:1].isdigit()]
        return {"lines": [{"i": i, "text": f"[pt] {line.split('. ', 1)[-1]}"}
                          for i, line in enumerate(lines)]}

    monkeypatch.setattr(longform, "_translate_cues", _REAL_TRANSLATE)
    monkeypatch.setattr(longform.llm, "complete_json", translate)
    project = _project()
    longform.assemble(project["id"])

    assert asked, "the cuts' transcript lines were sent for translation"
    timeline = _timeline_of(json.loads(
        db.get_longform(project["id"])["jobs_json"])["1"])
    interview_cues = [c["text"] for c in timeline["captions"]
                      if c["text"].startswith("[pt] ")]
    assert interview_cues, "the interview subtitles came from the translation"


def test_a_failed_translation_keeps_the_original_subtitles(fake_assembly, monkeypatch):
    """A film subtitled in the source language is worse than one subtitled in
    the target, and much better than one with no subtitles at all."""
    def refuse(*_a, **_k):
        raise RuntimeError("every model is out of quota")

    monkeypatch.setattr(longform, "_translate_cues", _REAL_TRANSLATE)
    monkeypatch.setattr(longform.llm, "complete_json", refuse)
    project = _project()
    longform.assemble(project["id"])

    timeline = _timeline_of(json.loads(
        db.get_longform(project["id"])["jobs_json"])["1"])
    assert timeline["captions"], "the subtitles survived the failure"


def test_the_translation_is_not_paid_for_twice_on_a_resume(fake_assembly, monkeypatch):
    calls: list[str] = []

    def translate(system, prompt, schema=None, max_tokens=8000, purpose=""):
        calls.append(purpose)
        return {"lines": []}

    monkeypatch.setattr(longform, "_translate_cues", _REAL_TRANSLATE)
    monkeypatch.setattr(longform.llm, "complete_json", translate)
    project = _project()
    longform.assemble(project["id"])
    first = len(calls)
    assert first > 0

    calls.clear()
    longform.assemble(project["id"])
    assert calls == [], "the cuts and their subtitles were already on disk"


# ------------------- a cut that lands on the source's own black -------------

def test_a_cut_opening_on_black_is_moved_forward(fake_assembly, monkeypatch):
    """The failure the transcript cannot show: someone talks over a title card
    or a chapter transition, so the window passes every check and then plays
    as five seconds of a broken player."""
    monkeypatch.setattr(longform.render, "leading_black",
                        lambda path, minimum=0.3: 5.07)
    cuts: list[tuple[float, float]] = []
    monkeypatch.setattr(longform, "_cut_segment",
                        lambda src, start, end, dest: cuts.append((start, end))
                        or dest.write_bytes(b"fake-cut") or dest)

    project = _project()
    longform.assemble(project["id"])

    # For at least one window there are two cuts: the one that was planned and
    # the one that slid past the black, 5.07s + a margin later, keeping its
    # length — a shorter window would drop the end of the quote.
    slid = [(start, end) for start, end in cuts
            if any(abs(start - (s + 5.07 + 0.2)) < 0.01
                   and abs((end - start) - (e - s)) < 0.01
                   for s, e in cuts)]
    assert slid, f"no window was moved past the black: {cuts}"


def test_a_black_open_with_no_room_to_move_is_reported_not_forced(
        fake_assembly, monkeypatch):
    """Sliding a window past the end of the material would cut nothing at all."""
    monkeypatch.setattr(longform.render, "leading_black",
                        lambda path, minimum=0.3: 5.0)
    lines: list[tuple[str, str]] = []
    shot = {"kind": "link", "material": "m02", "start": 500.0, "end": 508.0,
            "seconds": 8.0}

    result = longform._skip_black_open(  # noqa: SLF001
        Path("nowhere.mp4"), shot, "shot_x", Path("cut.mp4"), 509.0,
        lambda m, level="info": lines.append((level, m)))

    assert result == Path("cut.mp4"), "the cut it already had"
    assert shot["start"] == 500.0, "the window was not moved off the material"
    assert any(level == "warn" and "no room" in m for level, m in lines)


def test_a_clean_cut_is_not_cut_twice(fake_assembly, monkeypatch):
    """Probing is cheap, re-cutting is not: a window that opens on a picture
    must not pay for a second pass."""
    monkeypatch.setattr(longform.render, "leading_black",
                        lambda path, minimum=0.3: 0.0)
    cuts: list[tuple[float, float]] = []
    monkeypatch.setattr(longform, "_cut_segment",
                        lambda src, start, end, dest: cuts.append((start, end))
                        or dest.write_bytes(b"fake-cut") or dest)

    longform.assemble(_project()["id"])
    assert len(cuts) == len(set(cuts))


# ---------------- a card is for the audience, not for the editor ------------

def test_a_card_carrying_an_editorial_note_is_refused():
    """This shipped in a real film: 11 seconds of "Pendência de edição: o
    trecho previsto não consta na transcrição", printed on screen for the
    audience. The note belongs in the rejected list."""
    for note in ("Pendência de edição: o trecho previsto não consta na transcrição.",
                 "A verificar: falta material sobre o assunto",
                 "TODO: gravar narração deste bloco",
                 "placeholder"):
        assert longform._is_editorial_note(note), note  # noqa: SLF001


def test_a_real_card_is_not_mistaken_for_a_note():
    for card in ("Responsabilidade\nAutorização, ações e destino das descobertas",
                 "1960: o clube de ferromodelismo do MIT",
                 "O que é o Hacking",
                 "Faltam respostas — e é isso que o filme investiga"):
        assert not longform._is_editorial_note(card), card  # noqa: SLF001
