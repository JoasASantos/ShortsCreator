"""The requirements doctor — report shape, and found/missing with a faked PATH.

Nothing here asserts that FFmpeg (or anything else) is installed: that is a
property of the machine, not of the code. Presence is exercised by pointing
PATH at a directory of stub binaries, so the same assertions hold on a laptop
with everything and on a CI box with nothing.
"""
from __future__ import annotations

import json
import os
import stat

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import doctor

client = TestClient(app)

CHECK_KEYS = {"id", "label", "required", "found", "version", "unlocks",
              "install", "note", "level"}


@pytest.fixture
def empty_path(tmp_path, monkeypatch):
    """A PATH with nothing on it — every external binary reads as missing."""
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def _stub(directory, name: str, *lines: str) -> None:
    """A fake binary that prints `lines` and exits 0.

    Only shell builtins: PATH is empty in these tests, so a stub that shells
    out to `cat` would fail for the wrong reason.
    """
    body = "".join(f"echo '{line}'\n" for line in lines)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _by_id(report: dict) -> dict:
    return {c["id"]: c for c in report["checks"]}


# ------------------------------------------------------------------- shape

def test_report_carries_the_documented_shape():
    report = doctor.check()
    assert set(report) >= {"ok", "platform", "checks", "missing_required",
                           "missing_optional", "min_free_gb"}
    assert set(report["platform"]) == {"os", "machine", "package_manager"}
    assert report["checks"], "a report with no checks is a broken doctor"
    for check in report["checks"]:
        assert set(check) == CHECK_KEYS, check["id"]
        assert check["level"] in ("required", "optional")
        assert check["label"] and check["unlocks"]


def test_check_ids_are_unique():
    """The route is consumed by id; a duplicate would silently shadow a check."""
    ids = [c["id"] for c in doctor.check()["checks"]]
    assert len(ids) == len(set(ids))


def test_the_minimum_coverage_is_all_there():
    ids = set(_by_id(doctor.check()))
    assert {"python", "ffmpeg", "ffmpeg_libass", "ffprobe", "ytdlp", "node",
            "npm", "faster_whisper", "claude_cli", "codex_cli", "disk",
            "tts_keys", "broll_keys"} <= ids


def test_ok_is_exactly_the_absence_of_a_missing_requirement(empty_path):
    for report in (doctor.check(), _report_with_stubs(empty_path)):
        missing = [c["id"] for c in report["checks"]
                   if c["level"] == "required" and not c["found"]]
        assert report["missing_required"] == missing
        assert report["ok"] is (not missing)


def _report_with_stubs(bin_dir) -> dict:
    _stub(bin_dir, "ffmpeg", "ffmpeg version 9.9.9 Copyright (c) 2000-2026 x")
    _stub(bin_dir, "ffprobe", "ffprobe version 9.9.9 Copyright (c) 2000-2026 x")
    _stub(bin_dir, "node", "v22.0.0")
    _stub(bin_dir, "npm", "10.0.0")
    return doctor.check()


# ------------------------------------------------------- found and missing

def test_an_empty_path_reports_every_binary_missing(empty_path):
    checks = _by_id(doctor.check())
    for check_id in ("ffmpeg", "ffprobe", "node", "npm", "claude_cli", "codex_cli"):
        assert checks[check_id]["found"] is False, check_id
        assert checks[check_id]["version"] == ""
    assert "ffmpeg" in doctor.check()["missing_required"]


def test_a_stub_on_the_path_is_found_with_its_version_trimmed(empty_path):
    checks = _by_id(_report_with_stubs(empty_path))
    assert checks["ffmpeg"]["found"] is True
    assert checks["ffmpeg"]["version"] == "9.9.9"
    assert checks["node"]["found"] is True
    assert checks["node"]["version"] == "v22.0.0"


def test_node_below_the_floor_counts_as_missing(empty_path):
    _stub(empty_path, "node", "v18.20.0")
    node = _by_id(doctor.check())["node"]
    assert node["found"] is False
    assert "18" in node["note"] and "20" in node["note"]


def test_install_hints_fall_back_to_a_link_when_no_manager_is_detected(empty_path):
    """No apt, no brew, no winget on the faked PATH: a command for a manager
    the user does not have would be worse than a download link."""
    report = doctor.check()
    assert report["platform"]["package_manager"] == ""
    assert "ffmpeg.org" in _by_id(report)["ffmpeg"]["install"]


# ------------------------------------------------------------------- libass

def test_libass_is_optional_and_absent_when_there_is_no_ffmpeg(empty_path):
    libass = _by_id(doctor.check())["ffmpeg_libass"]
    assert libass["required"] is False
    assert libass["found"] is False
    # the whole point of the separate check: it names the fallback
    assert "overlays" in libass["note"]


def test_libass_reads_the_filter_list_of_the_ffmpeg_that_is_there(empty_path,
                                                                 monkeypatch):
    """A build can have ffmpeg and no `ass` filter — that is this machine, and
    it is why libass is not folded into the ffmpeg check."""
    from app.pipeline import render

    # `ffmpeg -filters` format: " FLAGS name in->out description"
    scale = [" ..C scale     V->V   Scale the input video"]
    with_ass = scale + [" ..C ass       V->V   Render ASS subtitles onto input"]

    for filters, expected in ((scale, False), (with_ass, True)):
        monkeypatch.setattr(render, "_FILTERS", None)   # the probe is cached
        stub = empty_path / "ffmpeg"
        body = "".join(f"  echo '{line}'\n" for line in filters)
        stub.write_text(
            '#!/bin/sh\nif [ "$1" = --version ]; then\n'
            "  echo 'ffmpeg version 9.9.9 Copyright (c) 2000-2026 x'\nelse\n"
            f"{body}fi\n", encoding="utf-8")
        stub.chmod(0o755)
        assert _by_id(doctor.check())["ffmpeg_libass"]["found"] is expected


# --------------------------------------------------------- optional wording

def test_a_missing_optional_never_reads_as_a_failure(empty_path):
    """Every optional item that is absent has to say what it costs, and must
    not drag `ok` down with it."""
    report = doctor.check()
    optional = [c for c in report["checks"] if c["level"] == "optional"]
    assert optional
    for check in optional:
        assert check["id"] not in report["missing_required"]
        if not check["found"]:
            assert check["note"], f"{check['id']} is silent about what is lost"


def test_a_missing_codex_is_reported_honestly_and_stays_optional(empty_path):
    """`codex` off the PATH is the ordinary case: it is the last link of the
    chain and plenty of installs never add it."""
    codex = _by_id(doctor.check())["codex_cli"]
    assert codex["required"] is False
    assert codex["found"] is False
    assert "PATH" in codex["note"]
    assert "codex" in codex["install"]


def test_faster_whisper_says_what_it_costs(monkeypatch):
    monkeypatch.setattr(doctor, "_module_version", lambda name: "")
    check = _by_id(doctor.check())["faster_whisper"]
    assert check["required"] is False
    assert check["found"] is False
    assert "caption your own recordings" in check["note"]


# ---------------------------------------------------------------- disk space

def test_a_nearly_full_disk_fails_the_required_check(monkeypatch):
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(doctor.shutil, "disk_usage",
                        lambda p: usage(500_000_000_000, 499_000_000_000,
                                        1_000_000_000))
    disk = _by_id(doctor.check())["disk"]
    assert disk["required"] is True
    assert disk["found"] is False
    assert "1.0 GB free" in disk["version"]


def test_plenty_of_room_passes(monkeypatch):
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(doctor.shutil, "disk_usage",
                        lambda p: usage(500_000_000_000, 0, 400_000_000_000))
    assert _by_id(doctor.check())["disk"]["found"] is True


def test_an_unreadable_mount_point_does_not_break_the_report(monkeypatch):
    def boom(path):
        raise OSError("no such mount")

    monkeypatch.setattr(doctor.shutil, "disk_usage", boom)
    disk = _by_id(doctor.check())["disk"]
    # unknown is not the same as full: refuse to cry wolf
    assert disk["found"] is True
    assert disk["version"] == "unknown"


# ---------------------------------------------------------------- platform

def test_package_manager_follows_what_is_installed(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which",
                        lambda name: "/usr/bin/dnf" if name == "dnf" else None)
    assert doctor.package_manager("linux") == "dnf"
    assert doctor.package_manager("macos") == ""


def test_install_hints_exist_for_every_supported_manager():
    """A new manager added to detection but not to the tables would print an
    empty install line, which is worse than a link."""
    for key, table in doctor._INSTALL.items():   # noqa: SLF001
        assert table.get("*"), key


def test_clean_version_trims_the_banner():
    assert doctor._clean_version(               # noqa: SLF001
        "ffmpeg", "ffmpeg version 8.1.1 Copyright (c) 2000-2026 the devs"
    ) == "8.1.1"
    assert doctor._clean_version("node", "v22.1.0") == "v22.1.0"   # noqa: SLF001
    assert doctor._clean_version("codex", "") == ""                # noqa: SLF001


# ------------------------------------------------------------------ secrets

def test_the_report_says_a_key_is_present_and_never_what_it_is(monkeypatch):
    """Not the value, and not a masked prefix either — a prefix still leaks."""
    sentinel = "zzzz-not-a-real-key-9f3c1a"
    monkeypatch.setenv("ELEVENLABS_API_KEY", sentinel)
    monkeypatch.setattr(doctor.settings, "elevenlabs_api_key", sentinel)

    report = doctor.check()
    assert _by_id(report)["tts_keys"]["found"] is True
    assert _by_id(report)["tts_keys"]["version"] == "configured"

    dumped = json.dumps(report) + doctor.render_text(report)
    assert sentinel not in dumped
    for fragment in (sentinel[:4], sentinel[-4:]):
        assert fragment not in dumped


def test_an_absent_key_is_optional_and_explains_the_fallback(monkeypatch):
    monkeypatch.setattr(doctor, "_configured", lambda *a: False)
    for check_id in ("tts_keys", "broll_keys"):
        check = _by_id(doctor.check())[check_id]
        assert check["required"] is False
        assert check["found"] is False
        assert check["note"]


# -------------------------------------------------------------------- output

def test_the_text_report_marks_required_and_optional_apart():
    text = doctor.render_text(doctor.check())
    assert "REQUIRED" in text and "OPTIONAL" in text
    # an absent optional is never labelled MISSING
    assert "[absent " in text or "Optional, not installed" in text


def test_the_text_report_prints_the_install_line_for_what_is_missing(empty_path):
    text = doctor.render_text(doctor.check())
    assert "install:" in text
    assert "unlocks:" in text


def test_main_exits_zero_only_when_the_requirements_are_met(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check",
                        lambda: {"ok": True, "platform": {"os": "linux",
                                                          "machine": "x86_64",
                                                          "package_manager": "apt"},
                                 "checks": [], "missing_required": [],
                                 "missing_optional": [], "min_free_gb": 5.0})
    assert doctor.main() == 0
    assert "doctor" in capsys.readouterr().out


def test_main_exits_nonzero_when_a_requirement_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check",
                        lambda: {"ok": False, "platform": {"os": "linux",
                                                           "machine": "x86_64",
                                                           "package_manager": ""},
                                 "checks": [], "missing_required": ["ffmpeg"],
                                 "missing_optional": [], "min_free_gb": 5.0})
    assert doctor.main() == 1
    assert "ffmpeg" in capsys.readouterr().out


# --------------------------------------------------------------------- cache

def test_the_cached_report_is_not_reprobed_on_every_call(monkeypatch):
    """/api/health polls every few seconds and each check spawns a process."""
    calls = []
    monkeypatch.setattr(doctor, "_CACHE", None)
    monkeypatch.setattr(doctor, "check", lambda: calls.append(1) or {
        "ok": True, "missing_required": [], "missing_optional": ["codex_cli"]})

    doctor.cached_check()
    doctor.cached_check()
    assert len(calls) == 1

    assert doctor.cached_check(ttl=-1)["ok"] is True
    assert len(calls) == 2


def test_summary_is_the_verdict_without_the_detail(monkeypatch):
    monkeypatch.setattr(doctor, "_CACHE", None)
    summary = doctor.summary()
    assert set(summary) == {"ok", "missing_required", "missing_optional"}


# --------------------------------------------------------------------- route

def test_the_requirements_route_returns_the_full_report():
    body = client.get("/api/system/requirements").json()
    assert set(body) >= {"ok", "checks", "missing_required", "missing_optional"}
    assert set(body["checks"][0]) == CHECK_KEYS


def test_health_carries_the_verdict_without_the_whole_report():
    """The sidebar polls /api/health; the detail belongs to the other route."""
    body = client.get("/api/health").json()
    assert set(body["requirements"]) == {"ok", "missing_required",
                                         "missing_optional"}


def test_the_route_never_ships_a_credential():
    raw = client.get("/api/system/requirements").text
    for env_var in ("ELEVENLABS_API_KEY", "PEXELS_API_KEY", "FISHAUDIO_API_KEY"):
        value = os.getenv(env_var, "")
        if value:
            assert value not in raw, f"{env_var} leaked into the report"
