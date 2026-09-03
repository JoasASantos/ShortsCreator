"""Final files served under a stable URL — the Instagram Graph API accepts no
upload: it downloads the MP4 (and the cover) from a public URL. Only what
lives in data/outputs and the job's cover go out through here; no directory
listing."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..config import settings

router = APIRouter(prefix="/api/outputs", tags=["outputs"])

_JOB = re.compile(r"^(job_[0-9a-f]{12})\.(mp4|jpg)$")


@router.get("/{filename}")
def get_output(filename: str):
    match = _JOB.match(filename)
    if not match:
        raise HTTPException(404, "File not available")
    job_id, ext = match.groups()
    if ext == "mp4":
        path = settings.outputs_dir / filename
        media = "video/mp4"
    else:
        path = settings.jobs_dir / job_id / "cover.jpg"
        media = "image/jpeg"
    if not path.exists():
        raise HTTPException(404, "File not generated yet")
    return FileResponse(path, media_type=media)
