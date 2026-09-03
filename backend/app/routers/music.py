"""Soundtrack library: upload your audio files and pick one per job."""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import settings

router = APIRouter(prefix="/api/music", tags=["music"])

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}


def music_dir() -> Path:
    path = settings.assets_dir / "music"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True,
        )
        return round(float(out.stdout.strip()), 1)
    except Exception:
        return 0.0


@router.get("")
def list_tracks():
    tracks = []
    for path in sorted(music_dir().iterdir()):
        if path.suffix.lower() in AUDIO_EXT:
            tracks.append({
                "id": path.name,
                "name": path.stem,
                "duration": _duration(path),
                "size_bytes": path.stat().st_size,
            })
    return tracks


@router.post("")
async def upload_track(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXT:
        raise HTTPException(400, f"Unsupported audio format: {ext or '(none)'}")

    # the file name is the id: predictable and easy to manage inside the folder
    safe = Path(file.filename or "trilha").name.replace("/", "_")
    dest = music_dir() / safe
    counter = 1
    while dest.exists():
        dest = music_dir() / f"{Path(safe).stem}_{counter}{ext}"
        counter += 1

    size = 0
    limit = settings.max_upload_mb * 1024 * 1024
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File larger than {settings.max_upload_mb}MB")
            fh.write(chunk)

    return {"id": dest.name, "name": dest.stem,
            "duration": _duration(dest), "size_bytes": size}


@router.get("/{track_id}/preview")
def preview(track_id: str):
    path = resolve(track_id)
    if path is None:
        raise HTTPException(404, "Track not found")
    return FileResponse(path, media_type="audio/mpeg")


@router.delete("/{track_id}")
def delete_track(track_id: str):
    path = resolve(track_id)
    if path is None:
        raise HTTPException(404, "Track not found")
    path.unlink()
    return {"deleted": track_id}


def resolve(track_id: str) -> Path | None:
    """Turns a track id into a path, blocking directory traversal."""
    if not track_id:
        return None
    candidate = (music_dir() / Path(track_id).name).resolve()
    if candidate.parent != music_dir().resolve() or not candidate.exists():
        return None
    return candidate
