"""Trend radar: many sources, each one independent, every item tagged with
its niche. The network is faked through httpx; nothing here goes online."""
from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import broll, llm, script, trends
from app.schemas import JobInput

NEW_NICHES = {"games", "saude", "politica"}

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Novo jogo anunciado</title><link href="https://www.reddit.com/r/gaming/a"/></entry>
  <entry><title>Patch quebrou o multiplayer</title><link href="https://www.reddit.com/r/gaming/b"/></entry>
</feed>"""


def _rss(*titles: str, source: str = "") -> bytes:
    items = "".join(
        f"<item><title>{title}{' - ' + source if source else ''}</title>"
        f"<link>https://example.com/{i}</link>"
        f"<description><![CDATA[<p>Resumo &amp; tal</p>]]></description></item>"
        for i, title in enumerate(titles))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>X</title>{items}</channel></rss>'.encode()


def _response(status: int, content: bytes, ctype: str = "application/rss+xml") -> httpx.Response:
    return httpx.Response(status, content=content, headers={"content-type": ctype},
                          request=httpx.Request("GET", "https://example.com"))


@pytest.fixture(autouse=True)
def isolated_radar(monkeypatch):
    """Empty cache per test, no Reddit pauses, and the sources that need the
    outside world (or an LLM) switched off unless a test turns them on."""
    with trends._lock:
        trends._cache.clear()
        trends._inflight.clear()
    monkeypatch.setattr(trends, "_pause", lambda seconds: None)
    for name in ("_google_trends", "_reddit", "_hackernews", "_youtube_popular", "_web_search"):
        monkeypatch.setattr(trends, name, lambda niche, geo: [])
    monkeypatch.setattr(trends, "FEEDS", {"generico": []})
    yield
    with trends._lock:
        trends._cache.clear()
        trends._inflight.clear()


# ------------------------------------------------------------ the new niches

def test_the_new_niches_exist_everywhere_a_niche_is_listed():
    assert NEW_NICHES <= set(trends.NICHES)
    assert NEW_NICHES <= set(trends.SUBREDDITS)
    assert NEW_NICHES <= set(trends.SEARCH_QUERY)
    assert NEW_NICHES <= set(script.NICHE_GUIDE)
    assert NEW_NICHES <= set(broll.PALETTES)
    for niche in trends.NICHES:
        assert set(trends.SEARCH_QUERY[niche]) == {"pt", "en", "es", "ru", "zh"}
        assert niche in script.NICHE_GUIDE


def test_config_lists_the_new_niches():
    body = TestClient(app).get("/api/config").json()
    assert NEW_NICHES <= set(body["niches"])
    assert body["niches"][-1] == "generico"


def test_the_schema_accepts_the_new_niches():
    for niche in NEW_NICHES:
        assert JobInput(source_type="tema", source="x", niche=niche).niche == niche


def test_political_guidance_is_about_craft_not_position():
    guide = script.NICHE_GUIDE["politica"].lower()
    assert "fonte" in guide and "lados" in guide
    assert "opinião" in guide


# ------------------------------------------------------------------- reddit

def test_reddit_asks_the_niche_subreddits_and_tags_the_items(monkeypatch):
    monkeypatch.undo()   # this test wants the real _reddit
    monkeypatch.setattr(trends, "_pause", lambda seconds: None)
    asked: list[str] = []

    def fake_get(url, **kwargs):
        asked.append(url)
        return _response(200, ATOM, "application/atom+xml; charset=UTF-8")

    monkeypatch.setattr(trends.httpx, "get", fake_get)
    items = trends._reddit("games", "BR")

    assert [u.split("/r/")[1].split("/")[0] for u in asked] == ["gaming", "pcgaming", "Games"]
    assert items[0]["source"] == "r/gaming"
    assert items[0]["niches"] == ["games"]
    assert items[0]["heat_kind"] == "reddit_rising"


def test_a_subreddit_shared_by_two_niches_is_tagged_with_both():
    assert set(trends._niches_of_sub("brasil")) >= {"tecnologia", "politica", "generico"}
    assert set(trends._niches_of_sub("todayilearned")) == {"historia", "curiosidades"}


def test_reddit_interstitial_html_is_not_mistaken_for_a_feed(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(trends, "_pause", lambda seconds: None)
    monkeypatch.setattr(trends.httpx, "get", lambda url, **k: _response(
        200, b"<html>welcome to reddit</html>", "text/html"))
    assert trends._reddit("games", "BR") == []


# -------------------------------------------------------------------- feeds

def test_a_dead_feed_does_not_empty_the_list(monkeypatch):
    monkeypatch.setattr(trends, "FEEDS", {
        "games": [("Dead", "en", "https://dead.example/rss"),
                  ("IGN", "en", "https://ign.example/rss"),
                  ("GameVício", "pt", "https://gamevicio.example/rss")],
        "generico": [],
    })

    def fake_get(url, **kwargs):
        if "dead" in url:
            raise httpx.ConnectError("boom", request=httpx.Request("GET", url))
        if "ign" in url:
            return _response(200, _rss("GTA VI ganha data", "Novo Zelda"))
        return _response(200, _rss("Jogo brasileiro vence prêmio"))

    monkeypatch.setattr(trends.httpx, "get", fake_get)
    items = trends.fetch("games", "BR")

    titles = {i["title"] for i in items}
    assert titles == {"GTA VI ganha data", "Novo Zelda", "Jogo brasileiro vence prêmio"}
    assert all(i["niches"] == ["games"] for i in items)
    assert all(i["heat_kind"] == "feed" for i in items)
    assert items[0]["snippet"] == "Resumo & tal"
    # the dead feed is on record as empty, not as an error
    status = {s["source"]: s["items"] for s in trends.sources_status("games", "BR")}
    assert status["feed:https://dead.example/rss"] == 0
    assert status["feed:https://ign.example/rss"] == 2


def test_the_regions_language_ranks_first(monkeypatch):
    monkeypatch.setattr(trends, "FEEDS", {
        "games": [("IGN", "en", "https://ign.example/rss"),
                  ("GameVício", "pt", "https://gamevicio.example/rss")],
        "generico": [],
    })
    monkeypatch.setattr(trends.httpx, "get", lambda url, **k: _response(
        200, _rss("Em inglês" if "ign" in url else "Em português")))

    assert trends.fetch("games", "BR")[0]["title"] == "Em português"
    with trends._lock:
        trends._cache.clear()
    assert trends.fetch("games", "US")[0]["title"] == "Em inglês"


def test_a_feed_shared_by_two_niches_is_tagged_with_both(monkeypatch):
    monkeypatch.setattr(trends, "FEEDS", {
        "ciencia": [("G1 Ciência e Saúde", "pt", "https://g1.example/ciencia")],
        "saude": [("G1 Ciência e Saúde", "pt", "https://g1.example/ciencia")],
        "generico": [],
    })
    assert set(trends._niches_of_feed("https://g1.example/ciencia")) == {"ciencia", "saude"}


def test_feed_parser_reads_atom_too():
    entries = trends._parse_feed(ATOM)
    assert entries[0] == ("Novo jogo anunciado", "https://www.reddit.com/r/gaming/a", "")


def test_every_shipped_feed_has_a_label_a_language_and_an_https_url(monkeypatch):
    monkeypatch.undo()
    for niche, feeds in trends.FEEDS.items():
        assert niche in trends.NICHES
        for label, lang, url in feeds:
            assert label and lang in ("pt", "en") and url.startswith("https://")
    for niche in NEW_NICHES:
        assert len(trends.FEEDS[niche]) >= 5


# --------------------------------------------------------------- web search

def test_search_degrades_silently_when_no_engine_answers(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(trends.httpx, "get", lambda url, **k: (_ for _ in ()).throw(
        httpx.ConnectError("offline", request=httpx.Request("GET", url))))

    def no_llm(*args, **kwargs):
        raise AssertionError("the LLM must not be asked without results")

    monkeypatch.setattr(trends.llm, "complete_json", no_llm)
    assert trends._web_search("games", "BR") == []


def test_search_without_an_llm_still_serves_the_raw_headlines(monkeypatch):
    monkeypatch.undo()

    def fake_get(url, **kwargs):
        if "news.google.com" in url:
            return _response(200, _rss("Lançamento do ano surpreende", source="IGN Brasil"))
        raise httpx.ConnectError("offline", request=httpx.Request("GET", url))

    monkeypatch.setattr(trends.httpx, "get", fake_get)

    def broken_llm(*args, **kwargs):
        raise llm.LLMError("quota")

    monkeypatch.setattr(trends.llm, "complete_json", broken_llm)
    items = trends._web_search("games", "BR")

    assert items[0]["title"] == "Lançamento do ano surpreende"   # publisher suffix stripped
    assert items[0]["source"] == "web_search"
    assert items[0]["niches"] == ["games"]
    assert "angle" not in items[0]


def test_search_curated_by_the_llm_carries_why_and_angle(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(trends.httpx, "get", lambda url, **k: _response(
        200, _rss("Estúdio anuncia sequência", source="Kotaku")))
    seen: dict = {}

    def fake_llm(system, prompt, schema=None, **kwargs):
        seen["prompt"] = prompt
        return {"items": [{"title": "Sequência anunciada", "why": "Trailer viralizou",
                           "angle": "Você lembra do final do primeiro?", "url": "https://k.example/1",
                           "heat": "95"}]}

    monkeypatch.setattr(trends.llm, "complete_json", fake_llm)
    items = trends._web_search("games", "BR")

    assert "Estúdio anuncia sequência" in seen["prompt"]
    assert "português do Brasil" in seen["prompt"]
    assert items == [{"source": "web_search", "title": "Sequência anunciada",
                      "snippet": "Trailer viralizou", "angle": "Você lembra do final do primeiro?",
                      "url": "https://k.example/1", "heat": 95, "heat_kind": "web_search",
                      "heat_data": {}, "niches": ["games"], "lang": "pt"}]


def test_search_query_follows_the_region(monkeypatch):
    monkeypatch.undo()
    asked: list[dict] = []

    def fake_get(url, params=None, **kwargs):
        asked.append(params or {})
        raise httpx.ConnectError("offline", request=httpx.Request("GET", url))

    monkeypatch.setattr(trends.httpx, "get", fake_get)
    trends._web_search("saude", "ES")
    assert asked[0]["q"] == "salud medicina estudio when:7d"
    assert asked[0]["ceid"] == "ES:es"


# ------------------------------------------------------------ the whole radar

def test_the_niches_own_sources_rank_above_the_generic_ones(monkeypatch):
    monkeypatch.setattr(trends, "_google_trends", lambda niche, geo: [
        {"source": "Google Trends", "title": "Final do campeonato", "snippet": "", "url": "",
         "heat": 2000, "heat_kind": "searches", "heat_data": {"searches": "200K"},
         "niches": ["generico"]}])
    monkeypatch.setattr(trends, "FEEDS", {"games": [("IGN", "en", "https://ign.example/rss")],
                                          "generico": []})
    monkeypatch.setattr(trends.httpx, "get", lambda url, **k: _response(200, _rss("Novo Zelda")))

    items = trends.fetch("games", "BR")
    assert [i["title"] for i in items] == ["Novo Zelda", "Final do campeonato"]
    assert all(1 <= i["heat"] <= 100 for i in items)
    assert all(i["niches"] for i in items)

    # the same cached Google Trends item leads the generic view — and the
    # clamp did not leak into the cache
    assert trends.fetch("generico", "BR")[0]["title"] == "Final do campeonato"
    with trends._lock:
        assert trends._cache["google_trends:BR"][1][0]["heat"] == 2000


def test_a_failing_unit_keeps_serving_its_previous_result():
    good = [{"title": "antes"}]
    assert trends._cached("unit", lambda: good) == good
    with trends._lock:
        trends._cache["unit"] = (time.time() - trends.CACHE_TTL - 1, good)   # expired

    def broken():
        raise httpx.ReadTimeout("slow")

    assert trends._cached("unit", broken) == good


def test_pending_lists_the_units_still_in_flight():
    with trends._lock:
        trends._inflight.add("web_search:games:BR")
        trends._inflight.add("web_search:saude:BR")
    assert trends.pending("games", "BR") == ["web_search"]
    assert trends.pending("politica", "BR") == []


def test_the_route_reports_pending_units(monkeypatch):
    monkeypatch.setattr(trends, "fetch", lambda niche, geo: [])
    monkeypatch.setattr(trends, "pending", lambda niche, geo: ["web_search"])
    body = TestClient(app).get("/api/trends?niche=politica").json()
    assert body["pending"] == ["web_search"]
    assert body["items"] == []
