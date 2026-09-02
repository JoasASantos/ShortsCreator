from __future__ import annotations

from fastapi import APIRouter, Query

from ..pipeline import trends as trends_mod

router = APIRouter(prefix="/api/trends", tags=["trends"])


@router.get("")
def list_trends(niche: str = Query("generico"), geo: str = Query("BR", max_length=2)):
    items = trends_mod.fetch(niche, geo.upper())
    return {"niche": niche, "geo": geo.upper(), "items": items[:40]}
