"""LLM client with a provider abstraction. Default: Fable -> Opus 5 -> Codex chain."""
from __future__ import annotations

import contextvars
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from .. import db
from ..config import MODEL_PRESETS, settings

# Job that owns the current call — the orchestrator sets it before running the
# pipeline, and every call is recorded with cost/latency in `llm_calls`.
current_job: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_current_job", default=None)
current_purpose: contextvars.ContextVar[str] = contextvars.ContextVar(
    "llm_current_purpose", default="geral")


class LLMError(RuntimeError):
    pass


def _record(provider: str, model: str | None, started: float, ok: bool,
            error: str = "") -> None:
    try:
        db.log_llm_call(current_job.get(), current_purpose.get(), provider,
                        model or "", time.monotonic() - started, ok, error)
    except Exception:  # noqa: BLE001 — telemetry never takes generation down
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
            raise LLMError(f"LLM response with no valid JSON: {text[:400]}")
        return json.loads(match.group(0))


def complete_json(system: str, prompt: str, schema: dict | None = None,
                  max_tokens: int = 8000, purpose: str = "") -> dict:
    token = current_purpose.set(purpose) if purpose else None
    try:
        provider = settings.llm_provider
        if provider == "chain":
            return _chain_json(system, prompt, schema, max_tokens)
        return _dispatch(provider, None, system, prompt, schema, max_tokens)
    finally:
        if token is not None:
            current_purpose.reset(token)


def _dispatch(provider: str, model: str | None, system: str, prompt: str,
              schema: dict | None, max_tokens: int) -> dict:
    started = time.monotonic()
    try:
        result = _dispatch_raw(provider, model, system, prompt, schema, max_tokens)
    except Exception as exc:
        _record(provider, model or _default_model(provider), started, False, str(exc))
        raise
    _record(provider, model or _default_model(provider), started, True)
    return result


def _default_model(provider: str) -> str:
    return {
        "anthropic": settings.anthropic_model,
        "openai": settings.openai_model,
        "ollama": settings.ollama_model,
        "claude_cli": settings.claude_cli_model or "session",
        "codex_cli": settings.codex_cli_model or "session",
    }.get(provider, "")


def _dispatch_raw(provider: str, model: str | None, system: str, prompt: str,
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
    raise LLMError(f"Unknown LLM_PROVIDER: {provider}")


def _chain_json(system: str, prompt: str, schema: dict | None, max_tokens: int) -> dict:
    """Tries each provider:model in order and falls through on failure.

    The chain comes from LLM_MODEL: the chosen model first, every other known
    one behind it. A subscription runs out per account, so the order alternates
    between the Claude and the OpenAI CLIs — the link after a spent quota is on
    the other subscription rather than the same one that just refused.

    A link that fails is reported, not swallowed. Silently dropping to the
    fallback is how someone measures a model for a week without noticing their
    primary never once answered.
    """
    steps = active_chain()
    if not steps:
        raise LLMError(
            "No model chain to try. Set LLM_MODEL in .env to one of: "
            + ", ".join(sorted(MODEL_PRESETS)))

    attempts: list[str] = []
    last_error: Exception | None = None

    for provider, model in steps:
        label = f"{provider}:{model}" if model else provider

        unavailable = _unavailable_reason(provider, model)
        if unavailable:
            attempts.append(f"{label} ({unavailable})")
            _log_event(f"LLM: skipping {label} — {unavailable}", "warn")
            continue

        try:
            answer = _dispatch(provider, model or None, system, prompt, schema,
                               max_tokens)
        except Exception as exc:  # noqa: BLE001 — try the next link in the chain
            reason = str(exc).strip().splitlines()[-1][:160] if str(exc) else type(exc).__name__
            attempts.append(f"{label} ({reason})")
            _log_event(f"LLM: {label} failed, moving to the next model — {reason}",
                       "warn")
            last_error = exc
            continue

        if attempts:
            # Worth stating plainly: the short was written by a different model
            # than the one that was chosen.
            _log_event(f"LLM: answered by {label} after {len(attempts)} "
                       f"model(s) could not", "warn")
        return answer

    raise LLMError(
        "Every model in the chain failed, so nothing could be generated. "
        "Tried: " + "; ".join(attempts) + ". "
        f"Last error: {last_error}")


SETTING_KEY = "llm_model"


def active_model() -> str:
    """The chosen model: what the interface saved, or what .env says.

    Read every time rather than cached on `settings`, because the point of
    saving it in the database is to change models without a restart.
    """
    return db.get_setting(SETTING_KEY, settings.llm_model)


def active_chain() -> list[tuple[str, str]]:
    """The chain in force, derived from the chosen model."""
    from ..config import build_chain  # noqa: PLC0415 — avoids a cycle at import

    return build_chain(active_model(), settings.llm_chain_raw)


# What each CLI answered when asked which models it has. Probing costs a
# subprocess, and the answer does not change inside one run.
_MODEL_CACHE: dict[str, set[str] | None] = {}

# CLIs that answer `<binary> models` with a listing, and the prefix their model
# ids carry.
#
# Only Codex is here, and the omission of Claude Code is the point: `claude
# models` is not a subcommand, so the CLI reads the word as a prompt, spends a
# real request answering it in prose, and hands back a paragraph. Parsing that
# yields a set of ordinary words that contains no model id at all — which then
# excluded every working Claude model from the chain. A listing has to be
# something the CLI documents, not something we hope it supports.
_LISTS_MODELS = {"codex_cli": "gpt-"}


def available_models(provider: str) -> set[str] | None:
    """The model ids a CLI provider actually offers, or None when unknowable.

    None is not "no models": it is "this CLI could not be asked". The caller
    must treat that as "go ahead and try", never as "skip" — refusing a model
    because we could not enumerate it would take the whole chain down over a
    changed output format.
    """
    if provider in _MODEL_CACHE:
        return _MODEL_CACHE[provider]

    _MODEL_CACHE[provider] = None   # unknowable until proven otherwise
    hint = _LISTS_MODELS.get(provider)
    if hint is None:
        return None

    binary = {"codex_cli": settings.codex_cli_bin,
              "claude_cli": settings.claude_cli_bin}.get(provider)
    found = shutil.which(binary) if binary else None
    if not found:
        return None

    try:
        proc = subprocess.run([found, "models"], capture_output=True, text=True,
                              timeout=30)
    except Exception:  # noqa: BLE001 — an unlistable CLI is not a broken one
        return None
    if proc.returncode != 0:
        return None

    # The listing is a human-facing table and its shape is not a contract, so
    # the ids are whatever token-like words are in it.
    ids = {w for w in re.findall(r"[a-z0-9][a-z0-9._-]{3,}", proc.stdout.lower())}
    # Trust it only if it looks like a listing of this provider's models. An
    # answer with none of them in it is prose, an error page, or a CLI that
    # changed — and excluding models on that basis is how a working chain goes
    # dark.
    if not any(w.startswith(hint) for w in ids):
        return None

    _MODEL_CACHE[provider] = ids
    return ids


def _unavailable_reason(provider: str, model: str) -> str:
    """Why this link cannot be used, or "" when it can be tried.

    Checked before the call so a model the CLI does not know is skipped with a
    reason instead of being sent anyway — a rejected model costs a failed call,
    and a silently substituted one costs a short written by a model nobody
    chose.
    """
    binary = {"codex_cli": settings.codex_cli_bin,
              "claude_cli": settings.claude_cli_bin}.get(provider)
    if binary and not shutil.which(binary):
        return f"`{binary}` is not installed"

    if not model or not settings.llm_verify_model:
        return ""

    known = available_models(provider)
    if known is None:
        return ""   # could not ask; trying is better than refusing
    if model.lower() in known:
        return ""
    return f"`{model}` is not among the models this CLI offers"


def _log_event(message: str, level: str = "info") -> None:
    """Put chain decisions on the job's own log, when there is a job.

    Called from inside a generation, so it must never be the thing that breaks
    one: a chain that cannot write to its log still has to answer.
    """
    job_id = current_job.get()
    if not job_id:
        return
    try:
        db.log_event(job_id, message, level)
    except Exception:  # noqa: BLE001 — logging must never break a generation
        pass


def _anthropic_json(system: str, prompt: str, schema: dict | None, max_tokens: int,
                    model: str | None = None) -> dict:
    import anthropic

    if not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY is not set")

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
        raise LLMError("The model refused the request.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return _extract_json(text)


def _openai_json(system: str, prompt: str, max_tokens: int) -> dict:
    if not settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY is not set")
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
# CLI-based providers — they use the subscription already logged in on the
# machine (Claude Pro/Max via `claude`, ChatGPT Plus/Pro via `codex`) instead of
# an API key billed per token. No key has to go into the .env.
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
            f"CLI '{binary}' not found on PATH. "
            f"Install and authenticate {label}, or switch LLM_PROVIDER in .env."
        )
    return found


def _run_cli(cmd: list[str], cwd: str, timeout: int) -> str:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise LLMError(
            f"CLI went over {timeout}s. Raise LLM_CLI_TIMEOUT in .env."
        ) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip()[-600:]
        raise LLMError(f"CLI failed (exit code {proc.returncode}): {tail}")
    return proc.stdout


def _claude_cli_json(system: str, prompt: str, model: str | None = None) -> dict:
    """Claude Code in non-interactive mode — draws on the Pro/Max subscription."""
    binary = _resolve(settings.claude_cli_bin, "Claude Code (`claude` login)")

    cmd = [
        binary, "-p", prompt,
        "--output-format", "json",
        "--append-system-prompt", f"{system}\n\n{JSON_ONLY}",
        # no tools: we want an answer, not an agent acting on the disk
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
        # if the format changes, we still try to find the JSON in the raw text
        return _extract_json(stdout)

    if envelope.get("is_error"):
        raise LLMError(f"Claude CLI returned an error: {str(envelope)[:400]}")
    return _extract_json(envelope.get("result") or "")


def _codex_cli_json(system: str, prompt: str, schema: dict | None,
                    model: str | None = None) -> dict:
    """Codex CLI in non-interactive mode — draws on the ChatGPT subscription."""
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
