from __future__ import annotations

import json

from fastapi import APIRouter

from .. import db
from ..pipeline import metrics as metrics_mod

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _serialize(row: dict) -> dict:
    out = dict(row)
    raw = out.pop("input_json", None)
    out["niche"] = (json.loads(raw).get("niche") if raw else None)
    return out


@router.get("")
def list_metrics():
    return {"summary": metrics_mod.summary(),
            "items": [_serialize(r) for r in db.list_metrics()],
            "llm": db.llm_call_summary()}


@router.post("/refresh")
def refresh():
    updated = metrics_mod.refresh_all()
    return {"updated": updated, "summary": metrics_mod.summary()}


@router.get("/insights")
def insights(niche: str = ""):
    return {"niche": niche, "briefing": metrics_mod.insights(niche)}
