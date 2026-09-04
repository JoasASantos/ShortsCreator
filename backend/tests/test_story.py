"""Film studio: the development stages, the consistency guarantee, generation
that survives a bad shot, the resume, the cost estimate and the routes."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db, worker
from app.config import settings
from app.main import app
from app.pipeline import story
from app.schemas import QAReport

client = TestClient(app)

PREMISE = "Uma médica pega o último trem da noite carregando um segredo que mata."
INSTRUCTION = "Thriller contido, sem sangue na tela."

RAW_BIBLE = {
    "title": "O Último Trem",
    "logline": "Uma médica embarca no último trem para entregar um antídoto e "
               "descobre que o passageiro ao lado veio impedi-la.",
    "theme": "Salvar alguém custa o que você jurou nunca pagar",
    "tone": "tensão contida, silêncios longos, humor seco",
    "look": "gritty handheld 35mm, teal and amber night palette",
    "setting": "Um trem noturno interestadual, anos 1990",
    "acts": [
        {"act": 1, "name": "Partida", "summary": "Ana embarca com a maleta.",
         "turning_point": "O trem sai da estação com Vitor a bordo."},
        {"act": 2, "name": "Confronto", "summary": "Vitor a encurrala no vagão.",
         "turning_point": "A maleta desaparece."},
        {"act": 3, "name": "Resolução", "summary": "Ana escolhe quem salvar.",
         "turning_point": "Ela desce na estação errada."},
    ],
}

RAW_CHARACTERS = {
    "characters": [
        {"name": "Ana", "role": "protagonista", "personality": "teimosa e exausta",
         "visual": "woman in her 40s, cropped grey hair, deep scar across the left "
                   "eyebrow, worn olive field jacket",
         "voice": "grave, cansada"},
        {"name": "Vitor", "role": "antagonista", "personality": "frio e cortês",
         "visual": "tall man in his 60s, silver beard, round wire glasses, "
                   "charcoal three-piece suit",
         "voice": "macia, controlada"},
        # dropped: a character with no visual description is unusable as a shot
        {"name": "Figurante", "role": "figurante", "personality": "x",
         "visual": "   ", "voice": ""},
    ]
}

RAW_SCREENPLAY = {
    "scenes": [
        {"scene": 1, "act": 1, "location": "Plataforma", "time_of_day": "noite",
         "beat": "Ana embarca segundos antes das portas fecharem",
         "characters": ["Ana"], "narration": "O trem partiu às onze em ponto.",
         "dialogue": [{"character": "Ana", "line": "Ainda dá tempo."}],
         "seconds": 14},
        {"scene": 2, "act": 2, "location": "Vagão-restaurante",
         "time_of_day": "noite", "beat": "Vitor senta na frente dela",
         "characters": ["Ana", "Vitor"], "narration": "",
         "dialogue": [{"character": "Vitor", "line": "Você não deveria estar aqui."}],
         "seconds": 18},
    ]
}

RAW_SHOTS = {
    "shots": [
        {"scene": 1, "shot": 1,
         "action": "A woman steps onto an empty platform as the doors slide shut",
         "camera": "slow dolly in, low angle", "characters": ["Ana"], "seconds": 7},
        {"scene": 2, "shot": 1,
         "action": "A man in a suit walks down the swaying carriage",
         "camera": "handheld tracking", "characters": ["Vitor"], "seconds": 40},
        {"scene": 2, "shot": 2,
         "action": "Close on a ticket crumpled inside a gloved hand",
         "camera": "macro", "characters": [], "seconds": 0},
    ]
}

BY_PURPOSE = {
    "filme_biblia": RAW_BIBLE,
    "filme_personagens": RAW_CHARACTERS,
    "filme_roteiro": RAW_SCREENPLAY,
    "filme_planos": RAW_SHOTS,
}


@pytest.fixture
def fake_llm(monkeypatch):
    """Every stage answered from a canned document. Never a real LLM in a test,
    and the calls are captured so the prompts can be inspected."""
    calls: list[dict] = []

    def fake(system, prompt, schema=None, max_tokens=8000, purpose=""):
        calls.append({"system": system, "prompt": prompt, "schema": schema,
                      "purpose": purpose})
        return json.loads(json.dumps(BY_PURPOSE[purpose]))

    monkeypatch.setattr(story.llm, "complete_json", fake)
    return calls


def _options(**overrides) -> dict:
    return story.options_of({"options_json": json.dumps(overrides)})


def _film(stages=("bible", "characters", "screenplay", "shots"), **options) -> dict:
    """A film row with the requested stages already developed."""
    film_id = db.create_film(PREMISE, INSTRUCTION, "", {"scenes": 2, **options})
    docs = {
        "bible": story._normalize_bible(RAW_BIBLE),                     # noqa: SLF001
        "characters": story._normalize_characters(RAW_CHARACTERS),      # noqa: SLF001
        "screenplay": story._normalize_screenplay(RAW_SCREENPLAY),      # noqa: SLF001
    }
    for stage in stages:
        row = db.get_film(film_id)
        if stage == "shots":
            doc = story._normalize_shots(                               # noqa: SLF001
                RAW_SHOTS, docs["characters"], docs["bible"], story.options_of(row))
        else:
            doc = docs[stage]
        story.save_stage(row, stage, doc)
    return db.get_film(film_id)


# ------------------------------- the stages -------------------------------

def test_the_bible_prompt_carries_the_premise_the_instruction_and_the_language(fake_llm):
    bible = story.develop_bible(PREMISE, INSTRUCTION, _options(language="es-ES"))

    call = fake_llm[0]
    assert call["purpose"] == "filme_biblia"
    assert PREMISE in call["prompt"]
    assert INSTRUCTION in call["prompt"]
    # the language marker is replaced, never formatted: the prompt carries a
    # literal JSON example and `.format` would read its braces as placeholders
    assert "español" in call["system"]
    assert "{language}" not in call["system"]
    assert bible["logline"].startswith("Uma médica embarca")
    assert len(bible["acts"]) == 3


def test_a_bible_with_no_logline_fails_with_a_clear_reason(monkeypatch):
    monkeypatch.setattr(story.llm, "complete_json",
                        lambda *a, **k: {"title": "x", "acts": []})
    with pytest.raises(RuntimeError, match="logline"):
        story.develop_bible(PREMISE, "", _options())


def test_a_bible_with_no_acts_fails_with_a_clear_reason(monkeypatch):
    monkeypatch.setattr(story.llm, "complete_json",
                        lambda *a, **k: {"logline": "tem logline", "acts": []})
    with pytest.raises(RuntimeError, match="acts"):
        story.develop_bible(PREMISE, "", _options())


def test_the_characters_stage_keeps_only_who_can_be_filmed(fake_llm):
    characters = story.develop_characters(story._normalize_bible(RAW_BIBLE),  # noqa: SLF001
                                          "", _options())
    names = [c["name"] for c in characters["characters"]]
    assert names == ["Ana", "Vitor"], "a character with no visual is unusable"
    assert fake_llm[0]["purpose"] == "filme_personagens"
    assert all(c["voice_id"] is None for c in characters["characters"])


def test_a_cast_with_no_visual_descriptions_fails(monkeypatch):
    monkeypatch.setattr(story.llm, "complete_json", lambda *a, **k: {
        "characters": [{"name": "Ana", "visual": ""}]})
    with pytest.raises(RuntimeError, match="visual"):
        story.develop_characters({}, "", _options())


def test_the_screenplay_prompt_names_the_cast_without_their_visuals(fake_llm):
    bible = story._normalize_bible(RAW_BIBLE)                          # noqa: SLF001
    characters = story._normalize_characters(RAW_CHARACTERS)           # noqa: SLF001
    screenplay = story.write_screenplay(bible, characters, "", _options(scenes=2))

    prompt = fake_llm[0]["prompt"]
    assert "Ana" in prompt and "teimosa e exausta" in prompt
    # the physical descriptions are for the generator, not the screenwriter:
    # carried here, they come back narrated out loud in the dialogue
    assert "cropped grey hair" not in prompt
    assert len(screenplay["scenes"]) == 2
    assert screenplay["scenes"][0]["dialogue"][0]["line"] == "Ainda dá tempo."


def test_a_screenplay_with_no_scene_fails_with_a_clear_reason(monkeypatch):
    monkeypatch.setattr(story.llm, "complete_json",
                        lambda *a, **k: {"scenes": [{"scene": 1, "beat": "  "}]})
    with pytest.raises(RuntimeError, match="scenes"):
        story.write_screenplay({}, {"characters": []}, "", _options())


def test_the_shot_list_asks_for_english_and_clamps_the_shot_length(fake_llm):
    bible = story._normalize_bible(RAW_BIBLE)                          # noqa: SLF001
    characters = story._normalize_characters(RAW_CHARACTERS)           # noqa: SLF001
    screenplay = story._normalize_screenplay(RAW_SCREENPLAY)           # noqa: SLF001

    shots = story.build_shot_list(bible, characters, screenplay, "",
                                  _options(shot_seconds=8))["shots"]

    call = fake_llm[0]
    assert call["purpose"] == "filme_planos"
    assert "INGLÊS" in call["system"]
    assert str(story.MAX_SHOT_SECONDS) in call["system"]
    assert bible["look"] in call["prompt"]

    assert [s["key"] for s in shots] == ["s01_p01", "s02_p01", "s02_p02"]
    # 40s is a scene, not a shot; 0 falls back to the film's shot length
    assert shots[1]["seconds"] == story.MAX_SHOT_SECONDS
    assert shots[2]["seconds"] == 8


def test_a_shot_list_with_no_usable_shot_fails(monkeypatch):
    monkeypatch.setattr(story.llm, "complete_json",
                        lambda *a, **k: {"shots": [{"scene": 1, "action": ""}]})
    with pytest.raises(RuntimeError, match="shot"):
        story.build_shot_list({}, {"characters": []}, {"scenes": []}, "", _options())


def test_a_runaway_shot_list_is_refused_instead_of_quietly_costing_a_fortune(monkeypatch):
    many = {"shots": [{"scene": i, "shot": 1, "action": f"a shot {i}",
                       "camera": "", "characters": [], "seconds": 8}
                      for i in range(story.MAX_SHOTS + 5)]}
    monkeypatch.setattr(story.llm, "complete_json", lambda *a, **k: many)
    with pytest.raises(RuntimeError, match=str(story.MAX_SHOTS)):
        story.build_shot_list({}, {"characters": []}, {"scenes": []}, "", _options())


def test_a_malformed_answer_that_is_not_even_the_right_shape_fails(monkeypatch):
    """The provider chain hands back whatever the model said; a list where an
    object was asked for must not crash somewhere three stages later."""
    monkeypatch.setattr(story.llm, "complete_json", lambda *a, **k: {"shots": "nope"})
    with pytest.raises(RuntimeError, match="shot"):
        story.build_shot_list({}, {"characters": []}, {"scenes": []}, "", _options())


# ------------------- the consistency guarantee (the whole point) -------------

def test_every_character_in_a_shot_has_their_visual_description_verbatim():
    """The generators are stateless: the only thing keeping a face the same
    between scenes is the description being byte-for-byte identical in every
    prompt it appears in."""
    bible = story._normalize_bible(RAW_BIBLE)                          # noqa: SLF001
    characters = story._normalize_characters(RAW_CHARACTERS)           # noqa: SLF001
    shots = story._normalize_shots(RAW_SHOTS, characters, bible,       # noqa: SLF001
                                   _options())["shots"]

    ana = characters["characters"][0]["visual"]
    vitor = characters["characters"][1]["visual"]

    assert ana in shots[0]["prompt"]
    assert vitor not in shots[0]["prompt"], "Vitor is not in that shot"
    assert vitor in shots[1]["prompt"]
    assert ana not in shots[1]["prompt"]
    # every shot carries the film's photography and tone, even one with no cast
    for shot in shots:
        assert bible["look"] in shot["prompt"]
        assert bible["tone"] in shot["prompt"]
    assert ana not in shots[2]["prompt"] and vitor not in shots[2]["prompt"]


def test_the_same_character_gets_the_same_description_in_every_shot():
    bible = story._normalize_bible(RAW_BIBLE)                          # noqa: SLF001
    characters = story._normalize_characters(RAW_CHARACTERS)           # noqa: SLF001
    raw = {"shots": [
        {"scene": 1, "shot": 1, "action": "Ana boards", "camera": "wide",
         "characters": ["Ana"], "seconds": 6},
        {"scene": 9, "shot": 1, "action": "Ana sleeps", "camera": "close",
         "characters": ["ana"], "seconds": 6},
    ]}
    shots = story._normalize_shots(raw, characters, bible, _options())["shots"]  # noqa: SLF001
    ana = characters["characters"][0]["visual"]

    # the name is matched case-insensitively; the description is not paraphrased
    assert shots[0]["prompt"].count(ana) == 1
    assert shots[1]["prompt"].count(ana) == 1


def test_a_shot_list_edited_by_hand_is_recomposed_from_the_current_cast():
    film = _film()
    edited = json.loads(film["characters_json"])
    edited["characters"][0]["visual"] = "woman in her 40s, shaved head, mirrored sunglasses"
    story.save_stage(film, "characters", edited)

    film = db.get_film(film["id"])
    shots = story.accept_stage(film, "shots", RAW_SHOTS)["shots"]
    assert "shaved head" in shots[0]["prompt"]


def test_a_prompt_the_user_wrote_by_hand_is_kept_as_written():
    film = _film()
    mine = {"shots": [{"scene": 1, "shot": 1, "action": "whatever", "camera": "",
                       "characters": ["Ana"], "seconds": 6,
                       "prompt": "exactly what I typed"}]}
    shots = story.accept_stage(film, "shots", mine)["shots"]
    assert shots[0]["prompt"] == "exactly what I typed"


# ------------------------- stage order and cascade -------------------------

def test_a_stage_refuses_to_run_before_the_stage_it_is_built_on():
    film = _film(stages=("bible",))
    with pytest.raises(story.StageNotReady, match="characters"):
        story.develop_stage(film, "screenplay")

    empty = db.get_film(db.create_film(PREMISE, "", "", {}))
    with pytest.raises(story.StageNotReady, match="bible"):
        story.develop_stage(empty, "characters")


def test_rewriting_a_stage_drops_the_stages_derived_from_it():
    film = _film()
    invalidated = story.save_stage(film, "characters",
                                   json.loads(film["characters_json"]))

    assert invalidated == ["screenplay", "shots"]
    row = db.get_film(film["id"])
    assert row["screenplay_json"] is None and row["shots_json"] is None
    assert row["bible_json"] is not None, "the bible is upstream, it stays"
    assert row["status"] == "developing"


def test_finishing_the_shot_list_marks_the_film_ready_to_generate():
    film = _film()
    assert film["status"] == "ready"
    assert film["stage"] == "shots"
    assert film["title"] == RAW_BIBLE["title"]


def test_an_unknown_stage_is_rejected():
    film = _film(stages=("bible",))
    with pytest.raises(ValueError):
        story.develop_stage(film, "trailer")
    with pytest.raises(ValueError):
        story.accept_stage(film, "trailer", {})


# ------------------------------ cost estimate ------------------------------

def test_the_estimate_matches_the_shot_list(monkeypatch):
    monkeypatch.setattr(story.videogen, "plan", lambda *a, **k: [])
    film = _film()
    shots = json.loads(film["shots_json"])["shots"]
    report = story.estimate(film)

    assert report["shots"] == len(shots) == 3
    assert report["seconds"] == sum(s["seconds"] for s in shots)
    assert report["seconds"] == 7 + story.MAX_SHOT_SECONDS + 8
    assert report["scenes"] == 2
    # narration line + one line of dialogue in scene 1, one in scene 2
    assert report["narration_lines"] == 3
    # nothing generated yet: the whole list has to be paid for
    assert report["shots_to_generate"] == report["shots"]
    assert report["seconds_to_generate"] == report["seconds"]
    assert report["reused_shots"] == 0
    assert report["provider"]["configured"] is False


def test_the_estimate_names_the_provider_that_would_actually_run(monkeypatch):
    class FakeCandidate:
        provider_id, label, model = "higgsfield", "Higgsfield", "sora-2"
        cost, reason, eligible = "pago por segundo", "chave configurada", True

    monkeypatch.setattr(story.videogen, "plan", lambda *a, **k: [FakeCandidate()])
    provider = story.video_provider()
    assert provider == {"id": "higgsfield", "name": "Higgsfield", "model": "sora-2",
                        "cost": "pago por segundo", "configured": True,
                        "reason": "chave configurada"}


def test_the_estimate_survives_a_generator_registry_that_blows_up(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(story.videogen, "plan", boom)
    provider = story.video_provider()
    assert provider["configured"] is False
    assert "registry unavailable" in provider["reason"]


# ------------------------------- generation -------------------------------

@pytest.fixture
def fake_generation(monkeypatch):
    """Generation with the expensive parts replaced: AI video, TTS, FFmpeg.

    Everything the film's own logic decides — the ledger, the placeholders, the
    timeline, the job — stays real.
    """
    generated: list[str] = []

    def generate_clip(prompt, duration, out_path, aspect="9:16", log=lambda m, *_: None,
                      **kwargs):
        generated.append(prompt)
        out_path.write_bytes(b"fake-clip")
        return out_path

    def synthesize(text, out_path, voice=None, log=lambda m, level="info": None):
        out_path.write_bytes(b"fake-audio")
        return story.tts.Narration(out_path, 1.5, [])

    def gradient(duration, niche, out, scroll="nenhum"):
        out.write_bytes(b"fake-gradient")
        return out

    def render_timeline(job_dir, timeline, out, log=lambda m: None):
        out.write_bytes(b"fake-film")
        return out

    monkeypatch.setattr(story.render, "ensure_ffmpeg", lambda: None)
    monkeypatch.setattr(story.render, "probe_duration", lambda p: 5.0)
    monkeypatch.setattr(story.render, "background_gradient", gradient)
    monkeypatch.setattr(story.render, "make_thumbnail",
                        lambda video, out, at=1.0: out.write_bytes(b"jpg"))
    monkeypatch.setattr(story.videogen, "generate_clip", generate_clip)
    monkeypatch.setattr(story.tts, "synthesize", synthesize)
    monkeypatch.setattr(story.timeline_render, "render_timeline", render_timeline)
    monkeypatch.setattr(story.qa_mod, "audit",
                        lambda *a, **k: QAReport(passed=True, score=88))
    monkeypatch.setattr(story.notify, "job_done", lambda *a, **k: None)
    return generated


def test_generation_produces_a_job_a_timeline_and_a_playable_output(fake_generation):
    film = _film()
    result = story.generate(film["id"])

    row = db.get_film(film["id"])
    assert row["status"] == "done" and row["job_id"]

    job = db.get_job(row["job_id"])
    assert job["status"] == "done"
    assert json.loads(job["result_json"])["mode"] == story.MODE
    assert result["video"].endswith("short.mp4")

    # editable in the existing editor, with no special case for films
    job_dir = settings.jobs_dir / row["job_id"]
    edl = json.loads((job_dir / "timeline.json").read_text(encoding="utf-8"))
    assert len(edl["video"]) == 3
    assert len(edl["audio"]) == 3          # one clip per spoken line
    assert len(edl["captions"]) == 3
    assert (settings.outputs_dir / f"{row['job_id']}.mp4").exists()
    assert (job_dir / "captions.srt").exists()
    body = client.get(f"/api/jobs/{row['job_id']}/timeline").json()
    assert body["duration"] > 0


def test_the_films_job_never_sits_in_the_shorts_queue(fake_generation, monkeypatch):
    """A film job left in `queued`/`running` would be picked up by
    `worker.start()` after a restart and run through the shorts pipeline."""
    def broken(job_dir, timeline, out, log=lambda m: None):
        raise RuntimeError("render interrupted")

    monkeypatch.setattr(story.timeline_render, "render_timeline", broken)

    film = _film()
    with pytest.raises(RuntimeError, match="render interrupted"):
        story.generate(film["id"])

    row = db.get_film(film["id"])
    assert row["status"] == "error" and "interrupted" in row["error"]
    assert db.get_job(row["job_id"])["status"] not in ("queued", "running")
    # what was already generated stays written down, so the retry is cheap
    assert len(json.loads(row["progress_json"])) == 6


def test_one_failed_shot_does_not_lose_the_film(fake_generation, monkeypatch):
    """Twenty minutes of paid generation must not be thrown away because shot
    two came back with a provider error."""
    def flaky(prompt, duration, out_path, aspect="9:16", log=lambda m, *_: None,
              **kwargs):
        if "swaying carriage" in prompt:
            raise RuntimeError("provider returned 500")
        out_path.write_bytes(b"fake-clip")
        return out_path

    monkeypatch.setattr(story.videogen, "generate_clip", flaky)

    film = _film()
    story.generate(film["id"])

    row = db.get_film(film["id"])
    assert row["status"] == "done", "the film is still assembled"
    ledger = {e["key"]: e for e in json.loads(row["progress_json"])}
    assert ledger["s02_p01"]["status"] == "placeholder"
    assert "500" in ledger["s02_p01"]["error"]
    assert ledger["s01_p01"]["status"] == "ok"
    assert ledger["s02_p02"]["status"] == "ok"

    # the placeholder holds the slot, so the timeline keeps its timing
    job_dir = settings.jobs_dir / row["job_id"]
    assert (job_dir / ledger["s02_p01"]["file"]).exists()
    edl = json.loads((job_dir / "timeline.json").read_text(encoding="utf-8"))
    assert len(edl["video"]) == 3


def test_a_missing_generator_is_reported_once_instead_of_per_shot(fake_generation,
                                                                  monkeypatch):
    calls: list[str] = []

    def unconfigured(prompt, duration, out_path, aspect="9:16",
                     log=lambda m, *_: None, **kwargs):
        calls.append(prompt)
        raise story.videogen.VideoGenNotConfigured("no generator configured")

    monkeypatch.setattr(story.videogen, "generate_clip", unconfigured)

    film = _film()
    story.generate(film["id"])

    assert len(calls) == 1, "the same failure is not asked for once per shot"
    ledger = json.loads(db.get_film(film["id"])["progress_json"])
    shots = [e for e in ledger if e["kind"] == "shot"]
    assert all(e["status"] == "placeholder" for e in shots)


def test_a_line_that_fails_to_record_stays_as_a_caption(fake_generation, monkeypatch):
    def mute(text, out_path, voice=None, log=lambda m, level="info": None):
        raise RuntimeError("tts provider out of credit")

    monkeypatch.setattr(story.tts, "synthesize", mute)

    film = _film()
    story.generate(film["id"])

    row = db.get_film(film["id"])
    assert row["status"] == "done"
    edl = json.loads((settings.jobs_dir / row["job_id"] / "timeline.json")
                     .read_text(encoding="utf-8"))
    assert edl["audio"] == []
    assert len(edl["captions"]) == 3, "the lines are still readable on screen"


def test_a_resume_does_not_regenerate_a_shot_that_already_succeeded(fake_generation):
    film = _film()
    story.generate(film["id"])
    assert len(fake_generation) == 3

    fake_generation.clear()
    story.generate(db.get_film(film["id"])["id"])
    assert fake_generation == [], "everything was already on disk"


def test_a_resume_retries_a_placeholder_and_a_changed_prompt(fake_generation,
                                                             monkeypatch):
    """A placeholder is a failure marker, not a result — and a shot whose
    prompt changed is a different shot."""
    failing = {"on": True}
    calls: list[str] = []

    def flaky(prompt, duration, out_path, aspect="9:16", log=lambda m, *_: None,
              **kwargs):
        calls.append(prompt)
        if failing["on"] and "swaying carriage" in prompt:
            raise RuntimeError("provider returned 500")
        out_path.write_bytes(b"fake-clip")
        return out_path

    monkeypatch.setattr(story.videogen, "generate_clip", flaky)
    film = _film()
    story.generate(film["id"])
    failing["on"] = False
    calls.clear()

    # scene 2's second shot is rewritten: only it and the placeholder are paid for
    row = db.get_film(film["id"])
    shots = json.loads(row["shots_json"])
    shots["shots"][2]["action"] = "Close on a ticket torn in half"
    shots["shots"][2].pop("prompt")
    story.save_stage(row, "shots", story.accept_stage(row, "shots", shots))

    story.generate(film["id"])
    assert len(calls) == 2
    assert any("swaying carriage" in prompt for prompt in calls)
    assert any("torn in half" in prompt for prompt in calls)


def test_a_shot_cut_from_the_list_stops_being_reported(fake_generation):
    film = _film()
    story.generate(film["id"])

    row = db.get_film(film["id"])
    shots = json.loads(row["shots_json"])
    dropped = shots["shots"].pop(1)["key"]
    story.save_stage(row, "shots", story.accept_stage(row, "shots", shots))
    story.generate(film["id"])

    ledger = json.loads(db.get_film(film["id"])["progress_json"])
    assert dropped not in [entry["key"] for entry in ledger]
    assert len([e for e in ledger if e["kind"] == "shot"]) == 2


def test_the_estimate_stops_charging_for_shots_already_on_disk(fake_generation,
                                                               monkeypatch):
    monkeypatch.setattr(story.videogen, "plan", lambda *a, **k: [])
    film = _film()
    story.generate(film["id"])

    report = story.estimate(db.get_film(film["id"]))
    assert report["reused_shots"] == 3
    assert report["shots_to_generate"] == 0
    assert report["seconds_to_generate"] == 0


def test_generating_without_a_shot_list_says_so():
    film = _film(stages=("bible", "characters", "screenplay"))
    with pytest.raises(story.StageNotReady, match="shot list"):
        story.generate(film["id"])


# ------------------------------- the timeline -------------------------------

def test_a_scene_whose_dialogue_outlasts_its_footage_holds_the_last_shot():
    """Otherwise the picture cuts to black while the line is still being
    spoken. The renderer loops a clip whose out_point runs past its source."""
    film = _film()
    shots = json.loads(film["shots_json"])["shots"]
    screenplay = json.loads(film["screenplay_json"])
    work = settings.job_dir("film_timeline_probe")
    ledger = {}
    for shot in shots:
        path = work / f"shot_{shot['key']}.mp4"
        path.write_bytes(b"x")
        ledger[shot["key"]] = {"kind": "shot", "key": shot["key"], "status": "ok",
                               "file": path.name, "seconds": 2.0,
                               "hash": story._hash(shot["prompt"])}  # noqa: SLF001
    for scene in screenplay["scenes"]:
        for line in story.scene_lines(scene):
            path = work / f"line_{line['key']}.mp3"
            path.write_bytes(b"x")
            ledger[line["key"]] = {"kind": "line", "key": line["key"],
                                   "status": "ok", "file": path.name,
                                   "seconds": 6.0,
                                   "hash": story._hash(line["text"])}  # noqa: SLF001

    edl = story.build_timeline(work, screenplay, shots, ledger,
                               story.options_of(film))

    # scene 1: 2s of footage against 12s of dialogue — the shot is held
    assert edl.video[0].duration == pytest.approx(12.0, abs=0.05)
    # no hole between the scenes
    assert edl.video[1].start == pytest.approx(12.0, abs=0.05)
    # scene 2: 4s of footage, one 6s line -> the last shot covers the rest
    assert edl.video[2].duration == pytest.approx(4.0, abs=0.05)
    assert edl.duration == pytest.approx(18.0, abs=0.05)


def test_scene_lines_puts_the_narration_before_the_dialogue():
    lines = story.scene_lines(RAW_SCREENPLAY["scenes"][0])
    assert [line["key"] for line in lines] == ["s01_l00", "s01_l01"]
    assert lines[0]["text"].startswith("O trem partiu")
    assert lines[1]["character"] == "Ana"
    # a scene with nothing spoken produces no lines at all
    assert story.scene_lines({"scene": 3, "narration": "", "dialogue": []}) == []


# --------------------------------- routes ---------------------------------

@pytest.fixture
def no_queue(monkeypatch):
    """The film is queued but not generated: the route is what is under test."""
    queued: list[str] = []
    monkeypatch.setattr(worker, "enqueue_film", lambda f: queued.append(f))
    return queued


def test_creating_a_film_develops_the_bible_and_persists_the_project(fake_llm):
    r = client.post("/api/films", json={"premise": PREMISE,
                                        "instruction": INSTRUCTION, "scenes": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bible"]["logline"].startswith("Uma médica")
    assert body["title"] == RAW_BIBLE["title"]
    assert body["stage"] == "bible" and body["status"] == "developing"
    # the json columns are serialized, never leaked raw
    assert "bible_json" not in body
    assert body["options"]["scenes"] == 3
    assert db.get_film(body["id"])["premise"] == PREMISE


def test_creating_a_film_without_a_real_premise_returns_400(fake_llm):
    assert client.post("/api/films", json={"premise": "curto"}).status_code == 400
    assert client.post("/api/films", json={"premise": "   "}).status_code == 400
    assert fake_llm == [], "no LLM call before the input is valid"


def test_creating_a_film_outside_the_limits_returns_400(fake_llm):
    assert client.post("/api/films", json={"premise": PREMISE,
                                           "scenes": 99}).status_code == 400
    assert client.post("/api/films", json={"premise": PREMISE,
                                           "shot_seconds": 60}).status_code == 400


def test_a_model_failure_while_creating_leaves_a_film_to_retry(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("every model in the chain failed")

    monkeypatch.setattr(story.llm, "complete_json", boom)
    r = client.post("/api/films", json={"premise": PREMISE})
    assert r.status_code == 502
    assert "every model in the chain failed" in r.json()["detail"]

    row = db.list_films()[0]
    assert row["status"] == "error" and "chain" in row["error"]


def test_unknown_film_returns_404():
    assert client.get("/api/films/film_inexistente").status_code == 404
    assert client.post("/api/films/film_x/stage/bible").status_code == 404
    assert client.put("/api/films/film_x/bible", json={}).status_code == 404
    assert client.post("/api/films/film_x/generate").status_code == 404


def test_the_poll_brings_every_stage_and_the_estimate(monkeypatch):
    monkeypatch.setattr(story.videogen, "plan", lambda *a, **k: [])
    film = _film()
    body = client.get(f"/api/films/{film['id']}").json()

    assert body["status"] == "ready"
    assert {"bible", "characters", "screenplay", "shots", "options", "progress",
            "estimate"} <= body.keys()
    assert body["progress"] is None            # nothing generated yet
    assert body["estimate"]["shots"] == 3
    assert body["shots"]["shots"][0]["prompt"]
    assert body["bible"]["look"] in body["shots"]["shots"][0]["prompt"]

    listed = client.get("/api/films").json()
    assert [f["id"] for f in listed] == [film["id"]]
    assert "estimate" not in listed[0]         # the listing stays cheap


def test_regenerating_a_stage_reports_what_it_invalidated(fake_llm):
    film = _film()
    r = client.post(f"/api/films/{film['id']}/stage/characters",
                    json={"instruction": "deixe o antagonista mais frio"})
    assert r.status_code == 200
    body = r.json()
    assert body["invalidated"] == ["screenplay", "shots"]
    assert [c["name"] for c in body["characters"]["characters"]] == ["Ana", "Vitor"]
    assert "deixe o antagonista mais frio" in fake_llm[0]["prompt"]


def test_regenerating_a_stage_out_of_order_returns_400(fake_llm):
    film = _film(stages=("bible",))
    r = client.post(f"/api/films/{film['id']}/stage/shots")
    assert r.status_code == 400
    assert "characters" in r.json()["detail"]
    assert fake_llm == []


def test_an_unknown_stage_returns_400_listing_the_real_ones():
    film = _film(stages=("bible",))
    r = client.post(f"/api/films/{film['id']}/stage/trailer")
    assert r.status_code == 400
    assert "screenplay" in r.json()["detail"]
    assert client.put(f"/api/films/{film['id']}/trailer",
                      json={}).status_code == 400


def test_a_model_failure_in_a_stage_returns_502_with_the_real_reason(monkeypatch):
    film = _film(stages=("bible",))
    monkeypatch.setattr(story.llm, "complete_json",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("codex CLI went over 420s")))
    r = client.post(f"/api/films/{film['id']}/stage/characters")
    assert r.status_code == 502
    assert "420s" in r.json()["detail"]


def test_saving_a_stage_by_hand_normalizes_it():
    film = _film(stages=("bible",))
    mine = dict(RAW_CHARACTERS)
    r = client.put(f"/api/films/{film['id']}/characters", json=mine)
    assert r.status_code == 200
    names = [c["name"] for c in r.json()["characters"]["characters"]]
    assert names == ["Ana", "Vitor"], "the unusable character is dropped"
    assert json.loads(db.get_film(film["id"])["characters_json"])["characters"]


def test_saving_a_stage_that_cannot_be_used_returns_400():
    film = _film(stages=("bible",))
    r = client.put(f"/api/films/{film['id']}/characters",
                   json={"characters": [{"name": "Ana", "visual": ""}]})
    assert r.status_code == 400
    assert "visual" in r.json()["detail"]
    assert client.put(f"/api/films/{film['id']}/bible",
                      json={"acts": []}).status_code == 400


def test_generating_answers_with_the_estimate_and_starts_nothing(no_queue, monkeypatch):
    monkeypatch.setattr(story.videogen, "plan", lambda *a, **k: [])
    film = _film()
    body = client.post(f"/api/films/{film['id']}/generate", json={}).json()

    assert body["queued"] is False
    assert body["estimate"]["shots"] == 3
    assert body["estimate"]["seconds"] == 27
    assert no_queue == [], "nothing paid for before the caller saw the estimate"


def test_generating_without_a_configured_provider_says_what_to_do(no_queue, monkeypatch):
    monkeypatch.setattr(story.videogen, "plan", lambda *a, **k: [])
    film = _film()
    r = client.post(f"/api/films/{film['id']}/generate", json={"confirm": True})
    assert r.status_code == 400
    assert "placeholder_ok" in r.json()["detail"]
    assert no_queue == []

    r = client.post(f"/api/films/{film['id']}/generate",
                    json={"confirm": True, "placeholder_ok": True})
    assert r.status_code == 200 and r.json()["queued"] is True
    assert no_queue == [film["id"]]


def test_generating_a_film_with_no_shot_list_returns_400(no_queue):
    film = _film(stages=("bible", "characters", "screenplay"))
    r = client.post(f"/api/films/{film['id']}/generate", json={"confirm": True})
    assert r.status_code == 400
    assert "shot list" in r.json()["detail"]
    assert no_queue == []


def test_generating_a_film_that_is_already_running_returns_400(no_queue):
    film = _film()
    db.update_film(film["id"], status="generating")
    r = client.post(f"/api/films/{film['id']}/generate", json={"confirm": True})
    assert r.status_code == 400
    assert no_queue == []


def test_deleting_a_film_removes_the_project():
    film = _film()
    assert client.delete(f"/api/films/{film['id']}").status_code == 200
    assert db.get_film(film["id"]) is None


def test_a_film_goes_on_its_own_queue_and_not_on_the_shorts_one():
    """A film waits behind other films, never in front of a short that is ready
    to render — and queueing it clears the error from the previous attempt."""
    film = _film()
    db.update_film(film["id"], status="error", error="provider was down")
    shorts_waiting = worker.queue_size()

    worker.enqueue_film(film["id"])

    assert worker._film_queue.get_nowait() == film["id"]   # noqa: SLF001
    assert worker.queue_size() == shorts_waiting
    row = db.get_film(film["id"])
    assert row["status"] == "queued" and row["error"] is None
