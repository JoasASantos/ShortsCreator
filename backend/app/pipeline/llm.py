"""Cliente LLM com abstração de provider. Padrão: Anthropic Claude Opus 5."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from ..config import settings


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise LLMError(f"Resposta do LLM sem JSON válido: {text[:400]}")
        return json.loads(match.group(0))


def complete_json(system: str, prompt: str, schema: dict | None = None,
                  max_tokens: int = 8000) -> dict:
    provider = settings.llm_provider
    if provider == "chain":
        return _chain_json(system, prompt, schema, max_tokens)
    return _dispatch(provider, None, system, prompt, schema, max_tokens)


def _dispatch(provider: str, model: str | None, system: str, prompt: str,
              schema: dict | None, max_tokens: int) -> dict:
    if provider == "anthropic":
        return _anthropic_json(system, prompt, schema, max_tokens, model)
    if provider == "openai":
        return _openai_json(system, prompt, max_tokens)
    if provider == "ollama":
        return _ollama_json(system, prompt, max_tokens)
    if provider == "claude_cli":
        return _claude_cli_json(system, prompt, model)
    if provider == "codex_cli":
        return _codex_cli_json(system, prompt, schema, model)
    raise LLMError(f"LLM_PROVIDER desconhecido: {provider}")


def _chain_json(system: str, prompt: str, schema: dict | None, max_tokens: int) -> dict:
    """Tenta cada provider:model de LLM_CHAIN em ordem; cai pro próximo se falhar.

    Padrão: Claude Fable (principal) -> Claude Opus 5 -> Codex GPT-5.6 Sol
    (assinatura ChatGPT), sem precisar de chave de API.
    """
    steps = settings.llm_chain
    if not steps:
        raise LLMError("LLM_PROVIDER=chain requer LLM_CHAIN configurado no .env")

    last_error: Exception | None = None
    for provider, model in steps:
        try:
            return _dispatch(provider, model or None, system, prompt, schema, max_tokens)
        except Exception as exc:  # noqa: BLE001 — tenta o próximo elo da cadeia
            last_error = exc
            continue
    raise LLMError(f"Todos os modelos da chain falharam. Último erro: {last_error}")


def _anthropic_json(system: str, prompt: str, schema: dict | None, max_tokens: int,
                    model: str | None = None) -> dict:
    import anthropic

    if not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY não configurada")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    kwargs: dict = {
        "model": model or settings.anthropic_model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    if schema:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}

    response = client.messages.create(**kwargs)
    if response.stop_reason == "refusal":
        raise LLMError("Modelo recusou a solicitação.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return _extract_json(text)


def _openai_json(system: str, prompt: str, max_tokens: int) -> dict:
    if not settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY não configurada")
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": settings.openai_model,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=180,
    )
    resp.raise_for_status()
    return _extract_json(resp.json()["choices"][0]["message"]["content"])


def _ollama_json(system: str, prompt: str, max_tokens: int) -> dict:
    resp = httpx.post(
        f"{settings.ollama_host}/api/chat",
        json={
            "model": settings.ollama_model,
            "stream": False,
            "format": "json",
            "options": {"num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=300,
    )
    resp.raise_for_status()
    return _extract_json(resp.json()["message"]["content"])


# --------------------------------------------------------------------------
# Providers por CLI — usam a assinatura já logada na máquina (Claude Pro/Max
# via `claude`, ChatGPT Plus/Pro via `codex`) em vez de uma chave de API
# cobrada por token. Nenhuma chave precisa entrar no .env.
# --------------------------------------------------------------------------

JSON_ONLY = (
    "Você responde exclusivamente com um objeto JSON válido. "
    "Não use blocos de código, comentários, preâmbulo ou texto após o JSON. "
    "Não use ferramentas, não leia nem escreva arquivos: apenas responda."
)


def _resolve(binary: str, label: str) -> str:
    found = shutil.which(binary)
    if not found:
        raise LLMError(
            f"CLI '{binary}' não encontrada no PATH. "
            f"Instale e autentique o {label}, ou troque LLM_PROVIDER no .env."
        )
    return found


def _run_cli(cmd: list[str], cwd: str, timeout: int) -> str:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise LLMError(
            f"CLI excedeu {timeout}s. Aumente LLM_CLI_TIMEOUT no .env."
        ) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip()[-600:]
        raise LLMError(f"CLI falhou (código {proc.returncode}): {tail}")
    return proc.stdout


def _claude_cli_json(system: str, prompt: str, model: str | None = None) -> dict:
    """Claude Code em modo não interativo — consome a assinatura Pro/Max."""
    binary = _resolve(settings.claude_cli_bin, "Claude Code (`claude` login)")

    cmd = [
        binary, "-p", prompt,
        "--output-format", "json",
        "--append-system-prompt", f"{system}\n\n{JSON_ONLY}",
        # sem ferramentas: queremos uma resposta, não um agente agindo no disco
        "--disallowed-tools", "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task",
        "--strict-mcp-config",
    ]
    chosen = model or settings.claude_cli_model
    if chosen:
        cmd += ["--model", chosen]

    with tempfile.TemporaryDirectory(prefix="shortscreator-llm-") as work:
        stdout = _run_cli(cmd, work, settings.llm_cli_timeout)

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        # se o formato mudar, ainda tentamos achar o JSON no texto cru
        return _extract_json(stdout)

    if envelope.get("is_error"):
        raise LLMError(f"Claude CLI retornou erro: {str(envelope)[:400]}")
    return _extract_json(envelope.get("result") or "")


def _codex_cli_json(system: str, prompt: str, schema: dict | None,
                    model: str | None = None) -> dict:
    """Codex CLI em modo não interativo — consome a assinatura ChatGPT."""
    binary = _resolve(settings.codex_cli_bin, "Codex (`codex login`)")

    with tempfile.TemporaryDirectory(prefix="shortscreator-llm-") as work:
        work_dir = Path(work)
        answer = work_dir / "answer.json"

        cmd = [
            binary, "exec",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--cd", str(work_dir),
            "--color", "never",
            "-o", str(answer),
            "-c", f"model_reasoning_effort={settings.codex_reasoning_effort}",
        ]
        chosen = model or settings.codex_cli_model
        if chosen:
            cmd += ["--model", chosen]
        if schema:
            schema_file = work_dir / "schema.json"
            schema_file.write_text(json.dumps(schema), encoding="utf-8")
            cmd += ["--output-schema", str(schema_file)]

        cmd.append(f"{system}\n\n{JSON_ONLY}\n\n{prompt}")

        stdout = _run_cli(cmd, str(work_dir), settings.llm_cli_timeout)
        raw = answer.read_text(encoding="utf-8") if answer.exists() else stdout

    return _extract_json(raw)
