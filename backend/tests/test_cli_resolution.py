"""Finding the CLIs, and speaking the dialect they demand.

Two failures that both look like "the model is not installed" and are neither:
the binary is present but outside PATH, and the schema is valid everywhere
except at the provider actually being called.
"""
from __future__ import annotations

import copy
import os
import stat

import pytest

from app.pipeline import doctor, llm


@pytest.fixture(autouse=True)
def _no_system_installs(monkeypatch, tmp_path):
    """Sandboxing HOME is not enough.

    Two of the fallbacks are absolute — /opt/homebrew/bin and /usr/local/bin —
    and they are real directories on the machine running the tests. On this one
    /usr/local/bin/codex is a symlink to the working install, so the tests
    asserting "nothing anywhere" found the developer's own binary and failed.
    Rerooting only the absolute patterns keeps the list's shape and its search
    order, and leaves the ~-relative ones pointing at the sandboxed HOME the
    individual tests set up.
    """
    rerooted = [pattern if pattern.startswith("~")
                else str(tmp_path / "system" / pattern.lstrip("/"))
                for pattern in llm._CLI_FALLBACKS]  # noqa: SLF001
    monkeypatch.setattr(llm, "_CLI_FALLBACKS", rerooted)


@pytest.fixture(autouse=True)
def _clear_caches():
    llm._BINARY_CACHE.clear()   # noqa: SLF001
    llm._MODEL_CACHE.clear()    # noqa: SLF001
    yield
    llm._BINARY_CACHE.clear()   # noqa: SLF001
    llm._MODEL_CACHE.clear()    # noqa: SLF001


def _executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# ------------------------------------------------------------ finding them

def test_path_wins_when_it_has_the_binary(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda b: "/usr/bin/" + b)
    assert llm.find_binary("codex") == "/usr/bin/codex"


def test_an_install_outside_path_is_still_found(tmp_path, monkeypatch):
    """The real failure: the server runs as one user while PATH still lists
    another user's ~/.local/bin. The binary is installed and authenticated,
    `which` finds nothing, and a subscription being paid for reads as absent."""
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    installed = _executable(tmp_path / ".local" / "bin" / "codex")

    assert llm.find_binary("codex") == str(installed)


def test_a_broken_symlink_counts_as_absent(tmp_path, monkeypatch):
    """Homebrew leaves one behind when the npm package under it is removed.
    `exists()` follows the link, so this resolves to nothing rather than
    failing later at exec time with a confusing error."""
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    link = tmp_path / ".local" / "bin" / "codex"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(tmp_path / "gone" / "codex")

    assert llm.find_binary("codex") is None


def test_a_file_that_is_not_executable_is_not_a_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".local" / "bin" / "codex"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a program", encoding="utf-8")
    path.chmod(0o644)

    assert llm.find_binary("codex") is None


def test_nothing_anywhere_is_reported_as_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert llm.find_binary("codex") is None
    assert "not installed" in llm._unavailable_reason("codex_cli", "gpt-6-astra")  # noqa: SLF001


def test_the_doctor_and_the_chain_agree(tmp_path, monkeypatch):
    """The doctor reporting a CLI as missing while the chain happily calls it
    is worse than either being wrong alone — the health readout is what people
    trust when something looks broken."""
    monkeypatch.setattr(llm.shutil, "which", lambda b: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    _executable(tmp_path / ".local" / "bin" / "codex")
    monkeypatch.setattr(llm.settings, "codex_cli_bin", "codex")

    assert doctor.provider_ready("codex_cli") is True
    assert llm._unavailable_reason("codex_cli", "gpt-6-astra") == ""  # noqa: SLF001


# --------------------------------------------------- the schema they accept

def _missing_strictness(schema, path="root"):
    bad = []
    if isinstance(schema, dict):
        if "properties" in schema and "additionalProperties" not in schema:
            bad.append(path)
        for key, value in (schema.get("properties") or {}).items():
            bad += _missing_strictness(value, f"{path}.{key}")
        if isinstance(schema.get("items"), dict):
            bad += _missing_strictness(schema["items"], path + "[]")
    return bad


def test_every_object_gains_the_key_codex_demands():
    """Codex answers 400 — not a soft failure — when a structured-output schema
    has an object without `additionalProperties: false`. A schema every other
    provider accepts was taking the link down before it could answer."""
    loose = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"kind": {"type": "string"},
                                   "seconds": {"type": "integer"}},
                },
            },
        },
    }
    assert _missing_strictness(loose), "the fixture has to start loose"
    assert _missing_strictness(llm._strict_schema(loose)) == []  # noqa: SLF001


def test_the_callers_schema_is_not_mutated():
    """The same schema object goes to every provider in the chain, and
    Anthropic does not want this key — rewriting in place would change what
    the next link receives."""
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    before = copy.deepcopy(schema)
    llm._strict_schema(schema)  # noqa: SLF001
    assert schema == before


def test_declared_requirements_are_kept():
    schema = {"type": "object",
              "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
              "required": ["a"]}
    assert llm._strict_schema(schema)["required"] == ["a"]  # noqa: SLF001


def test_a_schema_without_required_gets_every_property():
    """Codex wants each property listed; an optional field is expressed by
    allowing null, not by leaving it out."""
    schema = {"type": "object",
              "properties": {"a": {"type": "string"}, "b": {"type": "string"}}}
    assert set(llm._strict_schema(schema)["required"]) == {"a", "b"}  # noqa: SLF001


def test_a_schema_that_is_already_strict_is_unchanged():
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"a": {"type": "string"}}, "required": ["a"]}
    assert llm._strict_schema(schema) == schema  # noqa: SLF001


def test_the_pipelines_own_schemas_survive_the_rewrite():
    """Not a synthetic fixture: the real schemas the pipeline sends."""
    from app.pipeline import script, story

    for module in (script, story):
        for name in dir(module):
            if not name.endswith("_SCHEMA"):
                continue
            schema = getattr(module, name)
            if not isinstance(schema, dict):
                continue
            strict = llm._strict_schema(schema)  # noqa: SLF001
            assert _missing_strictness(strict) == [], f"{module.__name__}.{name}"
