"""How fast the narration is written and spoken.

A short that has to be read without breathing to fit its duration sounds
rushed, and the two halves of that are set in different places: the script is
written to a words-per-second budget, and the voice reads at its own rate.
They have to agree, or the script overshoots and the delivery races to catch up.
"""
from __future__ import annotations

from app.config import settings
from app.pipeline import script as script_mod
from app.schemas import JobInput, ScriptSegment, ShortScript


# --------------------------------------------------------- the written pace

def test_the_script_is_written_to_the_measured_delivery_rate():
    """The budget is not a taste: it is what edge-tts actually delivers at the
    house rate on a script written under the sentence rule, pauses included —
    measured at 1.94 words a second. The old 2.6 was the speaking rate with the
    pauses forgotten, so every script ran past the duration it was written
    for."""
    assert 1.85 <= script_mod.WORDS_PER_SECOND <= 2.1


def test_the_word_budget_follows_the_target_duration(monkeypatch):
    """The model is told how many words fit, and that number has to come from
    the same constant the delivery was calibrated against."""
    seen = {}

    def fake(system, prompt, schema=None, max_tokens=8000, purpose=""):
        seen["prompt"] = prompt
        return {"title": "t", "description": "d", "hashtags": [],
                "estimated_seconds": 45,
                "segments": [{"kind": "hook", "text": "Uma frase.",
                              "broll_query": "x", "on_screen": "y"}]}

    from app.pipeline import llm
    monkeypatch.setattr(llm, "complete_json", fake)

    job = JobInput(source_type="tema", source="um tema", duration=45)
    script_mod.build_script(job, _material())

    expected = int(45 * script_mod.WORDS_PER_SECOND)
    assert f"~{expected} palavras" in seen["prompt"]


def test_the_rules_ask_for_one_idea_per_sentence():
    """Clause-stacked lines are what read as breathless — the pauses between
    sentences are generous, but there are none inside one."""
    rules = script_mod.BASE_RULES
    assert "UMA IDEIA POR FRASE" in rules
    assert "16 palavras" in rules
    # and it has to say why, or the model treats it as a style preference
    assert "atropelada" in rules or "respirar" in rules


def test_the_density_reads_as_portuguese():
    """The prompt is in Portuguese; a decimal point in the middle of it reads
    as a typo to the model."""
    written = str(script_mod.WORDS_PER_SECOND).replace(".", ",")
    assert f"{written} palavras por segundo" in script_mod.BASE_RULES
    assert "2.0 palavras" not in script_mod.BASE_RULES


# --------------------------------------------------------- the spoken pace

def test_the_house_rate_is_slower_than_the_provider_default():
    """The neural voices read a sentence at about 2.9 words a second at +0%,
    which is quick enough that a long line arrives as one run."""
    rate = settings.narration_rate
    assert rate.startswith("-"), f"expected a slowdown, got {rate!r}"
    assert 3 <= int(rate.strip("-%")) <= 20, "a big change would sound broken"


def test_a_voice_with_its_own_rate_keeps_it(monkeypatch):
    """Someone who registered a voice at a deliberate speed set it for a
    reason; the house default is only for voices that never said."""
    seen = {}

    class _Comm:
        def __init__(self, text, voice, rate="+0%", pitch="+0Hz", **kw):
            seen["rate"] = rate

        async def stream(self):
            return
            yield  # pragma: no cover - never reached, keeps this a generator

    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", _Comm)

    from app.pipeline import tts
    for voice, expected in (({"rate": "-20%"}, "-20%"),
                            ({}, settings.narration_rate),
                            ({"rate": ""}, settings.narration_rate)):
        try:
            tts._edge("uma frase", _tmp(), voice, lambda *a, **k: None)  # noqa: SLF001
        except Exception:
            pass  # the fake stream yields nothing; only the rate matters here
        assert seen["rate"] == expected, f"voice={voice}"


def _material():
    from app.pipeline.ingest import SourceMaterial
    return SourceMaterial(kind="tema", title="um tema", text="um tema")


def _tmp():
    import tempfile
    from pathlib import Path
    return Path(tempfile.mkdtemp()) / "n.mp3"


# ------------------------------------------------------- the two agreeing

def test_a_script_written_to_budget_lands_near_its_target():
    """The point of keeping the two numbers in step: 45 seconds of script,
    read at the house rate, comes out around 45 seconds — not 53, which is
    what a budget calibrated on the old dense sentences produced once the
    sentences got shorter and each one bought its own pause."""
    # measured end to end: 103 words of a real script took 53.0s at the house
    # rate, under the sentence rule
    DELIVERED = 103 / 53.0
    for duration in (20, 45, 60):
        words = int(duration * script_mod.WORDS_PER_SECOND)
        spoken = words / DELIVERED
        assert abs(spoken - duration) < duration * 0.08, (
            f"{words} words would take {spoken:.1f}s against a {duration}s target")


def test_full_narration_keeps_every_segment():
    """Joining is where a segment could quietly go missing."""
    short = ShortScript(
        title="t", description="d", hashtags=[], estimated_seconds=20,
        segments=[ScriptSegment(kind="hook", text="Primeira."),
                  ScriptSegment(kind="corpo", text="Segunda."),
                  ScriptSegment(kind="cta", text="Terceira.")])
    spoken = script_mod.full_narration(short)
    assert spoken == "Primeira. Segunda. Terceira."
