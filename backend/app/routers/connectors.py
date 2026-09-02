from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from ..pipeline import connectors

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


@router.get("")
def list_connectors():
    return connectors.describe_all()


@router.get("/{connector_id}")
def get_connector(connector_id: str):
    try:
        return connectors.describe(connector_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@router.put("/{connector_id}")
def save_connector(connector_id: str, values: dict = Body(...)):
    try:
        return connectors.save(connector_id, values)
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@router.delete("/{connector_id}")
def clear_connector(connector_id: str):
    try:
        return connectors.clear(connector_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{connector_id}/test")
def test_connector(connector_id: str):
    try:
        message = connectors.test(connector_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "message": message}
