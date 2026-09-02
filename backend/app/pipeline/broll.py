"""Busca e download de B-roll vertical (Pexels/Pixabay) com cache local."""
from __future__ import annotations

import hashlib
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


def _cache_path(url: str) -> Path:
    name = hashlib.sha1(url.encode()).hexdigest()[:16]
    return settings.cache_dir / f"broll_{name}.mp4"


def search_clips(query: str, count: int = 3) -> list[str]:
    """Retorna URLs de vídeos verticais. Pexels primeiro, Pixabay como fallback."""
    urls: list[str] = []
    if settings.pexels_api_key:
        urls += _pexels(query, count)
    if len(urls) < count and settings.pixabay_api_key:
        urls += _pixabay(query, count - len(urls))
    return urls[:count]


def _pexels(query: str, count: int) -> list[str]:
    try:
        resp = httpx.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": settings.pexels_api_key},
            params={"query": query, "orientation": "portrait",
                    "size": "medium", "per_page": count},
            timeout=45,
        )
        resp.raise_for_status()
    except Exception:
        return []
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
    try:
        resp = httpx.get(
            "https://pixabay.com/api/videos/",
            params={"key": settings.pixabay_api_key, "q": query,
                    "per_page": max(count, 3), "safesearch": "true"},
            timeout=45,
        )
        resp.raise_for_status()
    except Exception:
        return []
    urls: list[str] = []
    for hit in resp.json().get("hits", []):
        video = hit.get("videos", {})
        pick = video.get("large") or video.get("medium") or video.get("small")
        if pick and pick.get("url"):
            urls.append(pick["url"])
    return urls[:count]


def download(url: str, log=lambda m: None) -> Path | None:
    dest = _cache_path(url)
    if dest.exists() and dest.stat().st_size > 4096:
        return dest
    try:
        with httpx.stream("GET", url, timeout=180, follow_redirects=True) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes(1 << 16):
                    fh.write(chunk)
    except Exception as exc:
        log(f"Falha ao baixar b-roll: {exc}")
        dest.unlink(missing_ok=True)
        return None
    return dest


def fetch_for_queries(queries: list[str], log=lambda m: None) -> list[Path]:
    clips: list[Path] = []
    for query in queries:
        if not query:
            continue
        for url in search_clips(query, count=1):
            path = download(url, log)
            if path:
                clips.append(path)
                break
    return clips


def palette(niche: str) -> tuple[str, str]:
    return PALETTES.get(niche, PALETTES["generico"])
