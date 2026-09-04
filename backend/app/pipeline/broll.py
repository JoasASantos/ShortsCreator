"""Search and download of vertical B-roll from free stock providers.

Three sources, all free to use with a no-cost API key: Pexels, Pixabay and
Coverr. They are queried in order and the results are merged, so a niche query
that returns nothing on one bank can still be covered by another.

Downloads are **ephemeral by default**: the clips land in a per-job scratch
directory and are deleted once the video is rendered. Stock footage is large
(tens of MB per clip, several clips per short) and it is cheaper to fetch it
again than to keep it around — set `BROLL_KEEP_CACHE=true` to reuse instead,
which pays off while iterating on the same short.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import httpx

from ..config import settings

PALETTES = {
    "tecnologia": ("0x0b1120", "0x1d4ed8"),
    "ciberseguranca": ("0x0a0a0a", "0x16a34a"),
    "programacao": ("0x111827", "0x7c3aed"),
    "cinema": ("0x1c1917", "0xb91c1c"),
    "historia": ("0x1c1917", "0xa16207"),
    "ciencia": ("0x082f49", "0x06b6d4"),
    "curiosidades": ("0x172554", "0xdb2777"),
    "negocios": ("0x0f172a", "0x0891b2"),
    "generico": ("0x0f172a", "0x334155"),
}

TIMEOUT = 45.0
DOWNLOAD_TIMEOUT = 180.0
# Name of the scratch folder inside the job directory.
SCRATCH = "broll"


def providers_ready() -> list[str]:
    """Which stock banks have a key configured."""
    ready = []
    if settings.pexels_api_key:
        ready.append("pexels")
    if settings.pixabay_api_key:
        ready.append("pixabay")
    if settings.coverr_api_key:
        ready.append("coverr")
    return ready


def search_clips(query: str, count: int = 3) -> list[str]:
    """URLs of vertical videos for one query, merged across the banks.

    Each bank is asked for the full count rather than the remainder: a query
    that is strong on one and weak on another still fills up, and the extra
    URLs are what makes a multi-scene background possible.
    """
    urls: list[str] = []
    for search in (_pexels, _pixabay, _coverr):
        if len(urls) >= count:
            break
        try:
            urls += [u for u in search(query, count) if u not in urls]
        except Exception:  # noqa: BLE001 — a bank being down is not fatal
            continue
    return urls[:count]


def _pexels(query: str, count: int) -> list[str]:
    if not settings.pexels_api_key:
        return []
    resp = httpx.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": settings.pexels_api_key},
        params={"query": query, "orientation": "portrait",
                "size": "medium", "per_page": count},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    urls: list[str] = []
    for video in resp.json().get("videos", []):
        files = sorted(
            (f for f in video.get("video_files", []) if f.get("height")),
            key=lambda f: abs(f["height"] - 1920),
        )
        if files:
            urls.append(files[0]["link"])
    return urls


def _pixabay(query: str, count: int) -> list[str]:
    if not settings.pixabay_api_key:
        return []
    resp = httpx.get(
        "https://pixabay.com/api/videos/",
        params={"key": settings.pixabay_api_key, "q": query,
                "per_page": max(count, 3), "safesearch": "true"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    urls: list[str] = []
    for hit in resp.json().get("hits", []):
        video = hit.get("videos", {})
        pick = video.get("large") or video.get("medium") or video.get("small")
        if pick and pick.get("url"):
            urls.append(pick["url"])
    return urls[:count]


def _coverr(query: str, count: int) -> list[str]:
    """Coverr ships mostly cinematic, loopable footage — good filler when the
    other two return literal stock imagery."""
    if not settings.coverr_api_key:
        return []
    resp = httpx.get(
        "https://api.coverr.co/videos",
        params={"query": query, "page_size": count,
                "urls": "true", "api_key": settings.coverr_api_key},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    urls: list[str] = []
    for hit in resp.json().get("hits", []):
        link = (hit.get("urls") or {}).get("mp4_download") or (hit.get("urls") or {}).get("mp4")
        if link:
            urls.append(link)
    return urls[:count]


def scratch_dir(job_dir: Path | None) -> Path:
    """Where the downloads go. Inside the job while it renders, or the shared
    cache when the clips are meant to be kept."""
    if job_dir is not None and not settings.broll_keep_cache:
        target = job_dir / SCRATCH
        target.mkdir(parents=True, exist_ok=True)
        return target
    return settings.cache_dir


def download(url: str, log=lambda m: None, job_dir: Path | None = None) -> Path | None:
    name = hashlib.sha1(url.encode()).hexdigest()[:16]
    dest = scratch_dir(job_dir) / f"broll_{name}.mp4"
    if dest.exists() and dest.stat().st_size > 4096:
        return dest
    try:
        with httpx.stream("GET", url, timeout=DOWNLOAD_TIMEOUT,
                          follow_redirects=True) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes(1 << 16):
                    fh.write(chunk)
    except Exception as exc:
        log(f"Failed to download b-roll: {exc}")
        dest.unlink(missing_ok=True)
        return None
    return dest


def fetch_for_queries(queries: list[str], log=lambda m: None,
                      job_dir: Path | None = None,
                      per_query: int = 1) -> list[Path]:
    """Clips for a list of queries, in order.

    `per_query` above 1 is what turns a single flat background into a sequence
    of different scenes — the pipeline asks for more when the script has few
    segments but the short is long.
    """
    clips: list[Path] = []
    seen: set[str] = set()
    for query in queries:
        if not query:
            continue
        found = 0
        for url in search_clips(query, count=per_query + 2):
            if url in seen:
                continue
            seen.add(url)
            path = download(url, log, job_dir)
            if path:
                clips.append(path)
                found += 1
                if found >= per_query:
                    break
    return clips


def cleanup(job_dir: Path, log=lambda m: None) -> int:
    """Delete the stock footage downloaded for this job.

    Called once the render is done. Stock clips are big and a short only needs
    them while ffmpeg is running; keeping them would grow the disk without
    bound, one short at a time.
    """
    if settings.broll_keep_cache:
        return 0
    target = job_dir / SCRATCH
    if not target.exists():
        return 0
    freed = sum(f.stat().st_size for f in target.glob("*") if f.is_file())
    shutil.rmtree(target, ignore_errors=True)
    if freed:
        log(f"B-roll cleared: {freed / 1_048_576:.1f} MB freed")
    return freed


def palette(niche: str) -> tuple[str, str]:
    return PALETTES.get(niche, PALETTES["generico"])
