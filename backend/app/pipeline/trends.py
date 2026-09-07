"""Trend radar: what is rising right now, by niche, without an API key.

Free, public sources — dozens of them, so every kind of creator finds
something, not only the technical ones:
  - Google Trends (daily RSS per country)
  - Reddit "rising" for the niche's subreddits (Atom feed)
  - Hacker News (Algolia) for technology/programming/security
  - RSS of real publications per niche (BBC, Guardian, NYT, IGN, Nature,
    STAT, Politico, G1, Folha, Agência Brasil, TecMundo …) — every URL was
    checked with a live request before landing here
  - Web search (Google News search, Bing RSS, DuckDuckGo) curated by the LLM
    into trend candidates with a suggested hook angle
  - YouTube "most popular" whenever a YouTube account is connected
    (reuses the OAuth already done — no extra key)

Each item becomes a short candidate with one click and carries the niches it
belongs to. Results are cached for 30 minutes PER UNIT (one feed, one
subreddit list, one search): a trend does not change on every F5, the sources
appreciate it, and one unit going dark never empties the list.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from html import unescape
from urllib.parse import parse_qs, urlparse

import httpx

from .. import db
from . import llm

log = logging.getLogger(__name__)

# www.reddit.com returns 403 for a bot User-Agent ever since they closed the
# public API; the RSS still answers with a browser UA.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
TIMEOUT = 15.0
FEED_TIMEOUT = 8.0
# How long a request waits for the slow units (the LLM curation, mostly).
# Whatever is not ready by then keeps running in the background, lands in the
# cache and is reported as `pending`, so the screen can ask again shortly.
DEADLINE = 12.0
CACHE_TTL = 1800
WEEK = 7 * 24 * 3600

NICHES = [
    "tecnologia", "ciberseguranca", "programacao", "cinema", "historia",
    "ciencia", "curiosidades", "negocios", "games", "saude", "politica", "generico",
]

SUBREDDITS = {
    "tecnologia": ["technology", "gadgets", "brasil"],
    "ciberseguranca": ["cybersecurity", "netsec", "hacking"],
    "programacao": ["programming", "webdev", "brdev"],
    "cinema": ["movies", "television", "cinema"],
    "historia": ["history", "todayilearned", "HistoriaEmPortugues"],
    "ciencia": ["science", "space", "askscience"],
    "curiosidades": ["todayilearned", "interestingasfuck", "Damnthatsinteresting"],
    "negocios": ["business", "Entrepreneur", "investimentos"],
    "games": ["gaming", "pcgaming", "Games"],
    "saude": ["medicine", "health", "Health"],
    "politica": ["worldnews", "politics", "brasil"],
    "generico": ["popular", "brasil"],
}

HN_NICHES = {"tecnologia", "ciberseguranca", "programacao"}

# YouTube video categories: 28 science & technology, 1 film, 25 news &
# politics, 20 gaming. Health has no category of its own — it stays on the
# region's overall chart.
YOUTUBE_CATEGORY = {"tecnologia": "28", "ciberseguranca": "28", "programacao": "28",
                    "cinema": "1", "ciencia": "28", "negocios": "25",
                    "games": "20", "politica": "25"}

# (label shown on the item, language, url) — every URL answered a live request
# with a real RSS/Atom feed the day it was added. A feed that stops answering
# is skipped for that request and retried two minutes later (see _cached).
FEEDS: dict[str, list[tuple[str, str, str]]] = {
    "tecnologia": [
        ("The Verge", "en", "https://www.theverge.com/rss/index.xml"),
        ("Ars Technica", "en", "https://feeds.arstechnica.com/arstechnica/index"),
        ("TechCrunch", "en", "https://techcrunch.com/feed/"),
        ("Wired", "en", "https://www.wired.com/feed/rss"),
        ("BBC Technology", "en", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
        ("NYT Technology", "en", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
        ("TecMundo", "pt", "https://rss.tecmundo.com.br/feed"),
        ("Olhar Digital", "pt", "https://olhardigital.com.br/feed/"),
        ("Canaltech", "pt", "https://canaltech.com.br/rss/"),
        ("G1 Tecnologia", "pt", "https://g1.globo.com/rss/g1/tecnologia/"),
    ],
    "ciberseguranca": [
        ("Krebs on Security", "en", "https://krebsonsecurity.com/feed/"),
        ("BleepingComputer", "en", "https://www.bleepingcomputer.com/feed/"),
        ("The Hacker News", "en", "https://feeds.feedburner.com/TheHackersNews"),
        ("Dark Reading", "en", "https://www.darkreading.com/rss.xml"),
        ("Wired", "en", "https://www.wired.com/feed/rss"),
        ("Canaltech", "pt", "https://canaltech.com.br/rss/"),
    ],
    "programacao": [
        ("dev.to", "en", "https://dev.to/feed"),
        ("GitHub Blog", "en", "https://github.blog/feed/"),
        ("Lobsters", "en", "https://lobste.rs/rss"),
        ("Stack Overflow Blog", "en", "https://stackoverflow.blog/feed/"),
        ("The Pragmatic Engineer", "en", "https://blog.pragmaticengineer.com/rss/"),
        ("InfoQ", "en", "https://www.infoq.com/feed/"),
        ("Product Hunt", "en", "https://www.producthunt.com/feed"),
    ],
    "cinema": [
        ("Variety", "en", "https://variety.com/feed/"),
        ("The Hollywood Reporter", "en", "https://www.hollywoodreporter.com/feed/"),
        ("Deadline", "en", "https://deadline.com/feed/"),
        ("IndieWire", "en", "https://www.indiewire.com/feed/"),
        ("The Guardian Film", "en", "https://www.theguardian.com/film/rss"),
        ("NYT Movies", "en", "https://rss.nytimes.com/services/xml/rss/nyt/Movies.xml"),
        ("BBC Entertainment", "en", "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"),
    ],
    "historia": [
        ("Smithsonian History", "en", "https://www.smithsonianmag.com/rss/history/"),
        ("HistoryExtra", "en", "https://www.historyextra.com/feed/"),
        ("HistoryNet", "en", "https://www.historynet.com/feed/"),
        ("Aventuras na História", "pt", "https://aventurasnahistoria.com.br/feed/"),
        ("Big Think", "en", "https://bigthink.com/feed/"),
    ],
    "ciencia": [
        ("Nature", "en", "https://www.nature.com/nature.rss"),
        ("ScienceDaily", "en", "https://www.sciencedaily.com/rss/top/science.xml"),
        ("Phys.org", "en", "https://phys.org/rss-feed/"),
        ("Space.com", "en", "https://www.space.com/feeds/all"),
        ("NASA", "en", "https://www.nasa.gov/rss/dyn/breaking_news.rss"),
        ("The Guardian Science", "en", "https://www.theguardian.com/science/rss"),
        ("NYT Science", "en", "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml"),
        ("BBC Science", "en", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
        ("Smithsonian Science", "en", "https://www.smithsonianmag.com/rss/science-nature/"),
        ("G1 Ciência e Saúde", "pt", "https://g1.globo.com/rss/g1/ciencia-e-saude/"),
    ],
    "curiosidades": [
        ("Live Science", "en", "https://www.livescience.com/feeds/all"),
        ("ScienceAlert", "en", "https://www.sciencealert.com/feed"),
        ("Big Think", "en", "https://bigthink.com/feed/"),
        ("Smithsonian History", "en", "https://www.smithsonianmag.com/rss/history/"),
        ("ScienceDaily", "en", "https://www.sciencedaily.com/rss/top/science.xml"),
        ("Superinteressante", "pt", "https://super.abril.com.br/feed/"),
    ],
    "negocios": [
        ("CNBC", "en", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("Forbes", "en", "https://www.forbes.com/business/feed/"),
        ("Bloomberg Markets", "en", "https://feeds.bloomberg.com/markets/news.rss"),
        ("The Guardian Business", "en", "https://www.theguardian.com/business/rss"),
        ("NYT Business", "en", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
        ("BBC Business", "en", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("InfoMoney", "pt", "https://www.infomoney.com.br/feed/"),
        ("Exame", "pt", "https://exame.com/feed/"),
    ],
    "games": [
        ("IGN", "en", "https://feeds.ign.com/ign/games-all"),
        ("Kotaku", "en", "https://kotaku.com/rss"),
        ("Polygon", "en", "https://www.polygon.com/rss/index.xml"),
        ("Eurogamer", "en", "https://www.eurogamer.net/feed"),
        ("PC Gamer", "en", "https://www.pcgamer.com/rss/"),
        ("GameSpot", "en", "https://www.gamespot.com/feeds/game-news/"),
        ("Rock Paper Shotgun", "en", "https://www.rockpapershotgun.com/feed"),
        ("GamesIndustry.biz", "en", "https://www.gamesindustry.biz/feed"),
        ("The Guardian Games", "en", "https://www.theguardian.com/games/rss"),
        ("IGN Brasil", "pt", "https://br.ign.com/feed.xml"),
        ("GameVício", "pt", "https://www.gamevicio.com/rss/"),
        ("Adrenaline", "pt", "https://www.adrenaline.com.br/feed/"),
    ],
    "saude": [
        ("BBC Health", "en", "https://feeds.bbci.co.uk/news/health/rss.xml"),
        ("STAT News", "en", "https://www.statnews.com/feed/"),
        ("WHO", "en", "https://www.who.int/rss-feeds/news-english.xml"),
        ("ScienceDaily Health", "en", "https://www.sciencedaily.com/rss/top/health.xml"),
        ("Medscape", "en", "https://www.medscape.com/cx/rssfeeds/2700.xml"),
        ("News-Medical", "en", "https://www.news-medical.net/syndication.axd?format=rss"),
        ("NYT Health", "en", "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml"),
        ("Agência Brasil Saúde", "pt", "https://agenciabrasil.ebc.com.br/rss/saude/feed.xml"),
        ("Fiocruz", "pt", "https://portal.fiocruz.br/rss.xml"),
        ("Drauzio Varella", "pt", "https://drauziovarella.uol.com.br/feed/"),
        ("G1 Ciência e Saúde", "pt", "https://g1.globo.com/rss/g1/ciencia-e-saude/"),
    ],
    "politica": [
        ("BBC World", "en", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("BBC Politics", "en", "https://feeds.bbci.co.uk/news/politics/rss.xml"),
        ("Politico", "en", "https://rss.politico.com/politics-news.xml"),
        ("Politico Europe", "en", "https://www.politico.eu/feed/"),
        ("The Hill", "en", "https://thehill.com/feed/"),
        ("NPR Politics", "en", "https://feeds.npr.org/1014/rss.xml"),
        ("The Guardian Politics", "en", "https://www.theguardian.com/politics/rss"),
        ("The Guardian World", "en", "https://www.theguardian.com/world/rss"),
        ("Al Jazeera", "en", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("NYT World", "en", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
        ("NYT Politics", "en", "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml"),
        ("WSJ World", "en", "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
        ("G1 Política", "pt", "https://g1.globo.com/rss/g1/politica/"),
        ("Folha Poder", "pt", "https://feeds.folha.uol.com.br/poder/rss091.xml"),
        ("Poder360", "pt", "https://www.poder360.com.br/feed/"),
        ("Agência Brasil Política", "pt", "https://agenciabrasil.ebc.com.br/rss/politica/feed.xml"),
        ("BBC Brasil", "pt", "https://feeds.bbci.co.uk/portuguese/rss.xml"),
        ("DW Brasil", "pt", "https://rss.dw.com/rdf/rss-br-all"),
        ("CNN Brasil", "pt", "https://www.cnnbrasil.com.br/feed/"),
        ("Veja", "pt", "https://veja.abril.com.br/feed/"),
    ],
    "generico": [
        ("BBC World", "en", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("The Guardian World", "en", "https://www.theguardian.com/world/rss"),
        ("Al Jazeera", "en", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("G1", "pt", "https://g1.globo.com/rss/g1/"),
        ("Folha", "pt", "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml"),
        ("Agência Brasil", "pt", "https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml"),
        ("BBC Brasil", "pt", "https://feeds.bbci.co.uk/portuguese/rss.xml"),
        ("CNN Brasil", "pt", "https://www.cnnbrasil.com.br/feed/"),
    ],
}

# What the web search asks for, per niche and language. Short and topical on
# purpose: the search engines rank recency themselves, and the LLM does the
# curating afterwards.
SEARCH_QUERY: dict[str, dict[str, str]] = {
    "tecnologia": {"pt": "tecnologia novidades", "en": "technology news", "es": "tecnología novedades",
                   "ru": "технологии новости", "zh": "科技 新闻"},
    "ciberseguranca": {"pt": "cibersegurança ataque vazamento", "en": "cybersecurity breach",
                       "es": "ciberseguridad ataque", "ru": "кибербезопасность утечка", "zh": "网络安全 漏洞"},
    "programacao": {"pt": "programação linguagem framework", "en": "programming language release",
                    "es": "programación lenguaje framework", "ru": "программирование релиз", "zh": "编程 语言 发布"},
    "cinema": {"pt": "cinema filme estreia", "en": "movies box office trailer", "es": "cine estreno película",
               "ru": "кино премьера фильм", "zh": "电影 上映 预告"},
    "historia": {"pt": "história descoberta arqueologia", "en": "history discovery archaeology",
                 "es": "historia descubrimiento arqueología", "ru": "история археология открытие", "zh": "历史 考古 发现"},
    "ciencia": {"pt": "ciência descoberta estudo", "en": "science discovery study", "es": "ciencia descubrimiento estudio",
                "ru": "наука открытие исследование", "zh": "科学 发现 研究"},
    "curiosidades": {"pt": "curiosidades fato surpreendente", "en": "surprising facts weird news",
                     "es": "curiosidades dato sorprendente", "ru": "удивительные факты", "zh": "冷知识 奇闻"},
    "negocios": {"pt": "negócios mercado empresas", "en": "business markets companies", "es": "negocios mercado empresas",
                 "ru": "бизнес рынок компании", "zh": "商业 市场 公司"},
    "games": {"pt": "games jogos lançamento", "en": "video games release", "es": "videojuegos lanzamiento",
              "ru": "видеоигры релиз", "zh": "游戏 发售"},
    "saude": {"pt": "saúde medicina estudo", "en": "health medicine study", "es": "salud medicina estudio",
              "ru": "здоровье медицина исследование", "zh": "健康 医学 研究"},
    "politica": {"pt": "política governo congresso", "en": "politics government election",
                 "es": "política gobierno elecciones", "ru": "политика правительство выборы", "zh": "政治 政府 选举"},
    "generico": {"pt": "notícias destaque hoje", "en": "top news today", "es": "noticias destacadas hoy",
                 "ru": "главные новости сегодня", "zh": "今日 头条 新闻"},
}

# geo -> (language for queries and LLM output, Google News hl/gl/ceid)
GEO_LANG = {
    "BR": ("pt", "pt-BR", "BR", "BR:pt-419"),
    "PT": ("pt", "pt-PT", "PT", "PT:pt-150"),
    "ES": ("es", "es", "ES", "ES:es"),
    "RU": ("ru", "ru", "RU", "RU:ru"),
    "CN": ("zh", "zh-CN", "CN", "CN:zh-Hans"),
    "US": ("en", "en-US", "US", "US:en"),
}
LANGUAGE_NAME = {"pt": "português do Brasil", "en": "inglês", "es": "espanhol",
                 "ru": "russo", "zh": "chinês simplificado"}

# Cache PER UNIT, not per query: Reddit rate-limits per IP aggressively, and
# a block from it must not wipe out what the other sources already delivered.
# It is also what keeps us from hammering the APIs on every F5.
_cache: dict[str, tuple[float, list[dict]]] = {}
_inflight: set[str] = set()
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=24, thread_name_prefix="trends")
# Reddit's pause between subreddits — a name of its own so tests can skip it.
_pause = time.sleep


def _units(niche: str, geo: str) -> list[tuple[str, Callable[[], list[dict]]]]:
    """Every independently cached fetch for this niche/geo: (key, thunk)."""
    units: list[tuple[str, Callable[[], list[dict]]]] = [
        (f"google_trends:{geo}", lambda: _google_trends(niche, geo)),
        (f"reddit:{niche}", lambda: _reddit(niche, geo)),
        (f"hackernews:{niche}", lambda: _hackernews(niche, geo)),
        (f"youtube_popular:{niche}:{geo}", lambda: _youtube_popular(niche, geo)),
        (f"web_search:{niche}:{geo}", lambda: _web_search(niche, geo)),
    ]
    for label, lang, url in FEEDS.get(niche, FEEDS["generico"]):
        units.append((f"feed:{url}", lambda label=label, lang=lang, url=url: _feed(label, lang, url)))
    return units


def fetch(niche: str = "generico", geo: str = "BR") -> list[dict]:
    """All units in parallel, up to DEADLINE. The slow ones keep running in
    the background and are served from the cache on the next request."""
    units = _units(niche, geo)
    futures = {_pool.submit(_cached, key, thunk): key for key, thunk in units}
    done, _ = wait(futures, timeout=DEADLINE)
    items: list[dict] = []
    for future in done:
        try:
            # copies: the cached dicts are shared between niches and requests
            items.extend(dict(item) for item in future.result())
        except Exception:  # noqa: BLE001 — _cached already swallows; belt and braces
            log.exception("trends unit %s failed", futures[future])
    lang = GEO_LANG.get(geo, GEO_LANG["US"])[0]
    for item in items:
        item.setdefault("niches", [niche])
        # a universal 0-100 scale for the bar: Google Trends counts searches
        # in the thousands, a feed only knows its position
        item["heat"] = max(1, min(100, int(item.get("heat", 0))))
        if item.get("lang") == lang:
            item["heat"] = min(100, item["heat"] + 10)
    items = _dedupe(items)
    # the niche's own sources first: Google Trends is generic and would
    # otherwise bury a games list under football and celebrities
    items.sort(key=lambda i: (niche in i["niches"] or "generico" == niche, i["heat"]), reverse=True)
    return items


def pending(niche: str = "generico", geo: str = "BR") -> list[str]:
    """Units still being fetched in the background for this niche/geo — the
    screen asks again in a moment when this is not empty."""
    keys = {key for key, _ in _units(niche, geo)}
    with _lock:
        return sorted(k.split(":")[0] for k in _inflight if k in keys)


def _cached(key: str, producer) -> list[dict]:
    now = time.time()
    with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1]
        if key in _inflight:
            # someone else is already fetching it: serve what we have
            return cached[1] if cached else []
        _inflight.add(key)

    try:
        items = producer()
    except Exception as exc:  # noqa: BLE001 — a source being down must not break the radar
        log.info("trends unit %s unavailable: %s", key, str(exc)[:200])
        items = []
    finally:
        with _lock:
            _inflight.discard(key)

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


def _cached_source(source, niche: str, geo: str) -> list[dict]:
    """Kept for the callers that still think in whole sources."""
    return _cached(f"{source.__name__.lstrip('_')}:{niche}:{geo}", lambda: source(niche, geo))


def sources_status(niche: str = "generico", geo: str = "BR") -> list[dict]:
    """Which units have a cached result. No longer shown as a filter; still
    useful to see, in the API, that Reddit is rate-limiting instead of
    pretending it isn't there."""
    out = []
    with _lock:
        for key, _ in _units(niche, geo):
            cached = _cache.get(key)
            out.append({
                "source": key.split(":", 1)[0] if not key.startswith("feed:") else key,
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


def _clean(text: str) -> str:
    """Feed descriptions come as HTML; the screen wants one plain line."""
    text = re.sub(r"<[^>]+>", " ", unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()


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
                    "url": url, "heat": heat, "niches": ["generico"],
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


def _niches_of_sub(sub: str) -> list[str]:
    return [n for n, subs in SUBREDDITS.items() if sub in subs] or ["generico"]


def _reddit(niche: str, geo: str) -> list[dict]:
    """Atom feed of `rising`. The public JSON became a 403 (and old.reddit
    returns a welcome page with status 200, which is worse), but the RSS is
    still open. It carries no score, so heat comes from the position in the
    list — which is already the "rising" order we care about."""
    out = []
    subs = SUBREDDITS.get(niche, SUBREDDITS["generico"])[:3]
    for index, sub in enumerate(subs):
        if index:
            _pause(1.2)   # Reddit shuts the door on bursts of requests
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
                "niches": _niches_of_sub(sub),
            })
    return out


def _hackernews(niche: str, geo: str) -> list[dict]:
    """`tags=front_page` crossed with `query` almost never matches (the front
    page holds ~30 items), so with a search term we go for the last week's
    stories ordered by relevance; without one, for today's front page."""
    if niche not in HN_NICHES:
        return []
    query = {"ciberseguranca": "security", "programacao": "programming"}.get(niche, "")
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
            "niches": [niche],
        })
    return out


def _niches_of_feed(url: str) -> list[str]:
    return [n for n, feeds in FEEDS.items() if any(f[2] == url for f in feeds)] or ["generico"]


def _parse_feed(content: bytes) -> list[tuple[str, str, str]]:
    """(title, link, summary) from RSS 2.0, RSS 1.0 (RDF) or Atom. Namespaces
    are stripped so one loop covers all three."""
    root = ET.fromstring(content)
    entries = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = summary = ""
        for child in node:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "title":
                title = child.text or ""
            elif name == "link":
                link = (child.text or "").strip() or child.get("href", "")
            elif name in ("description", "summary", "content") and not summary:
                summary = child.text or ""
        entries.append((unescape(title).strip(), link.strip(), _clean(summary)[:220]))
    return entries


def _feed(label: str, lang: str, url: str) -> list[dict]:
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=FEED_TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    out = []
    for position, (title, link, summary) in enumerate(_parse_feed(r.content)[:10]):
        if not title:
            continue
        out.append({
            "source": label, "title": title, "snippet": summary, "url": link,
            "heat": max(50 - position * 4, 6),
            "heat_kind": "feed", "heat_data": {"position": position + 1, "source": label},
            "niches": _niches_of_feed(url), "lang": lang,
        })
    return out


# ------------------------------------------------------------ web search

SEARCH_SYSTEM = """Você é um editor de pauta para criadores de vídeos curtos verticais (Shorts, Reels, TikTok).
Recebe resultados brutos de busca na web (título, fonte, resumo, link) sobre um nicho e devolve os assuntos que estão realmente em alta — não a lista inteira.

Regras:
- Agrupe resultados que falam do mesmo fato em UM assunto só.
- Ignore propaganda, cupons, páginas institucionais, listas evergreen e o que não é notícia da semana.
- Nunca invente fatos: só o que os resultados sustentam. Cite a fonte pelo nome.
- Em política, seja factual e atribua as afirmações a quem as fez; não tome partido.
- `title`: o assunto em até 12 palavras. `why`: por que está em alta agora, uma frase. `angle`: um gancho sugerido para abrir o short, uma frase falada. `url`: o link do melhor resultado sobre o assunto. `heat`: de 1 a 100, quanto mais fontes e quanto mais recente, maior.
- Responda APENAS com JSON válido, sem markdown."""

SEARCH_PROMPT = """Nicho: __NICHE__
Escreva `title`, `why` e `angle` em __LANGUAGE__.

Resultados brutos:
__RESULTS__

Devolva no máximo 12 assuntos neste formato:
{"items": [{"title": "...", "why": "...", "angle": "...", "url": "https://...", "heat": 80}]}"""

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "why": {"type": "string"},
                       "angle": {"type": "string"}, "url": {"type": "string"},
                       "heat": {"type": "integer"}},
        "required": ["title", "why", "angle"]}}},
    "required": ["items"],
}


def _web_search(niche: str, geo: str) -> list[dict]:
    """Search the web for the niche and let the LLM pick the trends out of the
    raw results. Three engines, each optional; the LLM curation optional too:
    when it fails the raw headlines are still worth showing. When nothing at
    all answers, the other sources carry the screen."""
    lang, hl, gl, ceid = GEO_LANG.get(geo, GEO_LANG["US"])
    query = SEARCH_QUERY.get(niche, SEARCH_QUERY["generico"]).get(lang) \
        or SEARCH_QUERY.get(niche, SEARCH_QUERY["generico"])["en"]

    raw: list[dict] = []
    for engine in (_search_google_news, _search_bing, _search_duckduckgo):
        try:
            raw.extend(engine(query, lang, hl, gl, ceid))
        except Exception as exc:  # noqa: BLE001 — one engine down is not news
            log.info("trends web search via %s unavailable: %s", engine.__name__, str(exc)[:160])
    raw = _dedupe(raw)
    if not raw:
        log.info("trends web search: no engine answered for %s/%s", niche, geo)
        return []

    lines = [f"- [{r['source']}] {r['title']} — {r['snippet'][:160]} <{r['url']}>" for r in raw[:40]]
    prompt = (SEARCH_PROMPT.replace("__NICHE__", niche)
              .replace("__LANGUAGE__", LANGUAGE_NAME.get(lang, "inglês"))
              .replace("__RESULTS__", "\n".join(lines)))
    try:
        data = llm.complete_json(SEARCH_SYSTEM, prompt, SEARCH_SCHEMA,
                                 max_tokens=3000, purpose="tendencias")
    except Exception as exc:  # noqa: BLE001 — no LLM: the raw headlines still count
        log.info("trends web search: LLM curation unavailable (%s); serving raw results", str(exc)[:160])
        return [{"source": "web_search", "title": r["title"], "snippet": r["snippet"],
                 "url": r["url"], "heat": max(45 - i * 3, 5), "heat_kind": "web_search",
                 "heat_data": {}, "niches": [niche], "lang": lang} for i, r in enumerate(raw[:12])]

    out = []
    for item in data.get("items", []):
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "source": "web_search", "title": title,
            "snippet": str(item.get("why") or "").strip(),
            "angle": str(item.get("angle") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "heat": _int(item.get("heat"), 50),
            "heat_kind": "web_search", "heat_data": {},
            "niches": [niche], "lang": lang,
        })
    return out


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _search_google_news(query: str, lang: str, hl: str, gl: str, ceid: str) -> list[dict]:
    """Google News search as RSS: no key, the last week, the region's language."""
    r = httpx.get("https://news.google.com/rss/search",
                  params={"q": f"{query} when:7d", "hl": hl, "gl": gl, "ceid": ceid},
                  headers={"User-Agent": UA}, timeout=FEED_TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    out = []
    for title, link, summary in _parse_feed(r.content)[:25]:
        # Google News appends " - Publisher" to every title
        source = "Google News"
        if " - " in title:
            title, source = title.rsplit(" - ", 1)
        out.append({"source": source.strip(), "title": title.strip(), "snippet": summary, "url": link})
    return out


def _search_bing(query: str, lang: str, hl: str, gl: str, ceid: str) -> list[dict]:
    """Bing web search offers its results as RSS with `format=rss`."""
    r = httpx.get("https://www.bing.com/search",
                  params={"q": query, "format": "rss", "setlang": hl, "cc": gl},
                  headers={"User-Agent": UA}, timeout=FEED_TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    return [{"source": urlparse(link).netloc.removeprefix("www.") or "Bing", "title": title,
             "snippet": summary, "url": link}
            for title, link, summary in _parse_feed(r.content)[:10] if title]


def _search_duckduckgo(query: str, lang: str, hl: str, gl: str, ceid: str) -> list[dict]:
    """The HTML endpoint answers a few requests and then 202s a bot check for
    a while — fine for a source that is cached half an hour per niche."""
    r = httpx.get("https://html.duckduckgo.com/html/", params={"q": query},
                  headers={"User-Agent": UA}, timeout=FEED_TIMEOUT, follow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"duckduckgo answered {r.status_code}")
    titles = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)
    out = []
    for i, (href, title) in enumerate(titles[:10]):
        # results are redirect links: //duckduckgo.com/l/?uddg=<real url>
        real = parse_qs(urlparse(href).query).get("uddg", [href])[0]
        out.append({"source": urlparse(real).netloc.removeprefix("www.") or "DuckDuckGo",
                    "title": _clean(title), "url": real,
                    "snippet": _clean(snippets[i]) if i < len(snippets) else ""})
    return out


# ------------------------------------------------------------------ youtube

def _youtube_popular(niche: str, geo: str) -> list[dict]:
    account = next((a for a in db.list_accounts() if a["platform"] == "youtube"), None)
    if account is None:
        return []
    from googleapiclient.discovery import build

    from .publishers import youtube

    creds = youtube._credentials(json.loads(account["credentials_json"]))  # noqa: SLF001
    service = build("youtube", "v3", credentials=creds)
    category = YOUTUBE_CATEGORY.get(niche)
    kwargs = {"part": "snippet,statistics", "chart": "mostPopular",
              "regionCode": geo, "maxResults": 12}
    if category:
        kwargs["videoCategoryId"] = category
    resp = service.videos().list(**kwargs).execute()
    out = []
    for item in resp.get("items", []):
        views = int(item.get("statistics", {}).get("viewCount", 0))
        out.append({
            "source": "youtube_popular", "title": item["snippet"]["title"],
            "snippet": item["snippet"].get("channelTitle", ""),
            "url": f"https://youtube.com/watch?v={item['id']}",
            "heat": views // 20000,
            "heat_kind": "views", "heat_data": {"views": views},
            "niches": [niche] if category else ["generico"],
        })
    return out
