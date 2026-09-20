"""Listing what an account's audience actually watched.

Metadata only: this route reads a profile's videos and their view counts so a
person can choose one. Downloading, transcribing and dubbing happen later, in
an ordinary job, and only for the video that was picked.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..pipeline import recycle

router = APIRouter(prefix="/api/recycle", tags=["recycle"])


class ScanRequest(BaseModel):
    target: str = Field(description="@handle or a profile URL")
    platform: str = Field(default="", description="tiktok | instagram | youtube")
    limit: int = Field(default=recycle.DEFAULT_ITEMS, ge=1,
                       le=recycle.MAX_ITEMS)


@router.get("/platforms")
def platforms():
    return {"platforms": sorted(recycle.PLATFORMS)}


@router.post("/scan")
def scan(body: ScanRequest):
    try:
        return recycle.scan(body.target, body.platform, body.limit)
    except recycle.ProfileUnavailable as exc:
        # Private, non-existent, or a platform asking for a session: all of
        # them are things to read and act on, none of them a traceback.
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — yt-dlp misbehaving
        raise HTTPException(502, f"The listing failed: {exc}") from exc
