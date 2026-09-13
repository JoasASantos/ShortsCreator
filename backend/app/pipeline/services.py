"""Starting the local servers this project talks to, from the panel.

The screen already says "VoiceStudio is not answering — run `docker run -d -p
127.0.0.1:3900:3900 ...`". Making someone copy that into a terminal is the
whole friction: the command never varies, the panel knows it, and the only
thing it legitimately needs from a person is permission.

So: the panel asks, the person answers yes, this starts it.

Two rules shape everything here:

* The command is written HERE, never received. A service is a fixed entry in
  `CATALOG` — image, container name, ports, volumes — and the route only ever
  names one of them. Nothing a request carries reaches a shell.
* `docker run` is never how a container is restarted. Once the container
  exists it is `docker start`, so the volume, the downloaded models and the
  settings survive; running again would leave a second container fighting for
  the same port.

The image is large and the first pull is minutes long, so starting is detached
and readiness is a separate question — the panel polls the service's own probe,
which is the truth about whether it can actually be used.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

# Long enough for docker to answer, short enough that a wedged daemon does not
# hold the screen. Pulling happens in the background, not inside these calls.
PROBE_TIMEOUT = 12.0
START_TIMEOUT = 60.0


@dataclass(frozen=True)
class Service:
    id: str
    label: str
    image: str
    container: str
    ports: tuple[str, ...]
    # Named volumes, so a container removed by hand does not take the models
    # with it. A first pull here is measured in gigabytes.
    volumes: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    note: str = ""
    docs: str = ""


CATALOG: dict[str, Service] = {
    "voicestudio": Service(
        id="voicestudio",
        label="VoiceStudio",
        image="palashdeb/omnivoice-studio:stable",
        container="shortscreator-voicestudio",
        # Bound to the loopback on purpose: this exposes voice cloning and an
        # unauthenticated API, and neither belongs on the network the machine
        # happens to be on.
        ports=("127.0.0.1:3900:3900",),
        volumes=("voicestudio-data:/app/omnivoice_data",
                 "voicestudio-models:/app/omnivoice_data/huggingface"),
        env=("OMNIVOICE_DATA_DIR=/app/omnivoice_data",),
        note="The first start downloads the image and a speech model — expect "
             "several gigabytes and a few minutes before it answers.",
        docs="https://github.com/debpalash/VoiceStudio",
    ),
}

# What the panel needs to decide what to offer.
MISSING_DOCKER = "sem_docker"      # docker is not installed
DAEMON_DOWN = "docker_parado"      # installed, but the daemon is not running
STOPPED = "parado"                 # the container exists and is stopped
ABSENT = "nao_criado"              # never created here
RUNNING = "rodando"


def _docker() -> str | None:
    return shutil.which("docker")


def _run(args: list[str], timeout: float = PROBE_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=timeout)


def docker_state() -> tuple[str, str]:
    """('', '') when docker is usable; otherwise the state and why."""
    if _docker() is None:
        return MISSING_DOCKER, ("Docker is not installed on this machine. "
                                "https://docs.docker.com/get-docker/")
    try:
        proc = _run(["info", "--format", "{{.ServerVersion}}"])
    except (OSError, subprocess.SubprocessError) as exc:
        return DAEMON_DOWN, f"Docker did not answer ({type(exc).__name__})."
    if proc.returncode != 0:
        return DAEMON_DOWN, ("Docker is installed but the daemon is not "
                             "running — open Docker Desktop, or "
                             "`sudo systemctl start docker`.")
    return "", ""


def _container_state(service: Service) -> str:
    """RUNNING / STOPPED / ABSENT, from docker's own view."""
    proc = _run(["ps", "-a", "--filter", f"name=^{service.container}$",
                 "--format", "{{.State}}"])
    state = (proc.stdout or "").strip().splitlines()
    if not state or not state[0]:
        return ABSENT
    return RUNNING if state[0].strip() == "running" else STOPPED


def status(service_id: str) -> dict:
    """Everything the panel needs to draw the offer, and nothing it cannot act
    on. Never raises: this is asked while a screen is being painted."""
    service = CATALOG[service_id]
    docker_problem, reason = docker_state()
    if docker_problem:
        return {"id": service.id, "label": service.label, "state": docker_problem,
                "reason": reason, "can_start": False, "image": service.image,
                "note": service.note, "docs": service.docs}
    try:
        state = _container_state(service)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"id": service.id, "label": service.label, "state": DAEMON_DOWN,
                "reason": f"Docker did not answer ({type(exc).__name__}).",
                "can_start": False, "image": service.image,
                "note": service.note, "docs": service.docs}
    reasons = {
        RUNNING: f"The {service.container} container is running.",
        STOPPED: f"The {service.container} container exists and is stopped.",
        ABSENT: f"Never started here — it will pull {service.image}.",
    }
    return {"id": service.id, "label": service.label, "state": state,
            "reason": reasons[state], "can_start": state != RUNNING,
            "image": service.image, "note": service.note, "docs": service.docs}


def start(service_id: str) -> dict:
    """Bring the service up. Detached: the pull is minutes long.

    A stopped container is STARTED, not re-run: re-running would create a
    second one and it would lose the fight for the port, with the models it
    already downloaded stranded in the first.
    """
    service = CATALOG[service_id]
    docker_problem, reason = docker_state()
    if docker_problem:
        raise RuntimeError(reason)

    state = _container_state(service)
    if state == RUNNING:
        return {"started": False, "state": RUNNING,
                "message": f"{service.label} is already running."}

    if state == STOPPED:
        proc = _run(["start", service.container], timeout=START_TIMEOUT)
        if proc.returncode != 0:
            raise RuntimeError(f"docker start failed: {_tail(proc)}")
        return {"started": True, "state": RUNNING,
                "message": f"{service.label} restarted — the models it already "
                           f"downloaded are still there."}

    args = ["run", "-d", "--name", service.container, "--restart", "unless-stopped"]
    for port in service.ports:
        args += ["-p", port]
    for volume in service.volumes:
        args += ["-v", volume]
    for variable in service.env:
        args += ["-e", variable]
    args.append(service.image)

    proc = _run(args, timeout=START_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"docker run failed: {_tail(proc)}")
    return {"started": True, "state": RUNNING,
            "message": f"{service.label} is starting. {service.note}"}


def stop(service_id: str) -> dict:
    """Stop it without removing it, so nothing downloaded is lost."""
    service = CATALOG[service_id]
    docker_problem, reason = docker_state()
    if docker_problem:
        raise RuntimeError(reason)
    if _container_state(service) != RUNNING:
        return {"stopped": False, "state": _container_state(service),
                "message": f"{service.label} was not running."}
    proc = _run(["stop", service.container], timeout=START_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"docker stop failed: {_tail(proc)}")
    return {"stopped": True, "state": STOPPED,
            "message": f"{service.label} stopped. Nothing was deleted."}


def logs(service_id: str, lines: int = 40) -> str:
    """The container's last lines — what "it is pulling a 4 GB model" looks
    like while the panel waits."""
    service = CATALOG[service_id]
    if _docker() is None:
        return ""
    try:
        proc = _run(["logs", "--tail", str(max(1, min(lines, 200))),
                     service.container])
    except (OSError, subprocess.SubprocessError):
        return ""
    return ((proc.stdout or "") + (proc.stderr or ""))[-4000:]


def _tail(proc: subprocess.CompletedProcess) -> str:
    text = (proc.stderr or proc.stdout or "").strip()
    return text.splitlines()[-1][:300] if text else f"exit {proc.returncode}"
