from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..pipeline import trends as trends_mod

router = APIRouter(prefix="/api/trends", tags=["trends"])


@router.get("/search")
def search(q: str = Query(..., min_length=2, max_length=200),
           lang: str = Query("pt", max_length=5),
           niche: str = Query("generico")):
    """Busca livre: o assunto que a pessoa quiser, no idioma que escolher."""
    try:
        items = trends_mod.search(q, lang, niche)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — motor de busca fora do ar
        raise HTTPException(502, f"A busca falhou: {exc}") from exc
    return {"query": q, "lang": lang, "items": items,
            "curated": any(item.get("curated") for item in items)}


@router.get("")
def list_trends(niche: str = Query("generico"), geo: str = Query("BR", max_length=2)):
    geo = geo.upper()
    items = trends_mod.fetch(niche, geo)
    # `pending`: units (the LLM-curated web search, mostly) still being fetched
    # in the background — the screen asks again in a moment when it is not empty
    return {"niche": niche, "geo": geo, "items": items[:60],
            "sources": trends_mod.sources_status(niche, geo),
            "pending": trends_mod.pending(niche, geo)}
