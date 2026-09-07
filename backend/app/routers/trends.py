from __future__ import annotations

from fastapi import APIRouter, Query

from ..pipeline import trends as trends_mod

router = APIRouter(prefix="/api/trends", tags=["trends"])


@router.get("")
def list_trends(niche: str = Query("generico"), geo: str = Query("BR", max_length=2)):
    geo = geo.upper()
    items = trends_mod.fetch(niche, geo)
    # `pending`: units (the LLM-curated web search, mostly) still being fetched
    # in the background — the screen asks again in a moment when it is not empty
    return {"niche": niche, "geo": geo, "items": items[:60],
            "sources": trends_mod.sources_status(niche, geo),
            "pending": trends_mod.pending(niche, geo)}
