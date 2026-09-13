"""Starting and stopping the local servers, from the panel.

The screen already prints the docker command; these routes are what turns that
paragraph into a button. The catalogue lives in the pipeline module — a request
names a service, never a command.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..pipeline import services

router = APIRouter(prefix="/api/services", tags=["services"])


@router.get("")
def list_services():
    """Every service the panel can start, with what docker says about it."""
    return {"docker": _docker_line(),
            "services": [services.status(sid) for sid in services.CATALOG]}


@router.get("/{service_id}")
def one(service_id: str):
    _known(service_id)
    return services.status(service_id)


@router.post("/{service_id}/start")
def start(service_id: str):
    _known(service_id)
    try:
        return services.start(service_id)
    except RuntimeError as exc:
        # Docker missing, daemon down, port taken: all things to read and act
        # on, none of them a traceback.
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — docker misbehaving
        raise HTTPException(502, f"Docker did not answer: {exc}") from exc


@router.post("/{service_id}/stop")
def stop(service_id: str):
    """Stops the container without removing it — nothing downloaded is lost,
    and starting again is seconds rather than another multi-gigabyte pull."""
    _known(service_id)
    try:
        return services.stop(service_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Docker did not answer: {exc}") from exc


@router.get("/{service_id}/logs")
def logs(service_id: str, lines: int = 40):
    """What the wait looks like: the first start is pulling gigabytes, and a
    screen with no output is indistinguishable from a stuck one."""
    _known(service_id)
    return {"logs": services.logs(service_id, lines)}


def _known(service_id: str) -> None:
    if service_id not in services.CATALOG:
        raise HTTPException(404, f"Unknown service: {service_id}")


def _docker_line() -> dict:
    state, reason = services.docker_state()
    return {"ok": not state, "state": state or "pronto",
            "reason": reason or "Docker answering."}
