"""Radar de tendências: o que está subindo agora, por nicho, sem chave de API.

Fontes gratuitas e públicas:
  - Google Trends (RSS diário por país)
  - Reddit "rising" dos subreddits do nicho (JSON público)
  - Hacker News top (Algolia) para tecnologia/programação/segurança
  - YouTube "mais populares" quando há uma conta do YouTube conectada
    (reaproveita o OAuth já feito — sem chave extra)

Cada item vira um candidato a short com um clique. Resultado em cache de
30 minutos: tendência não muda a cada F5 e as fontes agradecem.
"""
from __future__ import annotations

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from html import unescape

import httpx

from .. import db

UA = "ShortsCreator/1.0 (+https://github.com/JoasASantos/ShortsCreator)"
TIMEOUT = 15.0
CACHE_TTL = 1800

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

_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def fetch(niche: str = "generico", geo: str = "BR") -> list[dict]:
    key = f"{niche}:{geo}"
    with _lock:
        cached = _cache.get(key)
        if cached and time.time() - cached[0] < CACHE_TTL:
            return cached[1]

    items: list[dict] = []
    for source in (_google_trends, _reddit, _hackernews, _youtube_popular):
        try:
            items.extend(source(niche, geo))
        except Exception:  # noqa: BLE001 — fonte fora do ar não derruba o radar
            continue

    items = _dedupe(items)
    items.sort(key=lambda i: i.get("heat", 0), reverse=True)
    with _lock:
        _cache[key] = (time.time(), items)
    return items


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


# ------------------------------------------------------------------ fontes

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
                    "url": url, "heat": heat, "heat_label": f"{traffic}+ buscas" if traffic else "em alta"})
    return out[:20]


def _parse_traffic(raw: str) -> int:
    raw = raw.strip().upper().replace(".", "").replace(",", "")
    match = re.match(r"(\d+)\s*([KM]?)", raw)
    if not match:
        return 0
    n, unit = int(match.group(1)), match.group(2)
    return n * {"K": 1000, "M": 1_000_000}.get(unit, 1) // 100


def _reddit(niche: str, geo: str) -> list[dict]:
    out = []
    for sub in SUBREDDITS.get(niche, SUBREDDITS["generico"])[:3]:
        r = httpx.get(f"https://www.reddit.com/r/{sub}/rising.json?limit=8",
                      headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True)
        if r.status_code != 200:
            continue
        for child in r.json().get("data", {}).get("children", []):
            d = child.get("data", {})
            if d.get("over_18") or d.get("stickied"):
                continue
            score = int(d.get("score", 0))
            comments = int(d.get("num_comments", 0))
            out.append({
                "source": f"r/{sub}", "title": unescape(d.get("title", "")).strip(),
                "snippet": (d.get("selftext") or "")[:200].replace("\n", " "),
                "url": d.get("url") if not d.get("is_self") else
                       f"https://reddit.com{d.get('permalink', '')}",
                "heat": score // 10 + comments, "heat_label": f"{score} ↑ · {comments} comentários",
            })
    return out


def _hackernews(niche: str, geo: str) -> list[dict]:
    if niche not in HN_NICHES:
        return []
    query = {"ciberseguranca": "security", "programacao": ""}.get(niche, "")
    r = httpx.get("https://hn.algolia.com/api/v1/search",
                  params={"tags": "front_page", "query": query, "hitsPerPage": 12},
                  headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for hit in r.json().get("hits", []):
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        out.append({
            "source": "Hacker News", "title": (hit.get("title") or "").strip(),
            "snippet": "", "url": hit.get("url") or
            f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            "heat": points // 5 + comments // 2,
            "heat_label": f"{points} pontos · {comments} comentários",
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
            "heat": views // 20000, "heat_label": f"{views:,} views".replace(",", "."),
        })
    return out
