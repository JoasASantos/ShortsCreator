"""Model chain: ordering, fallback and telemetry.

No test here calls a real model — the dispatch is swapped for functions that
either answer or blow up on demand, which is exactly what the chain has to
tell apart.
"""
from __future__ import annotations

import pytest

from app import db
from app.config import _parse_chain
from app.pipeline import llm


def test_parse_chain_with_and_without_a_model():
    assert _parse_chain("claude_cli:claude-fable-5-1,codex_cli") == [
        ("claude_cli", "claude-fable-5-1"), ("codex_cli", "")]


def test_parse_chain_ignores_blanks_and_whitespace():
    assert _parse_chain(" a:1 , , b:2 ,") == [("a", "1"), ("b", "2")]
    assert _parse_chain("") == []


def test_chain_uses_the_first_link_that_answers(monkeypatch):
    called: list[tuple[str, str | None]] = []

    def fake(provider, model, system, prompt, schema, max_tokens):
        called.append((provider, model))
        return {"ok": provider}

    monkeypatch.setattr(llm, "_dispatch_raw", fake)
    monkeypatch.setattr(llm.settings, "llm_provider", "chain")
    monkeypatch.setattr(llm, "active_chain",
                        lambda: [("claude_cli", "claude-fable-5-1"),
                                 ("codex_cli", "gpt-5.6-sol")])
    monkeypatch.setattr(llm, "_unavailable_reason", lambda p, m: "")

    assert llm.complete_json("s", "p") == {"ok": "claude_cli"}
    assert called == [("claude_cli", "claude-fable-5-1")], "it should not have tried the second link"


def test_chain_falls_through_to_the_next_link_when_the_first_fails(monkeypatch):
    called: list[str] = []

    def fake(provider, model, system, prompt, schema, max_tokens):
        called.append(provider)
        if provider == "claude_cli":
            raise llm.LLMError("subscription limit reached")
        return {"ok": provider}

    monkeypatch.setattr(llm, "_dispatch_raw", fake)
    monkeypatch.setattr(llm.settings, "llm_provider", "chain")
    monkeypatch.setattr(llm, "active_chain",
                        lambda: [("claude_cli", "claude-fable-5-1"),
                                 ("claude_cli", "claude-opus-5"),
                                 ("codex_cli", "gpt-5.6-sol")])
    monkeypatch.setattr(llm, "_unavailable_reason", lambda p, m: "")

    assert llm.complete_json("s", "p") == {"ok": "codex_cli"}
    assert called == ["claude_cli", "claude_cli", "codex_cli"]


def test_chain_with_every_link_failing_reports_the_last_error(monkeypatch):
    def fake(provider, model, system, prompt, schema, max_tokens):
        raise llm.LLMError(f"died at {provider}")

    monkeypatch.setattr(llm, "_dispatch_raw", fake)
    monkeypatch.setattr(llm.settings, "llm_provider", "chain")
    monkeypatch.setattr(llm, "active_chain", lambda: [("a", ""), ("b", "")])
    monkeypatch.setattr(llm, "_unavailable_reason", lambda p, m: "")

    with pytest.raises(llm.LLMError, match="died at b"):
        llm.complete_json("s", "p")


def test_an_empty_chain_is_a_configuration_error(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "chain")
    monkeypatch.setattr(llm, "active_chain", lambda: [])
    with pytest.raises(llm.LLMError, match="LLM_MODEL"):
        llm.complete_json("s", "p")


def test_a_direct_provider_does_not_go_through_the_chain(monkeypatch):
    monkeypatch.setattr(llm, "_dispatch_raw",
                        lambda p, m, *a: {"provider": p, "model": m})
    monkeypatch.setattr(llm.settings, "llm_provider", "claude_cli")
    monkeypatch.setattr(llm.settings, "claude_cli_model", "claude-fable-5-1")
    # model None: the provider resolves it from .env, the way it always has
    assert llm.complete_json("s", "p") == {"provider": "claude_cli", "model": None}


def test_telemetry_records_both_success_and_failure(monkeypatch):
    def fake(provider, model, system, prompt, schema, max_tokens):
        if provider == "claude_cli":
            raise llm.LLMError("rate limited")
        return {"ok": True}

    monkeypatch.setattr(llm, "_dispatch_raw", fake)
    monkeypatch.setattr(llm.settings, "llm_provider", "chain")
    monkeypatch.setattr(llm, "active_chain",
                        lambda: [("claude_cli", "claude-fable-5-1"),
                                 ("codex_cli", "gpt-5.6-sol")])
    monkeypatch.setattr(llm, "_unavailable_reason", lambda p, m: "")

    llm.current_job.set("job_teste")
    llm.complete_json("s", "p", purpose="roteiro")

    calls = db.llm_calls_for_job("job_teste")
    assert len(calls) == 2
    assert [c["provider"] for c in calls] == ["claude_cli", "codex_cli"]
    assert calls[0]["ok"] == 0 and "rate limited" in calls[0]["error"]
    assert calls[1]["ok"] == 1
    assert all(c["purpose"] == "roteiro" for c in calls)


def test_extract_json_tolerates_fences_and_surrounding_text():
    assert llm._extract_json('```json\n{"a": 1}\n```') == {"a": 1}   # noqa: SLF001
    assert llm._extract_json('blah blah {"a": 2} the end') == {"a": 2}   # noqa: SLF001
    with pytest.raises(llm.LLMError):
        llm._extract_json("no json in here")                          # noqa: SLF001
