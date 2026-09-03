"""Trend radar: what is rising right now, by niche, without an API key.

Free, public sources:
  - Google Trends (daily RSS per country)
  - Reddit "rising" for the niche's subreddits (public JSON)
  - Hacker News top (Algolia) for technology/programming/security
  - YouTube "most popular" whenever a YouTube account is connected
    (reuses the OAuth already done — no extra key)

Each item becomes a short candidate with one click. Results are cached for
30 minutes: a trend does not change on every F5, and the sources appreciate it.
"""
from __future__ import annotations

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from html import unescape  # noqa: F401  (used by both RSS sources)

import httpx

from .. import db

# www.reddit.com returns 403 for a bot User-Agent ever since they closed the
# public API; old.reddit.com still serves the JSON with a browser UA.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
TIMEOUT = 15.0
CACHE_TTL = 1800
WEEK = 7 * 24 * 3600

SUBREDDITS = {
    "tecnologia": ["technology", "gadgets", "brasil"],
    "ciberseguranca": ["cybersecurity", "netsec", "hacking"],
    "programacao": ["programming", "webdev", "brdev"],
    "cinema": ["movies", "television", "cinema"],
    "historia": ["history", "todayilearned", "HistoriaEmPortugues"],
    "ciencia": ["science", "space", "askscience"],
    "curiosidades": ["todayilearned", "interestingasfuck", "Damnthatsinteresting"],
    "negocios": ["business", "Entrepreneur", "investimentos"],
    "generico": ["popular", "brasil"],
}

HN_NICHES = {"tecnologia", "ciberseguranca", "programacao"}

# Cache PER SOURCE, not per query: Reddit rate-limits per IP aggressively, and
# a block from it must not wipe out what the other sources already delivered.
# It is also what keeps us from hammering the APIs on every F5.
_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def _sources() -> tuple:
    # resolved at call time: the functions are defined further below
    return (_google_trends, _reddit, _hackernews, _youtube_popular)


def fetch(niche: str = "generico", geo: str = "BR") -> list[dict]:
    items: list[dict] = []
    for source in _sources():
        items.extend(_cached_source(source, niche, geo))
    items = _dedupe(items)
    items.sort(key=lambda i: i.get("heat", 0), reverse=True)
    return items


def _cached_source(source, niche: str, geo: str) -> list[dict]:
    key = f"{source.__name__}:{niche}:{geo}"
    now = time.time()
    with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1]

    try:
        items = source(niche, geo)
    except Exception:  # noqa: BLE001 — a source being down must not break the radar
        items = []

    with _lock:
        if items:
            _cache[key] = (now, items)
        elif cached:
            # the source just failed (rate limiting, for instance): better to
            # serve the previous result, however stale, than to drop it
            return cached[1]
        else:
            # nothing before: cache the empty result for less time and retry soon
            _cache[key] = (now - CACHE_TTL + 120, [])
    return items


def sources_status(niche: str = "generico", geo: str = "BR") -> list[dict]:
    """Which sources have a cached result — the screen shows this to make it
    clear when Reddit is rate-limiting, instead of pretending it isn't there."""
    out = []
    with _lock:
        for source in _sources():
            cached = _cache.get(f"{source.__name__}:{niche}:{geo}")
            out.append({
                "source": source.__name__.lstrip("_"),
                "items": len(cached[1]) if cached else 0,
                "age_seconds": int(time.time() - cached[0]) if cached else None,
            })
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for item in items:
        norm = re.sub(r"\W+", " ", item["title"].lower()).strip()[:60]
        if norm in seen or not norm:
            continue
        seen.add(norm)
        out.append(item)
    return out


# ------------------------------------------------------------------ sources

def _google_trends(niche: str, geo: str) -> list[dict]:
    r = httpx.get(f"https://trends.google.com/trending/rss?geo={geo}",
                  headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    for i, item in enumerate(root.iter("item")):
        title = unescape(item.findtext("title") or "").strip()
        if not title:
            continue
        traffic = (item.findtext("ht:approx_traffic", namespaces=ns) or "").replace("+", "")
        heat = _parse_traffic(traffic) or max(100 - i * 4, 10)
        news = item.find("ht:news_item", ns)
        snippet = ""
        url = ""
        if news is not None:
            snippet = unescape(news.findtext("ht:news_item_title", namespaces=ns) or "")
            url = news.findtext("ht:news_item_url", namespaces=ns) or ""
        out.append({"source": "Google Trends", "title": title, "snippet": snippet,
                    "url": url, "heat": heat,
                    # structured instead of a ready-made sentence: the UI speaks
                    # five languages and formats this itself
                    "heat_kind": "searches" if traffic else "rising",
                    "heat_data": {"searches": traffic} if traffic else {}})
    return out[:20]


def _parse_traffic(raw: str) -> int:
    raw = raw.strip().upper().replace(".", "").replace(",", "")
    match = re.match(r"(\d+)\s*([KM]?)", raw)
    if not match:
        return 0
    n, unit = int(match.group(1)), match.group(2)
    return n * {"K": 1000, "M": 1_000_000}.get(unit, 1) // 100


ATOM = {"a": "http://www.w3.org/2005/Atom"}


def _reddit(niche: str, geo: str) -> list[dict]:
    """Atom feed of `rising`. The public JSON became a 403 (and old.reddit
    returns a welcome page with status 200, which is worse), but the RSS is
    still open. It carries no score, so heat comes from the position in the
    list — which is already the "rising" order we care about."""
    out = []
    subs = SUBREDDITS.get(niche, SUBREDDITS["generico"])[:3]
    for index, sub in enumerate(subs):
        if index:
            time.sleep(1.2)   # Reddit shuts the door on bursts of requests
        r = httpx.get(f"https://www.reddit.com/r/{sub}/rising.rss?limit=8",
                      headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True)
        if r.status_code != 200 or "xml" not in r.headers.get("content-type", ""):
            continue
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            continue
        for position, entry in enumerate(root.findall("a:entry", ATOM)):
            title = unescape(entry.findtext("a:title", default="", namespaces=ATOM)).strip()
            if not title:
                continue
            link = entry.find("a:link", ATOM)
            out.append({
                "source": f"r/{sub}", "title": title, "snippet": "",
                "url": link.get("href", "") if link is not None else "",
                "heat": max(60 - position * 5, 8),
                "heat_kind": "reddit_rising",
                "heat_data": {"position": position + 1, "sub": sub},
            })
    return out


def _hackernews(niche: str, geo: str) -> list[dict]:
    """`tags=front_page` crossed with `query` almost never matches (the front
    page holds ~30 items), so with a search term we go for the last week's
    stories ordered by relevance; without one, for today's front page."""
    if niche not in HN_NICHES:
        return []
    query = {"ciberseguranca": "security", "programacao": "programming"}.get(niche, "")
    params: dict = {"hitsPerPage": 12, "User-Agent": UA}
    if query:
        params = {"query": query, "tags": "story", "hitsPerPage": 12,
                  "numericFilters": f"created_at_i>{int(time.time()) - WEEK}"}
    else:
        params = {"tags": "front_page", "hitsPerPage": 12}
    r = httpx.get("https://hn.algolia.com/api/v1/search", params=params,
                  headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for hit in r.json().get("hits", []):
        if not (hit.get("title") or "").strip():
            continue
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        out.append({
            "source": "Hacker News", "title": (hit.get("title") or "").strip(),
            "snippet": "", "url": hit.get("url") or
            f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            "heat": points // 5 + comments // 2,
            "heat_kind": "points_comments",
            "heat_data": {"points": points, "comments": comments},
        })
    return out


def _youtube_popular(niche: str, geo: str) -> list[dict]:
    account = next((a for a in db.list_accounts() if a["platform"] == "youtube"), None)
    if account is None:
        return []
    from googleapiclient.discovery import build

    from .publishers import youtube

    creds = youtube._credentials(json.loads(account["credentials_json"]))  # noqa: SLF001
    service = build("youtube", "v3", credentials=creds)
    category = {"tecnologia": "28", "ciberseguranca": "28", "programacao": "28",
                "cinema": "1", "ciencia": "28", "negocios": "25"}.get(niche)
    kwargs = {"part": "snippet,statistics", "chart": "mostPopular",
              "regionCode": geo, "maxResults": 12}
    if category:
        kwargs["videoCategoryId"] = category
    resp = service.videos().list(**kwargs).execute()
    out = []
    for item in resp.get("items", []):
        views = int(item.get("statistics", {}).get("viewCount", 0))
        out.append({
            "source": "YouTube em alta", "title": item["snippet"]["title"],
            "snippet": item["snippet"].get("channelTitle", ""),
            "url": f"https://youtube.com/watch?v={item['id']}",
            "heat": views // 20000,
            "heat_kind": "views", "heat_data": {"views": views},
        })
    return out
