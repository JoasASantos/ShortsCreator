"""VoiceStudio: your own voice, cloned and spoken on your own machine.

The local, open-source ElevenLabs alternative. What it buys this project is the
one thing the other cloning paths each miss: fish.audio clones well but uploads
the sample and bills per word; XTTS is local but is a second server to wire up
and only speaks — VoiceStudio clones and speaks, locally, with no key.

These tests fake the HTTP so they run with nothing installed. The contract they
pin is the one the app actually publishes: `POST /profiles` (multipart,
kind=clone) to make a voice, `POST /v1/audio/speech` to speak with it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from app import db
from app.config import settings
from app.pipeline import connectors, tts, voice_clone


@pytest.fixture(autouse=True)
def _local_url(monkeypatch):
    """A developer's own VOICESTUDIO_URL must not decide these."""
    monkeypatch.delenv("VOICESTUDIO_URL", raising=False)
    monkeypatch.setattr(settings, "voicestudio_url", "http://127.0.0.1:3900")
    monkeypatch.setattr(settings, "voicestudio_engine", "")


def _response(status: int, url: str = "http://x", method: str = "POST", **kwargs):
    return httpx.Response(status, request=httpx.Request(method, url), **kwargs)


def _sample(tmp_path: Path, seconds: float = 12.0) -> Path:
    """Real audio: `check_sample` measures level and length before any provider
    is touched, and it is right to."""
    path = tmp_path / "voz.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=180:duration={seconds}",
         "-af", "volume=0.5", "-ar", "22050", "-ac", "1", str(path)],
        check=True, capture_output=True)
    return path


# ----------------------------------------------------------------- the probe

def test_a_server_that_is_not_running_says_how_to_start_it(monkeypatch):
    def refuse(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(voice_clone.httpx, "get", refuse)
    with pytest.raises(voice_clone.LocalServerDown) as exc:
        voice_clone.probe_voicestudio()

    message = str(exc.value)
    assert "127.0.0.1:3900" in message
    assert "docker run" in message or "Start the app" in message


def test_something_else_on_the_port_is_not_mistaken_for_it(monkeypatch):
    monkeypatch.setattr(voice_clone.httpx, "get",
                        lambda *a, **k: _response(404, method="GET"))
    with pytest.raises(voice_clone.LocalServerDown, match="not VoiceStudio"):
        voice_clone.probe_voicestudio()


def test_a_running_server_reports_how_many_voices_it_has(monkeypatch):
    monkeypatch.setattr(voice_clone.httpx, "get", lambda *a, **k: _response(
        200, method="GET", json={"voices": [{"voice_id": "a1", "name": "Minha voz"},
                                            {"voice_id": "alloy", "name": "alloy"}]}))
    line = voice_clone.probe_voicestudio()
    assert "2 voice(s)" in line and "no key" in line


def test_the_profiles_it_already_has_can_be_listed(monkeypatch):
    """Cloning is not the only way in: a voice built in VoiceStudio's own
    interface must not have to be built twice."""
    monkeypatch.setattr(voice_clone.httpx, "get", lambda *a, **k: _response(
        200, method="GET", json={"voices": [
            {"voice_id": "p1", "name": "Narrador", "language": "pt"},
            {"name": "sem id"},
        ]}))
    found = voice_clone.list_voicestudio_profiles()
    assert [v["id"] for v in found] == ["p1"]
    assert found[0]["name"] == "Narrador"


# ---------------------------------------------------------------- cloning

def test_cloning_posts_the_sample_as_a_clone_profile(monkeypatch, tmp_path):
    sent: dict = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent["data"] = kwargs["data"]
        sent["files"] = list(kwargs["files"])
        return _response(200, url, json={"id": "ab12cd34", "name": "Minha voz"})

    monkeypatch.setattr(voice_clone.httpx, "post", fake_post)
    monkeypatch.setattr(voice_clone, "describe_providers", lambda probe=True: [
        {"id": voice_clone.VOICESTUDIO, "label": "VoiceStudio (local)",
         "state": voice_clone.READY, "reason": "running"}])

    voice = voice_clone.register("Minha voz", _sample(tmp_path),
                                 provider=voice_clone.VOICESTUDIO, language="pt")

    assert sent["url"].endswith("/profiles")
    assert sent["data"]["kind"] == "clone", "the cloning path, not voice design"
    assert sent["data"]["name"] == "Minha voz"
    assert sent["files"] == ["ref_audio"]
    assert voice["provider"] == "voicestudio"
    assert voice["provider_voice_id"] == "ab12cd34"
    # the sample is kept here too, so the voice can be played back and re-cloned
    assert Path(voice["sample_path"]).exists()
    assert "never left here" in voice["note"]


def test_a_sample_the_app_refuses_is_reported_as_such(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_clone.httpx, "post", lambda url, **k: _response(
        422, url, json={"detail": "clone profiles require ref_audio"}))
    monkeypatch.setattr(voice_clone, "describe_providers", lambda probe=True: [
        {"id": voice_clone.VOICESTUDIO, "label": "VoiceStudio (local)",
         "state": voice_clone.READY, "reason": "running"}])

    with pytest.raises(voice_clone.SampleRejected, match="require ref_audio"):
        voice_clone.register("Minha voz", _sample(tmp_path),
                             provider=voice_clone.VOICESTUDIO)


def test_local_cloning_is_preferred_when_no_provider_is_named(monkeypatch, tmp_path):
    """Free and on this machine beats paid and uploaded, and the choice must
    not be an accident of dict ordering."""
    monkeypatch.setattr(voice_clone, "describe_providers", lambda probe=True: [
        {"id": voice_clone.VOICESTUDIO, "label": "VoiceStudio (local)",
         "state": voice_clone.READY, "reason": "running"},
        {"id": voice_clone.XTTS, "label": "XTTS (local)",
         "state": voice_clone.READY, "reason": "running"},
        {"id": voice_clone.FISHAUDIO, "label": "fish.audio",
         "state": voice_clone.READY, "reason": "key"},
    ])
    ran: list[str] = []
    monkeypatch.setattr(voice_clone, "_register_with",
                        lambda provider, *a, **k: ran.append(provider) or {"id": "v"})

    voice_clone.register("Minha voz", _sample(tmp_path))
    assert ran == ["voicestudio"]


def test_a_refusal_falls_towards_the_free_path_never_towards_a_paid_one(
        monkeypatch, tmp_path):
    monkeypatch.setattr(voice_clone, "describe_providers", lambda probe=True: [
        {"id": voice_clone.VOICESTUDIO, "label": "VoiceStudio (local)",
         "state": voice_clone.READY, "reason": "running"},
        {"id": voice_clone.XTTS, "label": "XTTS (local)",
         "state": voice_clone.READY, "reason": "running"},
        {"id": voice_clone.FISHAUDIO, "label": "fish.audio",
         "state": voice_clone.READY, "reason": "key"},
    ])
    ran: list[str] = []

    def maybe(provider, *a, **k):
        ran.append(provider)
        if provider == voice_clone.VOICESTUDIO:
            raise tts.VoiceUnavailable("no engine installed yet")
        return {"id": "v"}

    monkeypatch.setattr(voice_clone, "_register_with", maybe)
    voice_clone.register("Minha voz", _sample(tmp_path))

    assert ran == ["voicestudio", "xtts"]
    assert "fishaudio" not in ran, "a refusal must not spend someone's credit"


# ----------------------------------------------------------------- speaking

def test_speech_is_asked_for_per_sentence_with_the_profile_id(monkeypatch, tmp_path):
    """One request per sentence, each file measured: estimating over the total
    lets the error pile up across a 90-second narration, and the captions are
    word-synced."""
    asked: list[dict] = []

    def fake_post(url, **kwargs):
        asked.append({"url": url, **kwargs["json"]})
        return _response(200, url, content=b"RIFF....WAVEfake")

    monkeypatch.setattr(tts.httpx, "post", fake_post)
    monkeypatch.setattr(tts, "audio_duration", lambda p: 2.0)
    monkeypatch.setattr(tts, "_concat_audio", lambda parts, out: out.write_bytes(b"x"))

    narration = tts.synthesize(
        "Primeira frase. Segunda frase.", tmp_path / "n.wav",
        {"provider": "voicestudio", "provider_voice_id": "ab12cd34"})

    assert len(asked) == 2
    assert asked[0]["url"].endswith("/v1/audio/speech")
    assert asked[0]["voice"] == "ab12cd34"
    assert asked[0]["response_format"] == "wav"
    assert narration.words, "word timings came out of the per-sentence spans"


def test_a_deleted_profile_is_a_refusal_so_the_render_survives(monkeypatch, tmp_path):
    """Deleted in VoiceStudio while a job was queued: no retry fixes it, and
    the system voice finishing the short beats a failed render."""
    monkeypatch.setattr(tts.httpx, "post",
                        lambda url, **k: _response(404, url, json={}))
    fell_back: list[str] = []
    monkeypatch.setattr(tts, "_edge", lambda text, out, voice, log:
                        fell_back.append("edge") or tts.Narration(out, 2.0, []))

    tts.synthesize("uma frase", tmp_path / "n.wav",
                   {"provider": "voicestudio", "provider_voice_id": "gone"})
    assert fell_back == ["edge"]


def test_the_server_being_down_names_the_address_and_does_not_fall_back(
        monkeypatch, tmp_path):
    """A local server that is not running is a state to fix, not a reason to
    change the voice of somebody's video behind their back."""
    def refuse(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(tts.httpx, "post", refuse)
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="127.0.0.1:3900"):
        tts.synthesize("uma frase", tmp_path / "n.wav",
                       {"provider": "voicestudio", "provider_voice_id": "x"})


def test_the_engine_can_be_pinned_per_voice(monkeypatch, tmp_path):
    """VoiceStudio ships sixteen engines; a voice cloned under one of them
    should not be spoken by another."""
    asked: list[dict] = []
    monkeypatch.setattr(tts.httpx, "post", lambda url, **k: (
        asked.append(k["json"]) or _response(200, url, content=b"x")))
    monkeypatch.setattr(tts, "audio_duration", lambda p: 1.0)
    monkeypatch.setattr(tts, "_concat_audio", lambda parts, out: out.write_bytes(b"x"))

    tts.synthesize("uma frase", tmp_path / "n.wav",
                   {"provider": "voicestudio", "provider_voice_id": "p1",
                    "settings": {"engine": "cosyvoice"}})
    assert asked[0]["model"] == "cosyvoice"


# ---------------------------------------------------------------- the panel

def test_the_accounts_screen_carries_it_as_a_local_server(monkeypatch):
    connector = connectors.get("voicestudio")
    assert connector.category == "voz"
    assert [f.key for f in connector.fields] == ["base_url"]
    assert not connector.fields[0].required, "empty means the default port"


def test_the_connector_test_is_reachability(monkeypatch):
    monkeypatch.setattr(voice_clone.httpx, "get", lambda *a, **k: _response(
        200, method="GET", json={"voices": []}))
    assert "answering" in connectors.test("voicestudio")
