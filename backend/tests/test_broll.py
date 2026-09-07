"""Stock footage: three banks, several scenes per short, and no disk creep."""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.pipeline import broll


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    """Every test states the keys it wants; none leak in from the .env."""
    monkeypatch.setattr(settings, "pexels_api_key", "")
    monkeypatch.setattr(settings, "pixabay_api_key", "")
    monkeypatch.setattr(settings, "coverr_api_key", "")
    monkeypatch.setattr(settings, "broll_keep_cache", False)


# ------------------------------------------------------------------ banks

def test_no_key_means_no_provider_and_no_search():
    """Without a key the pipeline has to fall back to a gradient rather than
    fail — a missing key is a configuration state, not an error."""
    assert broll.providers_ready() == []
    assert broll.search_clips("city", 3) == []


def test_providers_are_listed_as_they_get_keys(monkeypatch):
    monkeypatch.setattr(settings, "pexels_api_key", "k")
    assert broll.providers_ready() == ["pexels"]
    monkeypatch.setattr(settings, "coverr_api_key", "k")
    assert broll.providers_ready() == ["pexels", "coverr"]


def test_results_are_merged_across_banks(monkeypatch):
    """A niche query can come up empty on one bank and full on another; the
    search only stops once it has enough."""
    monkeypatch.setattr(settings, "pexels_api_key", "k")
    monkeypatch.setattr(settings, "pixabay_api_key", "k")
    monkeypatch.setattr(broll, "_pexels", lambda q, n, landscape=False: ["a.mp4"])
    monkeypatch.setattr(broll, "_pixabay", lambda q, n, landscape=False: ["b.mp4", "c.mp4"])

    assert broll.search_clips("city", 3) == ["a.mp4", "b.mp4", "c.mp4"]


def test_a_bank_that_is_down_does_not_break_the_search(monkeypatch):
    def explode(q, n):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(broll, "_pexels", explode)
    monkeypatch.setattr(broll, "_pixabay", lambda q, n, landscape=False: ["b.mp4"])
    assert broll.search_clips("city", 2) == ["b.mp4"]


def test_the_same_url_is_never_returned_twice(monkeypatch):
    monkeypatch.setattr(broll, "_pexels", lambda q, n, landscape=False: ["dup.mp4"])
    monkeypatch.setattr(broll, "_pixabay", lambda q, n, landscape=False: ["dup.mp4", "other.mp4"])
    assert broll.search_clips("city", 5) == ["dup.mp4", "other.mp4"]


# ------------------------------------------------------ several scenes

def test_per_query_pulls_more_than_one_clip(monkeypatch, tmp_path):
    """One clip stretched over a whole short reads as a still image, so the
    orchestrator asks for several per query."""
    monkeypatch.setattr(broll, "search_clips",
                        lambda q, count, landscape=False: [f"{q}-{i}.mp4" for i in range(count)])
    baixados = []

    def fake_download(url, log=None, job_dir=None):
        baixados.append(url)
        path = tmp_path / url.replace("/", "_")
        path.write_bytes(b"video")
        return path

    monkeypatch.setattr(broll, "download", fake_download)
    clips = broll.fetch_for_queries(["city", "server"], job_dir=tmp_path, per_query=3)

    assert len(clips) == 6
    assert len(set(baixados)) == 6      # no repeats across queries


def test_a_failed_download_does_not_stop_the_others(monkeypatch, tmp_path):
    monkeypatch.setattr(broll, "search_clips", lambda q, count, landscape=False: ["bad", "good"])

    def fake_download(url, log=None, job_dir=None):
        if url == "bad":
            return None
        path = tmp_path / "good.mp4"
        path.write_bytes(b"video")
        return path

    monkeypatch.setattr(broll, "download", fake_download)
    assert len(broll.fetch_for_queries(["city"], job_dir=tmp_path, per_query=1)) == 1


# ----------------------------------------------------------- disk hygiene

def test_downloads_land_inside_the_job_by_default(tmp_path):
    """Ephemeral by default: the clips belong to the job that is rendering,
    so cleanup can take them all at once."""
    target = broll.scratch_dir(tmp_path)
    assert target == tmp_path / broll.SCRATCH
    assert target.exists()


def test_keeping_the_cache_shares_the_downloads(monkeypatch, tmp_path):
    """Worth it while iterating on the same short — re-fetching the same
    clips on every render is the slower trade."""
    monkeypatch.setattr(settings, "broll_keep_cache", True)
    assert broll.scratch_dir(tmp_path) == settings.cache_dir


def test_cleanup_frees_the_job_footage(tmp_path):
    """Stock clips are tens of MB each; without this the disk grows by one
    short's worth of footage every render, without bound."""
    scratch = broll.scratch_dir(tmp_path)
    (scratch / "broll_a.mp4").write_bytes(b"x" * 5000)
    (scratch / "broll_b.mp4").write_bytes(b"x" * 3000)

    freed = broll.cleanup(tmp_path)

    assert freed == 8000
    assert not scratch.exists()


def test_cleanup_leaves_the_shared_cache_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "broll_keep_cache", True)
    (tmp_path / broll.SCRATCH).mkdir()
    (tmp_path / broll.SCRATCH / "keep.mp4").write_bytes(b"x")

    assert broll.cleanup(tmp_path) == 0
    assert (tmp_path / broll.SCRATCH / "keep.mp4").exists()


def test_cleanup_on_a_job_that_downloaded_nothing(tmp_path):
    assert broll.cleanup(tmp_path) == 0


# ------------------------------------------------------------- providers

def _answer(payload: dict):
    """A response `raise_for_status` accepts — it needs its request set."""
    return lambda *a, **k: httpx.Response(
        200, json=payload, request=httpx.Request("GET", "https://stock.test"))


def test_pexels_prefers_the_file_closest_to_1080x1920(monkeypatch):
    monkeypatch.setattr(settings, "pexels_api_key", "k")
    monkeypatch.setattr(broll.httpx, "get", _answer({"videos": [{"video_files": [
        {"height": 360, "link": "small.mp4"},
        {"height": 1920, "link": "tall.mp4"},
        {"height": 720, "link": "mid.mp4"},
    ]}]}))
    assert broll._pexels("city", 1) == ["tall.mp4"]      # noqa: SLF001


def test_coverr_reads_the_download_url(monkeypatch):
    monkeypatch.setattr(settings, "coverr_api_key", "k")
    monkeypatch.setattr(broll.httpx, "get", _answer({"hits": [
        {"urls": {"mp4_download": "d.mp4", "mp4": "s.mp4"}},
        {"urls": {"mp4": "only-stream.mp4"}},
    ]}))
    assert broll._coverr("city", 2) == ["d.mp4", "only-stream.mp4"]   # noqa: SLF001


def test_pixabay_picks_the_largest_rendition(monkeypatch):
    monkeypatch.setattr(settings, "pixabay_api_key", "k")
    monkeypatch.setattr(broll.httpx, "get", _answer({"hits": [
        {"videos": {"small": {"url": "s.mp4"}, "large": {"url": "l.mp4"}}},
        {"videos": {"medium": {"url": "m.mp4"}}},
    ]}))
    assert broll._pixabay("city", 2) == ["l.mp4", "m.mp4"]   # noqa: SLF001
