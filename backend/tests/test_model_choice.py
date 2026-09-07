"""Choosing a model, and never being left without one.

The promise this file guards is narrow and absolute: whatever is chosen, and
whatever is broken, the pipeline still gets an answer as long as one model on
the machine can give one. Everything else here — the presets, the ordering,
the reporting — exists to serve that.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import FALLBACK_ORDER, MODEL_PRESETS, build_chain, is_legacy_chain
from app.pipeline import llm


@pytest.fixture()
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_choice():
    db.clear_setting(llm.SETTING_KEY)
    llm._MODEL_CACHE.clear()  # noqa: SLF001
    yield
    db.clear_setting(llm.SETTING_KEY)
    llm._MODEL_CACHE.clear()  # noqa: SLF001


# ------------------------------------------------------- one name, a chain

def test_choosing_a_model_puts_it_first():
    for name in FALLBACK_ORDER:
        provider, model, _ = MODEL_PRESETS[name]
        chain = build_chain(name, "")
        assert chain[0] == (provider, model), f"{name} should lead its own chain"


def test_a_choice_never_leaves_the_chain_one_link_long():
    """The point of deriving the chain: picking a model cannot be the thing
    that removes the fallback."""
    for name in FALLBACK_ORDER:
        assert len(build_chain(name, "")) >= 3


def test_every_model_appears_exactly_once():
    """A duplicated link wastes an attempt on a quota that just refused."""
    for name in FALLBACK_ORDER:
        chain = build_chain(name, "")
        assert len(chain) == len(set(chain)), f"{name} produced a duplicate"


def test_the_order_alternates_between_the_two_subscriptions():
    """A subscription runs out per account, so the link after a spent quota
    has to be on the other one — falling from Astra to Sol would ask the same
    exhausted ChatGPT account again."""
    chain = build_chain("astra", "")
    assert chain[0][0] == "codex_cli"
    assert chain[1][0] == "claude_cli", "the first fallback must change account"


def test_an_explicit_provider_model_still_keeps_its_fallbacks():
    """Naming something the presets do not cover must not cost the safety net."""
    chain = build_chain("claude_cli:some-new-model", "")
    assert chain[0] == ("claude_cli", "some-new-model")
    assert len(chain) > 1


def test_an_unknown_name_still_produces_a_working_chain():
    """A typo in LLM_MODEL must not leave the pipeline with nothing to call."""
    chain = build_chain("gpt-nonexistent-9", "")
    assert len(chain) == len(FALLBACK_ORDER)


# --------------------------------------------------------- the escape hatch

def test_an_explicit_chain_wins():
    chain = build_chain("astra", "ollama:llama3.1,anthropic:claude-opus-5")
    assert chain == [("ollama", "llama3.1"), ("anthropic", "claude-opus-5")]


def test_the_old_shipped_default_does_not_outrank_a_choice():
    """An .env carrying the chain this project used to ship was never a
    decision — it was the default written out. Letting it win would mean
    picking a model in the interface and silently getting the old one."""
    legacy = ("claude_cli:claude-fable-5-1,claude_cli:claude-opus-5,"
              "codex_cli:gpt-5.6-sol")
    assert is_legacy_chain(legacy)
    assert build_chain("astra", legacy)[0] == ("codex_cli", "gpt-6-astra")


def test_a_customised_chain_is_not_mistaken_for_the_legacy_one():
    assert not is_legacy_chain("claude_cli:claude-opus-5")
    assert not is_legacy_chain(
        "claude_cli:claude-fable-5-1,codex_cli:gpt-5.6-sol")


# ------------------------------------------------ loading the right model

def test_a_missing_cli_is_skipped_with_a_reason(monkeypatch):
    # Truly missing means neither on PATH nor where a CLI installs itself:
    # faking only `which` leaves the fallbacks free to find the real binary,
    # which is precisely the case this test used to miss.
    monkeypatch.setattr(llm, "find_binary", lambda b: None)
    reason = llm._unavailable_reason("codex_cli", "gpt-6-astra")  # noqa: SLF001
    assert "not installed" in reason


def test_a_cli_that_cannot_list_is_trusted_rather_than_skipped(monkeypatch):
    """Regression, and the expensive kind: `claude models` is not a
    subcommand, so the CLI reads it as a prompt, spends a real request, and
    answers in prose. Parsing that produced a set of ordinary words with no
    model id in it, which then excluded every working Claude model and took
    the whole chain down."""
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/" + b)
    assert llm.available_models("claude_cli") is None
    assert llm._unavailable_reason("claude_cli", "claude-fable-5-1") == ""  # noqa: SLF001


def test_a_listing_without_this_providers_models_is_not_trusted(monkeypatch):
    """Same failure from the other side: if the listing comes back as prose or
    an error page, it must not be used to exclude anything."""
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/" + b)

    class _Proc:
        returncode = 0
        stdout = "Here is an explanation of what models are, in words."

    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: _Proc())
    assert llm.available_models("codex_cli") is None


def test_a_real_listing_excludes_what_is_not_in_it(monkeypatch):
    """The check earning its keep: a model the CLI does not offer is skipped
    before the call, instead of being sent and silently swapped for the
    default — which would put a model nobody chose behind the short."""
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/" + b)

    class _Proc:
        returncode = 0
        stdout = "gpt-5.6-sol\ngpt-5-codex\n"

    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: _Proc())
    assert llm._unavailable_reason("codex_cli", "gpt-5.6-sol") == ""  # noqa: SLF001
    assert "not among" in llm._unavailable_reason("codex_cli", "gpt-6-astra")  # noqa: SLF001


def test_verification_can_be_turned_off(monkeypatch):
    """An escape hatch for a CLI whose listing we read wrong: trying and
    failing is recoverable, refusing to try is not."""
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setattr(llm.settings, "llm_verify_model", False)
    assert llm._unavailable_reason("codex_cli", "anything-at-all") == ""  # noqa: SLF001


# ---------------------------------------------------- falling through

def _chain(monkeypatch, steps):
    monkeypatch.setattr(llm, "active_chain", lambda: steps)
    monkeypatch.setattr(llm, "_unavailable_reason", lambda p, m: "")


def test_the_next_model_answers_when_the_first_runs_out(monkeypatch):
    """The behaviour the whole feature exists for: a spent quota is not an
    outage."""
    _chain(monkeypatch, [("codex_cli", "gpt-6-astra"),
                         ("claude_cli", "claude-fable-5-1")])

    def dispatch(provider, model, *a, **k):
        if model == "gpt-6-astra":
            raise RuntimeError("usage limit reached")
        return {"ok": True, "by": model}

    monkeypatch.setattr(llm, "_dispatch", dispatch)
    assert llm._chain_json("s", "p", None, 100)["by"] == "claude-fable-5-1"  # noqa: SLF001


def test_a_skipped_model_does_not_stop_the_chain(monkeypatch):
    monkeypatch.setattr(llm, "active_chain",
                        lambda: [("codex_cli", "gpt-6-astra"),
                                 ("claude_cli", "claude-fable-5-1")])
    monkeypatch.setattr(llm, "_unavailable_reason",
                        lambda p, m: "`codex` is not installed"
                        if p == "codex_cli" else "")
    monkeypatch.setattr(llm, "_dispatch",
                        lambda p, m, *a, **k: {"by": m})
    assert llm._chain_json("s", "p", None, 100)["by"] == "claude-fable-5-1"  # noqa: SLF001


def test_falling_back_is_reported_not_swallowed(monkeypatch):
    """Silently dropping to the fallback is how someone measures a model for a
    week without noticing their primary never once answered."""
    said: list[tuple[str, str]] = []
    _chain(monkeypatch, [("codex_cli", "gpt-6-astra"),
                         ("claude_cli", "claude-fable-5-1")])
    monkeypatch.setattr(llm, "_log_event", lambda m, level="info": said.append((level, m)))

    def dispatch(provider, model, *a, **k):
        if model == "gpt-6-astra":
            raise RuntimeError("usage limit reached")
        return {"ok": True}

    monkeypatch.setattr(llm, "_dispatch", dispatch)
    llm._chain_json("s", "p", None, 100)  # noqa: SLF001

    assert any(level == "warn" and "gpt-6-astra" in m for level, m in said)
    assert any("usage limit reached" in m for _, m in said)
    assert any("answered by" in m for _, m in said)


def test_everything_failing_says_what_was_tried(monkeypatch):
    """When there is genuinely nothing left, the error has to name every model
    and why each one could not — otherwise it is one opaque line."""
    _chain(monkeypatch, [("codex_cli", "gpt-6-astra"),
                         ("claude_cli", "claude-fable-5-1")])

    def dispatch(provider, model, *a, **k):
        raise RuntimeError(f"{model} is out of quota")

    monkeypatch.setattr(llm, "_dispatch", dispatch)
    with pytest.raises(llm.LLMError) as caught:
        llm._chain_json("s", "p", None, 100)  # noqa: SLF001

    message = str(caught.value)
    assert "gpt-6-astra" in message and "claude-fable-5-1" in message
    assert "out of quota" in message


# ----------------------------------------------------------- the routes

def test_the_route_reports_the_chain_and_where_the_choice_came_from(client):
    body = client.get("/api/models").json()
    assert body["source"] == "env"
    assert len(body["chain"]) >= 3
    assert {m["name"] for m in body["models"]} == set(FALLBACK_ORDER)


def test_choosing_through_the_interface_takes_effect_without_a_restart(client):
    answer = client.put("/api/models", json={"model": "fable"})
    assert answer.status_code == 200
    body = answer.json()
    assert body["chosen"] == "fable"
    assert body["source"] == "interface"
    assert body["chain"][0]["model"] == "claude-fable-5-1"
    # and the pipeline agrees, not just the response
    assert llm.active_chain()[0] == ("claude_cli", "claude-fable-5-1")


def test_an_unknown_model_is_refused_with_the_list(client):
    answer = client.put("/api/models", json={"model": "gpt-99"})
    assert answer.status_code == 400
    detail = answer.json()["detail"]
    assert "astra" in detail and "fable" in detail


def test_the_choice_can_be_handed_back_to_the_env(client):
    client.put("/api/models", json={"model": "opus"})
    body = client.delete("/api/models").json()
    assert body["source"] == "env"


def test_refresh_forgets_what_the_clis_said(client, monkeypatch):
    """`gpt-6-astra` is a staged rollout: a model enrolled after the server
    started would otherwise stay invisible until a restart."""
    llm._MODEL_CACHE["codex_cli"] = {"gpt-5.6-sol"}  # noqa: SLF001
    client.post("/api/models/refresh")
    assert "codex_cli" not in llm._MODEL_CACHE or llm._MODEL_CACHE["codex_cli"] != {"gpt-5.6-sol"}  # noqa: SLF001
