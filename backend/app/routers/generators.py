"""Honest state of every AI media generator, for the generators screen.

`probe=false` is there for a caller that only wants the catalog: probing the
local servers is what makes the answer truthful, but it costs a round trip
against each of them.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

from ..pipeline.generators import registry

router = APIRouter(prefix="/api/generators", tags=["generators"])


@router.get("")
def list_generators(probe: bool = Query(True)):
    return registry.describe_all(probe=probe)


@router.post("/test")
def test_generator(payload: dict = Body(...)):
    provider_id = (payload.get("provider") or payload.get("id") or "").strip()
    try:
        message = registry.test(provider_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001 — every failure here is a message
        # 400, not 500: a provider that is unconfigured or switched off is a
        # normal answer to this question, and the text says what to do.
        raise HTTPException(400, str(exc))
    return {"ok": True, "provider": provider_id, "message": message}
