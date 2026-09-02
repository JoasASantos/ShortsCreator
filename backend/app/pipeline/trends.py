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
from html import unescape  # noqa: F401  (usado nas duas fontes de RSS)

import httpx

from .. import db

# O www.reddit.com devolve 403 para User-Agent de robô desde que fecharam a
# API pública; old.reddit.com continua servindo o JSON com UA de navegador.
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

# Cache POR FONTE, não por consulta: o Reddit limita requisições por IP de
# forma agressiva, e um bloqueio dele não pode apagar o que as outras fontes
# já entregaram. Também é o que evita marretar as APIs a cada F5.
_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def _sources() -> tuple:
    # resolvido em tempo de chamada: as funções são definidas abaixo
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
    except Exception:  # noqa: BLE001 — fonte fora do ar não derruba o radar
        items = []

    with _lock:
        if items:
            _cache[key] = (now, items)
        elif cached:
            # a fonte falhou agora (limite de requisições, por exemplo): melhor
            # servir o resultado anterior, mesmo velho, do que sumir com ela
            return cached[1]
        else:
            # sem nada antes: guarda o vazio por menos tempo e tenta de novo logo
            _cache[key] = (now - CACHE_TTL + 120, [])
    return items


def sources_status(niche: str = "generico", geo: str = "BR") -> list[dict]:
    """Quais fontes têm resultado em cache — a tela mostra isso para deixar
    claro quando o Reddit está limitando em vez de fingir que não existe."""
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


ATOM = {"a": "http://www.w3.org/2005/Atom"}


def _reddit(niche: str, geo: str) -> list[dict]:
    """Feed Atom de `rising`. O JSON público virou 403 (e o old.reddit devolve
    uma página de boas-vindas com status 200, que é pior), mas o RSS continua
    aberto. Ele não traz score, então o calor vem da posição na lista — que já
    é a ordem de "subindo" que interessa."""
    out = []
    subs = SUBREDDITS.get(niche, SUBREDDITS["generico"])[:3]
    for index, sub in enumerate(subs):
        if index:
            time.sleep(1.2)   # o Reddit fecha a porta em rajada de requisições
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
                "heat_label": f"subindo · #{position + 1} em r/{sub}",
            })
    return out


def _hackernews(niche: str, geo: str) -> list[dict]:
    """`tags=front_page` cruzado com `query` quase nunca casa (a capa tem ~30
    itens), então com termo de busca vamos nas stories da última semana
    ordenadas por relevância; sem termo, na capa de hoje."""
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
