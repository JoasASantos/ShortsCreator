"""Cadeia de modelos: ordem, fallback e telemetria.

Nenhum teste aqui chama modelo de verdade — o dispatch é substituído por
funções que respondem ou explodem sob demanda, que é exatamente o que a
cadeia precisa distinguir.
"""
from __future__ import annotations

import pytest

from app import db
from app.config import _parse_chain
from app.pipeline import llm


def test_parse_chain_provider_com_e_sem_modelo():
    assert _parse_chain("claude_cli:claude-fable-5-1,codex_cli") == [
        ("claude_cli", "claude-fable-5-1"), ("codex_cli", "")]


def test_parse_chain_ignora_vazios_e_espacos():
    assert _parse_chain(" a:1 , , b:2 ,") == [("a", "1"), ("b", "2")]
    assert _parse_chain("") == []


def test_chain_usa_o_primeiro_que_responde(monkeypatch):
    chamados: list[tuple[str, str | None]] = []

    def fake(provider, model, system, prompt, schema, max_tokens):
        chamados.append((provider, model))
        return {"ok": provider}

    monkeypatch.setattr(llm, "_dispatch_raw", fake)
    monkeypatch.setattr(llm.settings, "llm_provider", "chain")
    monkeypatch.setattr(llm.settings, "llm_chain",
                        [("claude_cli", "claude-fable-5-1"), ("codex_cli", "gpt-5.6-sol")])

    assert llm.complete_json("s", "p") == {"ok": "claude_cli"}
    assert chamados == [("claude_cli", "claude-fable-5-1")], "não deveria ter tentado o segundo"


def test_chain_cai_para_o_proximo_quando_o_primeiro_falha(monkeypatch):
    chamados: list[str] = []

    def fake(provider, model, system, prompt, schema, max_tokens):
        chamados.append(provider)
        if provider == "claude_cli":
            raise llm.LLMError("limite da assinatura")
        return {"ok": provider}

    monkeypatch.setattr(llm, "_dispatch_raw", fake)
    monkeypatch.setattr(llm.settings, "llm_provider", "chain")
    monkeypatch.setattr(llm.settings, "llm_chain",
                        [("claude_cli", "claude-fable-5-1"),
                         ("claude_cli", "claude-opus-5"),
                         ("codex_cli", "gpt-5.6-sol")])

    assert llm.complete_json("s", "p") == {"ok": "codex_cli"}
    assert chamados == ["claude_cli", "claude_cli", "codex_cli"]


def test_chain_toda_falhando_reporta_o_ultimo_erro(monkeypatch):
    def fake(provider, model, system, prompt, schema, max_tokens):
        raise llm.LLMError(f"morreu em {provider}")

    monkeypatch.setattr(llm, "_dispatch_raw", fake)
    monkeypatch.setattr(llm.settings, "llm_provider", "chain")
    monkeypatch.setattr(llm.settings, "llm_chain", [("a", ""), ("b", "")])

    with pytest.raises(llm.LLMError, match="morreu em b"):
        llm.complete_json("s", "p")


def test_chain_vazia_e_erro_de_configuracao(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "chain")
    monkeypatch.setattr(llm.settings, "llm_chain", [])
    with pytest.raises(llm.LLMError, match="LLM_CHAIN"):
        llm.complete_json("s", "p")


def test_provider_direto_nao_passa_pela_chain(monkeypatch):
    monkeypatch.setattr(llm, "_dispatch_raw",
                        lambda p, m, *a: {"provider": p, "model": m})
    monkeypatch.setattr(llm.settings, "llm_provider", "claude_cli")
    monkeypatch.setattr(llm.settings, "claude_cli_model", "claude-fable-5-1")
    # modelo None: o provider resolve pelo .env, como sempre fez
    assert llm.complete_json("s", "p") == {"provider": "claude_cli", "model": None}


def test_telemetria_registra_sucesso_e_falha(monkeypatch):
    def fake(provider, model, system, prompt, schema, max_tokens):
        if provider == "claude_cli":
            raise llm.LLMError("estourou")
        return {"ok": True}

    monkeypatch.setattr(llm, "_dispatch_raw", fake)
    monkeypatch.setattr(llm.settings, "llm_provider", "chain")
    monkeypatch.setattr(llm.settings, "llm_chain",
                        [("claude_cli", "claude-fable-5-1"), ("codex_cli", "gpt-5.6-sol")])

    llm.current_job.set("job_teste")
    llm.complete_json("s", "p", purpose="roteiro")

    calls = db.llm_calls_for_job("job_teste")
    assert len(calls) == 2
    assert [c["provider"] for c in calls] == ["claude_cli", "codex_cli"]
    assert calls[0]["ok"] == 0 and "estourou" in calls[0]["error"]
    assert calls[1]["ok"] == 1
    assert all(c["purpose"] == "roteiro" for c in calls)


def test_extract_json_tolera_cercas_e_texto_ao_redor():
    assert llm._extract_json('```json\n{"a": 1}\n```') == {"a": 1}   # noqa: SLF001
    assert llm._extract_json('blá blá {"a": 2} fim') == {"a": 2}      # noqa: SLF001
    with pytest.raises(llm.LLMError):
        llm._extract_json("sem json aqui")                            # noqa: SLF001
