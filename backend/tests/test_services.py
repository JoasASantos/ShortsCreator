"""Starting a local server from the panel, with permission asked first.

The screen was already printing `docker run -d -p 127.0.0.1:3900:3900 ...` and
asking someone to copy it into a terminal. What is worth guarding once that
becomes a button: the command is written here and never received, a container
that already exists is restarted rather than duplicated, and every way docker
can be unavailable is a sentence with a next step in it.
"""
from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from app.pipeline import services


@pytest.fixture()
def client():
    from app.main import app

    return TestClient(app)


class _Proc:
    def __init__(self, code: int = 0, out: str = "", err: str = ""):
        self.returncode, self.stdout, self.stderr = code, out, err


def _docker(monkeypatch, *, installed=True, daemon=True, container=""):
    """A machine in a given state, as `docker` would report it."""
    monkeypatch.setattr(services.shutil, "which",
                        lambda name: "/usr/local/bin/docker" if installed else None)
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(list(args))
        command = args[1] if len(args) > 1 else ""
        if command == "info":
            return _Proc(0 if daemon else 1, "27.0.1" if daemon else "",
                         "" if daemon else "Cannot connect to the Docker daemon")
        if command == "ps":
            return _Proc(0, container)
        return _Proc(0, "deadbeef")

    monkeypatch.setattr(services.subprocess, "run", run)
    return calls


# ------------------------------------------------------- what docker can say

def test_docker_not_installed_is_a_sentence_with_a_link(monkeypatch):
    _docker(monkeypatch, installed=False)
    report = services.status("voicestudio")
    assert report["state"] == services.MISSING_DOCKER
    assert report["can_start"] is False
    assert "docs.docker.com" in report["reason"]


def test_a_stopped_daemon_is_told_apart_from_a_missing_docker(monkeypatch):
    """Different problem, different fix: one is an install, the other is
    opening an app that is already on the machine."""
    _docker(monkeypatch, daemon=False)
    report = services.status("voicestudio")
    assert report["state"] == services.DAEMON_DOWN
    assert "Docker Desktop" in report["reason"] or "systemctl" in report["reason"]
    assert report["can_start"] is False


def test_a_hung_docker_does_not_take_the_screen_down(monkeypatch):
    monkeypatch.setattr(services.shutil, "which", lambda name: "/usr/bin/docker")

    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=12)

    monkeypatch.setattr(services.subprocess, "run", hang)
    report = services.status("voicestudio")
    assert report["state"] == services.DAEMON_DOWN
    assert report["can_start"] is False


def test_a_container_that_was_never_created_offers_to_pull(monkeypatch):
    _docker(monkeypatch, container="")
    report = services.status("voicestudio")
    assert report["state"] == services.ABSENT
    assert report["can_start"] is True
    assert "palashdeb/omnivoice-studio" in report["image"]
    assert "gigabytes" in report["note"], "the wait is named before it starts"


def test_a_running_container_offers_nothing_to_start(monkeypatch):
    _docker(monkeypatch, container="running\n")
    report = services.status("voicestudio")
    assert report["state"] == services.RUNNING
    assert report["can_start"] is False


# ------------------------------------------------------------- starting it

def test_the_first_start_runs_the_pinned_image_on_the_loopback(monkeypatch):
    """Every part of this command is written in the catalogue. The port is
    bound to 127.0.0.1 on purpose: this exposes voice cloning and an
    unauthenticated API, which do not belong on the local network."""
    calls = _docker(monkeypatch, container="")
    services.start("voicestudio")

    run = next(c for c in calls if c[1] == "run")
    assert "-d" in run and run[-1] == "palashdeb/omnivoice-studio:stable"
    assert "127.0.0.1:3900:3900" in run, "never 0.0.0.0"
    assert "shortscreator-voicestudio" in run
    # named volumes, so the gigabytes survive a container being removed by hand
    assert any(v.startswith("voicestudio-data:") for v in run)


def test_an_existing_container_is_started_not_run_again(monkeypatch):
    """Re-running would create a second container that loses the fight for the
    port, with the models the first one downloaded stranded inside it."""
    calls = _docker(monkeypatch, container="exited\n")
    result = services.start("voicestudio")

    assert [c[1] for c in calls if c[1] in ("run", "start")] == ["start"]
    assert result["state"] == services.RUNNING
    assert "already downloaded" in result["message"]


def test_starting_what_is_already_running_does_nothing(monkeypatch):
    calls = _docker(monkeypatch, container="running\n")
    result = services.start("voicestudio")
    assert result["started"] is False
    assert [c[1] for c in calls if c[1] in ("run", "start")] == []


def test_starting_without_docker_refuses_with_the_reason(monkeypatch):
    _docker(monkeypatch, installed=False)
    with pytest.raises(RuntimeError, match="not installed"):
        services.start("voicestudio")


def test_a_failed_run_reports_dockers_own_last_line(monkeypatch):
    monkeypatch.setattr(services.shutil, "which", lambda name: "/usr/bin/docker")

    def run(args, **kwargs):
        if args[1] == "info":
            return _Proc(0, "27.0.1")
        if args[1] == "ps":
            return _Proc(0, "")
        return _Proc(125, "", "docker: Error response from daemon: port is "
                              "already allocated.")

    monkeypatch.setattr(services.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="already allocated"):
        services.start("voicestudio")


# -------------------------------------------------------------- stopping it

def test_stopping_keeps_the_container_so_nothing_is_downloaded_twice(monkeypatch):
    calls = _docker(monkeypatch, container="running\n")
    result = services.stop("voicestudio")

    assert [c[1] for c in calls if c[1] in ("stop", "rm")] == ["stop"]
    assert "Nothing was deleted" in result["message"]


def test_stopping_what_is_not_running_is_not_an_error(monkeypatch):
    _docker(monkeypatch, container="exited\n")
    assert services.stop("voicestudio")["stopped"] is False


# ------------------------------------------------------------- the routes

def test_the_route_lists_what_can_be_started(client, monkeypatch):
    _docker(monkeypatch, container="")
    body = client.get("/api/services").json()
    assert body["services"][0]["id"] == "voicestudio"
    assert body["docker"]["ok"] is True


def test_an_unknown_service_is_a_404_not_a_command(client):
    """The catalogue is the whole allow-list: a request names a service, never
    a command."""
    assert client.post("/api/services/rm%20-rf/start").status_code == 404
    assert client.get("/api/services/anything").status_code == 404


def test_the_start_route_turns_a_refusal_into_a_readable_400(client, monkeypatch):
    _docker(monkeypatch, installed=False)
    answer = client.post("/api/services/voicestudio/start")
    assert answer.status_code == 400
    assert "docs.docker.com" in answer.json()["detail"]


def test_the_stop_route_is_there_for_taking_it_down(client, monkeypatch):
    _docker(monkeypatch, container="running\n")
    answer = client.post("/api/services/voicestudio/stop")
    assert answer.status_code == 200
    assert answer.json()["stopped"] is True


def test_the_logs_route_shows_what_the_wait_looks_like(client, monkeypatch):
    monkeypatch.setattr(services.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(services.subprocess, "run",
                        lambda args, **k: _Proc(0, "pulling model 42%\n"))
    assert "42%" in client.get("/api/services/voicestudio/logs").json()["logs"]
