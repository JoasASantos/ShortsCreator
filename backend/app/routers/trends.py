from __future__ import annotations

from fastapi import APIRouter, Query

from ..pipeline import trends as trends_mod

router = APIRouter(prefix="/api/trends", tags=["trends"])


@router.get("")
def list_trends(niche: str = Query("generico"), geo: str = Query("BR", max_length=2)):
    geo = geo.upper()
    items = trends_mod.fetch(niche, geo)
    return {"niche": niche, "geo": geo, "items": items[:40],
            "sources": trends_mod.sources_status(niche, geo)}
