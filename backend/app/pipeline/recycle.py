"""What an account's audience actually watched, ranked.

Paste a handle — `@alguem` on TikTok, Instagram or YouTube — and get that
profile's videos with their view counts, biggest first. That list is the input
to the thing people actually want: take what already worked somewhere, and
remake it in another language.

Only metadata is read here. Nothing is downloaded until a specific video is
picked, because a profile listing is cheap and forty downloads are not.

What this does NOT decide is whether reposting someone else's video is yours
to do. Translating and re-narrating a video you do not own is a copyright
question and a platform-rules question, and both depend on what you add, who
you credit and where you publish. The uploader's name and the original link
travel with every item precisely so that crediting is the easy path.
"""
from __future__ import annotations

import json
import re
import subprocess

from ..config import settings
from .ingest import ytdlp_command

# Where a handle lives on each platform. YouTube's /shorts is deliberate: a
# channel's long videos are not what someone recycling shorts is looking for.
PLATFORMS = {
    "tiktok": "https://www.tiktok.com/@{handle}",
    "instagram": "https://www.instagram.com/{handle}/",
    "youtube": "https://www.youtube.com/@{handle}/shorts",
}

HOST_HINTS = {
    "tiktok.com": "tiktok",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
}

# Listing a profile is metadata only, but yt-dlp still walks pages of it.
SCAN_TIMEOUT = 180.0
MAX_ITEMS = 40
DEFAULT_ITEMS = 12


class ProfileUnavailable(RuntimeError):
    """The profile could not be listed, with the reason in the message —
    private, gone, or a platform that wants a logged-in session."""


def parse_target(value: str, platform: str = "") -> tuple[str, str]:
    """('handle', 'platform') from whatever was pasted.

    `@alguem`, `alguem`, a profile URL, or a URL with a platform already
    chosen. A full URL wins over the dropdown: someone who pasted a TikTok
    link while the selector said Instagram meant the link.
    """
    raw = (value or "").strip()
    if not raw:
        raise ProfileUnavailable("Type a handle, like @alguem.")

    found = ""
    for host, name in HOST_HINTS.items():
        if host in raw.lower():
            found = name
            break
    if found:
        platform = found
        match = re.search(r"@([A-Za-z0-9._\-]+)", raw)
        if not match:
            # instagram.com/alguem/ has no @ in it
            match = re.search(r"(?:instagram|youtube)\.com/(?:c/)?([A-Za-z0-9._\-]+)",
                              raw, re.I)
        if not match:
            raise ProfileUnavailable(f"No handle found in {raw}")
        handle = match.group(1)
    else:
        handle = raw.lstrip("@").strip("/")

    platform = (platform or "tiktok").lower()
    if platform not in PLATFORMS:
        raise ProfileUnavailable(
            f"Unknown platform: {platform}. Use {', '.join(PLATFORMS)}.")
    if not re.fullmatch(r"[A-Za-z0-9._\-]{1,60}", handle):
        raise ProfileUnavailable(f"That does not look like a handle: {handle}")
    return handle, platform


def profile_url(handle: str, platform: str) -> str:
    return PLATFORMS[platform].format(handle=handle)


def scan(target: str, platform: str = "", limit: int = DEFAULT_ITEMS) -> dict:
    """The account's videos, most watched first.

    `--flat-playlist` keeps this to one listing request per page instead of
    resolving every video: the view count is in the listing, and the point is
    to choose before downloading anything.
    """
    handle, platform = parse_target(target, platform)
    url = profile_url(handle, platform)
    limit = max(1, min(int(limit or DEFAULT_ITEMS), MAX_ITEMS))

    cmd = [*ytdlp_command(), "--flat-playlist", "--dump-single-json",
           "--no-warnings", "--playlist-end", str(limit)]
    if settings.ytdlp_cookies:
        cmd += ["--cookies", settings.ytdlp_cookies]
    elif settings.ytdlp_cookies_browser:
        cmd += ["--cookies-from-browser", settings.ytdlp_cookies_browser]
    cmd.append(url)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=SCAN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise ProfileUnavailable(
            f"{platform} took too long to answer for @{handle}.") from exc
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        raise ProfileUnavailable(_why(platform, handle, proc.stderr))

    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise ProfileUnavailable(
            f"{platform} answered something that is not a listing.") from exc

    items = [_entry(raw, platform) for raw in (payload.get("entries") or [])]
    items = [item for item in items if item["url"]]
    # Most watched first, and an item with no count sinks rather than leading:
    # "unknown" is not "zero", but it is also not evidence of anything.
    items.sort(key=lambda item: (item["views"] or -1), reverse=True)

    return {
        "handle": handle,
        "platform": platform,
        "profile_url": url,
        "author": payload.get("uploader") or payload.get("channel") or f"@{handle}",
        "items": items[:limit],
        "note": ("Nem toda plataforma informa visualizações no índice do "
                 "perfil; onde não vier, a ordem é a do próprio perfil."
                 if any(item["views"] is None for item in items) else ""),
    }


def _entry(raw: dict, platform: str) -> dict:
    if not isinstance(raw, dict):
        return {"url": ""}
    views = raw.get("view_count")
    return {
        "id": str(raw.get("id") or ""),
        "url": raw.get("url") or raw.get("webpage_url") or "",
        "title": (raw.get("title") or raw.get("description") or "").strip()[:200],
        "views": int(views) if isinstance(views, (int, float)) else None,
        "likes": raw.get("like_count"),
        "duration": raw.get("duration"),
        "thumbnail": _thumbnail(raw),
        "uploader": raw.get("uploader") or raw.get("channel") or "",
        "platform": platform,
    }


def _thumbnail(raw: dict) -> str:
    if raw.get("thumbnail"):
        return str(raw["thumbnail"])
    thumbs = raw.get("thumbnails") or []
    return str(thumbs[-1].get("url", "")) if thumbs else ""


def _why(platform: str, handle: str, stderr: str) -> str:
    """yt-dlp's own failure, turned into the next step.

    The three that actually happen are a private account, a handle that does
    not exist, and Instagram wanting a session — and they need different
    answers, not one "could not list".
    """
    text = (stderr or "").strip()
    lowered = text.lower()
    if "login" in lowered or "cookies" in lowered or "rate-limit" in lowered:
        return (f"{platform} is asking for a logged-in session to list "
                f"@{handle}. Export your cookies and point YTDLP_COOKIES at "
                f"the file, or set YTDLP_COOKIES_BROWSER=chrome to take them "
                f"from the browser you are already signed in to.")
    if "private" in lowered:
        return f"@{handle} is private on {platform}."
    if "not found" in lowered or "404" in lowered or "unable to find" in lowered:
        return f"@{handle} does not exist on {platform}."
    last = text.splitlines()[-1][:300] if text else "no reason given"
    return f"{platform} did not list @{handle}: {last}"
