"""Your own avatar with your own voice.

Two features that share a screen and nothing else: cloning a voice from a
sample you provide (free and local through XTTS, or hosted through
fish.audio), and rendering a talking presenter from a script through HeyGen.

What is worth testing about both is the same thing: a missing provider has to
be a readable answer rather than a traceback, a sample that cannot make a
voice has to be refused before anything is uploaded, and the requests actually
sent have to be the ones the providers document.
"""
from __future__ import annotations

import json
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.pipeline import avatar, connectors, voice_clone
from app.pipeline.generators import heygen
from app.pipeline.generators.registry import (NOT_CONFIGURED, READY,
                                              UNREACHABLE, LocalServerDown)

from conftest import has_ffmpeg, needs_ffmpeg

XTTS_URL = "http://127.0.0.1:18020"


@pytest.fixture(autouse=True)
def no_ambient_config(monkeypatch):
    """A developer's own .env must not decide whether these pass — this machine
    may well have a real fish.audio or HeyGen key sitting in it."""
    for var in ("HEYGEN_API_KEY", "FISHAUDIO_API_KEY", "XTTS_SERVER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "xtts_server", XTTS_URL)
    monkeypatch.setattr(settings, "fishaudio_api_key", "")


@pytest.fixture()
def client():
    from app.main import app

    return TestClient(app)


def _response(status: int, url: str = "http://x", method: str = "POST", **kwargs):
    """A response that can raise_for_status — which needs the request on it."""
    return httpx.Response(status, request=httpx.Request(method, url), **kwargs)


class _Stream:
    """httpx.stream() is used as a context manager, so a fake has to be one."""

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *exc):
        return False


def _xtts_down(monkeypatch):
    def refuse(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(voice_clone.httpx, "get", refuse)


def _xtts_up(monkeypatch):
    monkeypatch.setattr(voice_clone.httpx, "get", lambda *a, **k: _response(
        200, method="GET", json=["speaker_a"]))


def _tone(path, seconds: float, silent: bool = False):
    """A real audio file, because the whole check is ffprobe and ffmpeg reading
    it — a stub would exercise none of what matters."""
    source = ("anullsrc=r=24000:cl=mono" if silent
              else "sine=frequency=220:sample_rate=24000")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"{source}:duration={seconds}",
         "-t", str(seconds), "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )
    return path


# ------------------------------------------------------- refusing a sample

@needs_ffmpeg
def test_a_sample_shorter_than_the_minimum_is_refused_with_the_minimum_in_it(tmp_path):
    sample = _tone(tmp_path / "short.wav", 1.2)
    with pytest.raises(voice_clone.SampleRejected) as raised:
        voice_clone.check_sample(sample)

    message = str(raised.value)
    assert "1.2s" in message
    assert f"{voice_clone.MIN_SECONDS:.0f}s" in message
    # it has to say what to do, not only what is wrong
    assert "Record" in message


@needs_ffmpeg
def test_a_silent_sample_is_refused_even_when_it_is_long_enough(tmp_path):
    sample = _tone(tmp_path / "quiet.wav", 8, silent=True)
    with pytest.raises(voice_clone.SampleRejected, match="silent"):
        voice_clone.check_sample(sample)


@needs_ffmpeg
def test_a_usable_sample_reports_what_it_is(tmp_path):
    facts = voice_clone.check_sample(_tone(tmp_path / "ok.wav", 12))
    assert facts["seconds"] == pytest.approx(12, abs=0.3)
    assert facts["mean_dbfs"] < 0


@needs_ffmpeg
def test_a_rejected_sample_registers_nothing_and_leaves_no_file(tmp_path, monkeypatch):
    """The refusal has to happen before any provider is touched, otherwise a
    bad recording costs an upload — or a credit."""
    _xtts_up(monkeypatch)
    called: list[str] = []
    monkeypatch.setattr(voice_clone.httpx, "post",
                        lambda *a, **k: called.append("posted"))

    with pytest.raises(voice_clone.SampleRejected):
        voice_clone.register("Minha voz", _tone(tmp_path / "tiny.wav", 1))

    assert called == []
    assert db.list_voices() == []
    assert not list(settings.voices_dir.glob("sample_*.wav"))


def test_a_missing_sample_file_is_refused_rather_than_crashing(tmp_path):
    with pytest.raises(voice_clone.SampleRejected, match="did not arrive"):
        voice_clone.check_sample(tmp_path / "nothing.wav")


# ------------------------------------------------- the local XTTS path

def test_an_unreachable_xtts_server_says_the_url_and_how_to_start_it(monkeypatch):
    _xtts_down(monkeypatch)
    with pytest.raises(LocalServerDown) as raised:
        voice_clone.probe_xtts()

    message = str(raised.value)
    assert XTTS_URL in message
    assert "xtts_api_server" in message
    assert "XTTS_SERVER" in message


def test_something_else_answering_on_the_xtts_port_is_not_mistaken_for_xtts(monkeypatch):
    monkeypatch.setattr(voice_clone.httpx, "get", lambda *a, **k: _response(
        404, method="GET", json={}))
    with pytest.raises(LocalServerDown, match="not an XTTS server"):
        voice_clone.probe_xtts()


def test_a_reachable_xtts_server_is_ready_and_free(monkeypatch):
    _xtts_up(monkeypatch)
    states = {p["id"]: p for p in voice_clone.describe_providers()}
    assert states["xtts"]["state"] == READY
    assert states["xtts"]["cost"] == "free"
    assert XTTS_URL in states["xtts"]["reason"]


def test_with_nothing_configured_both_paths_explain_themselves(monkeypatch):
    _xtts_down(monkeypatch)
    states = {p["id"]: p for p in voice_clone.describe_providers()}

    assert states["xtts"]["state"] == UNREACHABLE
    assert states["fishaudio"]["state"] == NOT_CONFIGURED
    # every unavailable path carries its own next step
    assert "FISHAUDIO_API_KEY" in states["fishaudio"]["reason"]
    assert voice_clone.unavailable_message(list(states.values())).startswith(
        "No way to clone a voice right now.")


def test_describing_without_probing_does_not_touch_the_network(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("probe=False must not make a request")

    monkeypatch.setattr(voice_clone.httpx, "get", explode)
    states = {p["id"]: p for p in voice_clone.describe_providers(probe=False)}
    assert states["xtts"]["state"] == READY
    assert "not probed" in states["xtts"]["reason"]


@needs_ffmpeg
def test_cloning_locally_stores_the_sample_as_the_voice_and_uploads_nothing(
        tmp_path, monkeypatch):
    """This is the whole open-source path: the voice IS the reference audio,
    and tts._xtts sends it as speaker_wav on every synthesis."""
    _xtts_up(monkeypatch)
    monkeypatch.setattr(voice_clone.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("the local path must not upload the sample")))

    voice = voice_clone.register("Minha voz", _tone(tmp_path / "me.wav", 9))

    assert voice["provider"] == "xtts"
    assert voice["cloned_with"] == "xtts"
    assert voice["provider_voice_id"] == ""
    assert voice["sample_path"].endswith(".wav")
    # it is an ordinary voice row, usable anywhere a voice is
    assert [v["id"] for v in db.list_voices()] == [voice["id"]]
    extra = json.loads(voice["settings_json"])
    assert extra["cloned"] is True
    assert extra["sample_seconds"] == pytest.approx(9, abs=0.3)


@needs_ffmpeg
def test_a_video_sample_has_its_audio_extracted(tmp_path, sample_video, monkeypatch):
    """The recording most people already have is a clip of themselves talking
    to camera, so asking them to extract the audio first is asking them to
    install something."""
    _xtts_up(monkeypatch)
    voice = voice_clone.register("Da câmera", sample_video)

    from pathlib import Path

    stored = Path(voice["sample_path"])
    assert stored.suffix == ".wav"
    assert stored.exists()
    assert voice["sample_seconds"] > voice_clone.MIN_SECONDS


@needs_ffmpeg
def test_asking_for_xtts_while_it_is_down_refuses_with_the_reason(tmp_path, monkeypatch):
    _xtts_down(monkeypatch)
    with pytest.raises(voice_clone.CloneUnavailable) as raised:
        voice_clone.register("Minha voz", _tone(tmp_path / "me.wav", 8),
                             provider="xtts")
    assert XTTS_URL in str(raised.value)
    assert db.list_voices() == []


# ------------------------------------------------------ the fish.audio path

@needs_ffmpeg
def test_cloning_on_fish_sends_the_documented_multipart_shape(tmp_path, monkeypatch):
    """POST /model with type/title/train_mode/voices, and the new id comes back
    as `_id`. train_mode only accepts "fast"; visibility is forced to private
    so nobody's own voice lands in a public marketplace by default."""
    connectors.save("fishaudio", {"api_key": "fish-key"})
    _xtts_down(monkeypatch)
    seen: dict = {}

    def capture(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        seen["data"] = kwargs.get("data") or {}
        seen["files"] = kwargs.get("files") or {}
        seen["timeout"] = kwargs.get("timeout")
        return _response(201, json={"_id": "ref123", "state": "trained"})

    monkeypatch.setattr(voice_clone.httpx, "post", capture)
    voice = voice_clone.register("Minha voz", _tone(tmp_path / "me.wav", 14),
                                 provider="fishaudio")

    assert seen["url"] == "https://api.fish.audio/model"
    assert seen["data"]["type"] == "tts"
    assert seen["data"]["train_mode"] == "fast"
    assert seen["data"]["title"] == "Minha voz"
    assert seen["data"]["visibility"] == "private"
    assert "voices" in seen["files"]
    assert seen["timeout"] == voice_clone.CLONE_TIMEOUT
    assert seen["headers"]["Authorization"].startswith("Bearer ")

    assert voice["provider"] == "fishaudio"
    assert voice["provider_voice_id"] == "ref123"


@needs_ffmpeg
def test_a_fish_refusal_falls_back_to_the_local_path_when_it_is_up(tmp_path, monkeypatch):
    """Only a refusal justifies changing provider — the same rule
    tts._synthesize_with follows. A voice is still registered, locally, and the
    log says why the path changed."""
    connectors.save("fishaudio", {"api_key": "fish-key"})
    _xtts_up(monkeypatch)
    monkeypatch.setattr(voice_clone.httpx, "post", lambda *a, **k: _response(
        402, json={"detail": "no credit"}))

    notes: list[str] = []
    voice = voice_clone.register(
        "Minha voz", _tone(tmp_path / "me.wav", 8), provider="fishaudio",
        log=lambda message, level="info": notes.append(message))

    assert voice["cloned_with"] == "xtts"
    assert any("credit" in note for note in notes)


@needs_ffmpeg
def test_a_fish_refusal_with_no_local_server_explains_both(tmp_path, monkeypatch):
    connectors.save("fishaudio", {"api_key": "fish-key"})
    _xtts_down(monkeypatch)
    monkeypatch.setattr(voice_clone.httpx, "post", lambda *a, **k: _response(
        402, json={"detail": "no credit"}))

    with pytest.raises(voice_clone.CloneUnavailable) as raised:
        voice_clone.register("Minha voz", _tone(tmp_path / "me.wav", 8))

    message = str(raised.value)
    assert "credit" in message
    assert XTTS_URL in message


@needs_ffmpeg
def test_fish_under_load_propagates_instead_of_switching_provider(tmp_path, monkeypatch):
    """503 is transient. Silently cloning somewhere else because of a blip is
    worse than saying "try again in a few minutes"."""
    connectors.save("fishaudio", {"api_key": "fish-key"})
    _xtts_up(monkeypatch)
    monkeypatch.setattr(voice_clone.httpx, "post", lambda *a, **k: _response(503))

    with pytest.raises(RuntimeError, match="high load"):
        voice_clone.register("Minha voz", _tone(tmp_path / "me.wav", 8),
                             provider="fishaudio")
    assert db.list_voices() == []


@needs_ffmpeg
def test_fish_accepting_the_sample_without_an_id_is_not_registered(tmp_path, monkeypatch):
    connectors.save("fishaudio", {"api_key": "fish-key"})
    _xtts_down(monkeypatch)
    monkeypatch.setattr(voice_clone.httpx, "post",
                        lambda *a, **k: _response(201, json={"state": "trained"}))

    with pytest.raises(RuntimeError, match="no model id"):
        voice_clone.register("Minha voz", _tone(tmp_path / "me.wav", 8),
                             provider="fishaudio")
    assert db.list_voices() == []


def test_an_unknown_cloning_provider_is_refused_by_name():
    with pytest.raises(voice_clone.SampleRejected, match="Unknown cloning provider"):
        voice_clone.register("x", settings.voices_dir / "nothing.wav",
                             provider="elevenlabs")


def test_a_voice_with_no_name_is_refused_before_the_file_is_read():
    with pytest.raises(voice_clone.SampleRejected, match="name"):
        voice_clone.register("   ", settings.voices_dir / "nothing.wav")


# ------------------------------------------------------ HeyGen: not configured

def test_without_a_heygen_key_the_avatar_refuses_with_instructions():
    with pytest.raises(avatar.AvatarNotConfigured) as raised:
        avatar.credentials()

    message = str(raised.value)
    assert "HEYGEN_API_KEY" in message
    # and it points at what works with no key at all
    assert "cloned voice" in message


def test_the_avatar_state_is_not_configured_rather_than_an_error(monkeypatch):
    _xtts_down(monkeypatch)
    report = avatar.describe()
    assert report["avatar"]["state"] == NOT_CONFIGURED
    assert report["avatar"]["cost"] == "paid"
    assert report["avatar"]["unlocks"]
    # the voice paths ride along, so one call answers "what can I do today"
    assert [p["id"] for p in report["voice_clone"]] == ["xtts", "fishaudio"]
    assert report["limits"]["min_sample_seconds"] == voice_clone.MIN_SECONDS


def test_a_configured_key_makes_the_avatar_ready(monkeypatch):
    connectors.save("heygen", {"api_key": "hey-key"})
    _xtts_down(monkeypatch)
    assert avatar.describe()["avatar"]["state"] == READY
    assert avatar.is_configured() is True


def test_generating_without_a_key_refuses_before_making_any_request(tmp_path, monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("nothing should be requested with no credential")

    monkeypatch.setattr(heygen.httpx, "post", explode)
    with pytest.raises(avatar.AvatarNotConfigured, match="HeyGen is not configured"):
        avatar.generate(tmp_path, "Um roteiro qualquer para o avatar ler.",
                        "avatar_1", "voice_1", lambda m, level="info": None)


def test_choosing_no_avatar_says_so_instead_of_calling_heygen(tmp_path, monkeypatch):
    connectors.save("heygen", {"api_key": "hey-key"})
    monkeypatch.setattr(heygen.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not be reached")))

    with pytest.raises(avatar.AvatarNotConfigured, match="No avatar was chosen"):
        avatar.generate(tmp_path, "roteiro suficiente", "", "voice_1",
                        lambda m, level="info": None)


# ----------------------------------------------- HeyGen: the request shapes

def test_the_generate_poll_and_download_requests_are_the_documented_ones(
        tmp_path, monkeypatch):
    connectors.save("heygen", {"api_key": "hey-key"})
    posted: dict = {}
    polls: list[dict] = []

    def fake_post(url, **kwargs):
        posted["url"] = url
        posted["json"] = kwargs.get("json")
        posted["headers"] = kwargs.get("headers")
        posted["timeout"] = kwargs.get("timeout")
        return _response(200, json={"data": {"video_id": "vid_1"}})

    def fake_get(url, **kwargs):
        polls.append({"url": url, "params": kwargs.get("params")})
        return _response(200, method="GET", json={"data": {
            "status": "completed", "video_url": "https://cdn.heygen/x.mp4",
            "duration": 12.0}})

    def fake_stream(_method, url, **_kwargs):
        posted["download"] = url
        response = _response(200, method="GET", content=b"mp4-bytes")
        return _Stream(response)

    monkeypatch.setattr(heygen.httpx, "post", fake_post)
    monkeypatch.setattr(heygen.httpx, "get", fake_get)
    monkeypatch.setattr(heygen.httpx, "stream", fake_stream)

    out = avatar.generate(tmp_path, "Roteiro do avatar falando.",
                          "avatar_1", "voice_1", lambda m, level="info": None)

    assert posted["url"] == "https://api.heygen.com/v2/video/generate"
    assert posted["headers"]["X-Api-Key"] == "hey-key"
    assert posted["timeout"] == heygen.TIMEOUT

    body = posted["json"]
    character = body["video_inputs"][0]["character"]
    voice = body["video_inputs"][0]["voice"]
    assert character == {"type": "avatar", "avatar_id": "avatar_1",
                         "avatar_style": "normal"}
    assert voice == {"type": "text", "input_text": "Roteiro do avatar falando.",
                     "voice_id": "voice_1"}
    # 9:16 is the whole point of this project
    assert body["dimension"] == {"width": 1080, "height": 1920}

    assert polls[0]["url"] == "https://api.heygen.com/v1/video_status.get"
    assert polls[0]["params"] == {"video_id": "vid_1"}
    assert posted["download"] == "https://cdn.heygen/x.mp4"
    assert out.read_bytes() == b"mp4-bytes"


def test_a_failed_render_reports_the_providers_reason_not_none(tmp_path, monkeypatch):
    connectors.save("heygen", {"api_key": "hey-key"})
    monkeypatch.setattr(heygen.httpx, "post", lambda *a, **k: _response(
        200, json={"data": {"video_id": "vid_1"}}))
    monkeypatch.setattr(heygen.httpx, "get", lambda *a, **k: _response(
        200, method="GET", json={"data": {"status": "failed",
                                          "failure_message": "avatar unavailable"}}))

    with pytest.raises(RuntimeError, match="avatar unavailable"):
        avatar.generate(tmp_path, "roteiro do avatar", "a", "v",
                        lambda m, level="info": None)


def test_a_rejected_key_says_so_rather_than_raising_an_http_error(tmp_path, monkeypatch):
    connectors.save("heygen", {"api_key": "wrong"})
    monkeypatch.setattr(heygen.httpx, "post", lambda *a, **k: _response(401))

    with pytest.raises(RuntimeError, match="rejected by HeyGen"):
        avatar.generate(tmp_path, "roteiro do avatar", "a", "v",
                        lambda m, level="info": None)


def test_an_already_downloaded_video_is_not_paid_for_twice(tmp_path, monkeypatch):
    """A job that failed after the render — during framing, say — must not buy
    the same clip again when it is retried."""
    connectors.save("heygen", {"api_key": "hey-key"})
    (tmp_path / "avatar_source.mp4").write_bytes(b"already here")
    monkeypatch.setattr(heygen.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("the clip was already on disk")))

    notes: list[str] = []
    out = avatar.generate(tmp_path, "roteiro do avatar", "a", "v",
                          lambda m, level="info": notes.append(m))
    assert out.read_bytes() == b"already here"
    assert any("Reusing" in note for note in notes)


def test_a_listing_is_read_whether_it_is_wrapped_or_flat():
    """v2 wraps the items under `data.avatars`; the v3 endpoints replacing it
    return a flat `data[]`. Reading both is what keeps the picker from
    quietly going empty across that switch."""
    wrapped = {"data": {"avatars": [{"avatar_id": "a"}]}}
    flat = {"data": [{"avatar_id": "a"}]}
    assert heygen._listing(wrapped, "avatars") == [{"avatar_id": "a"}]
    assert heygen._listing(flat, "avatars") == [{"avatar_id": "a"}]
    assert heygen._listing({}, "avatars") == []


def test_the_catalog_is_trimmed_to_what_the_picker_needs(monkeypatch):
    connectors.save("heygen", {"api_key": "hey-key"})
    monkeypatch.setattr(heygen.httpx, "get", lambda url, **k: _response(
        200, method="GET",
        json={"data": {
            "avatars": [{"avatar_id": "a1", "avatar_name": "Ana",
                         "gender": "female", "preview_image_url": "img",
                         "secret_internal_field": "nope"},
                        {"avatar_name": "no id at all"}],
            "voices": [{"voice_id": "v1", "name": "Ana BR", "language": "pt",
                        "preview_audio_url": "aud"}],
        }}))

    avatars = avatar.list_avatars()
    assert avatars == [{"id": "a1", "name": "Ana", "gender": "female",
                        "preview_image": "img", "preview_video": ""}]
    voices = avatar.list_voices()
    assert voices[0]["id"] == "v1"
    assert voices[0]["preview_audio"] == "aud"


# ---------------------------------------------------------------- the script

def test_a_script_too_short_to_be_read_is_refused():
    with pytest.raises(ValueError, match="at least"):
        avatar.check_script("oi")


def test_a_script_longer_than_one_take_is_refused_with_the_limit():
    with pytest.raises(ValueError) as raised:
        avatar.check_script("a" * (avatar.MAX_SCRIPT_CHARS + 1))
    assert str(avatar.MAX_SCRIPT_CHARS) in str(raised.value)


def test_a_script_is_stripped_but_kept_whole():
    assert avatar.check_script("  Fala isso aqui.  ") == "Fala isso aqui."


# ---------------------------------------------------------------- the routes

def test_the_capability_route_answers_with_nothing_configured(client, monkeypatch):
    _xtts_down(monkeypatch)
    body = client.get("/api/avatar").json()
    assert body["avatar"]["state"] == NOT_CONFIGURED
    assert {p["id"] for p in body["providers"]} == {"heygen", "xtts", "fishaudio"}
    assert body["limits"]["min_script_chars"] == avatar.MIN_SCRIPT_CHARS


def test_listing_avatars_without_a_key_is_a_400_with_the_instruction(client):
    answer = client.get("/api/avatar/avatars")
    assert answer.status_code == 400
    assert "HEYGEN_API_KEY" in answer.json()["detail"]


def test_listing_voices_when_heygen_is_down_is_a_502_not_a_traceback(client, monkeypatch):
    connectors.save("heygen", {"api_key": "hey-key"})

    def refuse(*_a, **_k):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(heygen.httpx, "get", refuse)
    answer = client.get("/api/avatar/voices")
    assert answer.status_code == 502
    assert "HeyGen did not answer" in answer.json()["detail"]


def test_an_avatar_video_without_a_key_is_refused_before_a_job_exists(client):
    answer = client.post("/api/avatar/videos", json={
        "script": "Um roteiro longo o suficiente para o avatar ler.",
        "avatar_id": "a1", "voice_id": "v1"})
    assert answer.status_code == 400
    assert "HEYGEN_API_KEY" in answer.json()["detail"]
    assert db.list_jobs() == []


def test_an_avatar_video_needs_an_avatar_and_a_voice(client):
    connectors.save("heygen", {"api_key": "hey-key"})
    script = "Um roteiro longo o suficiente para o avatar ler em voz alta."

    no_avatar = client.post("/api/avatar/videos", json={
        "script": script, "avatar_id": "", "voice_id": "v1"})
    no_voice = client.post("/api/avatar/videos", json={
        "script": script, "avatar_id": "a1", "voice_id": ""})

    assert no_avatar.status_code == 400
    assert "which avatar" in no_avatar.json()["detail"]
    assert no_voice.status_code == 400
    assert "which voice" in no_voice.json()["detail"]


def test_an_avatar_video_refuses_a_script_it_cannot_read(client):
    connectors.save("heygen", {"api_key": "hey-key"})
    answer = client.post("/api/avatar/videos", json={
        "script": "oi", "avatar_id": "a1", "voice_id": "v1"})
    assert answer.status_code == 400
    assert "at least" in answer.json()["detail"]


def test_a_queued_avatar_video_is_a_job_with_its_own_edit_mode(client, monkeypatch):
    """It rides the normal job queue on purpose: that is what gives it the
    timeline editor, QA, the cover and publishing for free."""
    from app import worker

    connectors.save("heygen", {"api_key": "hey-key"})
    queued: list[str] = []
    monkeypatch.setattr(worker, "enqueue", queued.append)

    answer = client.post("/api/avatar/videos", json={
        "script": "Ninguém avisou que isso ia vazar, e é sobre isso que eu vou falar.",
        "avatar_id": "a1", "voice_id": "v1", "title": "meu avatar"})
    assert answer.status_code == 200
    job_id = answer.json()["job_id"]
    assert queued == [job_id]

    saved = json.loads(db.get_job(job_id)["input_json"])
    assert saved["edit_mode"] == avatar.MODE
    assert saved["avatar_id"] == "a1"
    assert saved["avatar_voice_id"] == "v1"
    # the provider supplies both the picture and the voice
    assert saved["background"] == "video_fonte"
    assert saved["music"] is False
    assert saved["title_overlay"] is False
    # rewriting the script to fix a QA finding would mean paying for a second
    # render, so the audit reports instead of autofixing
    assert saved["qa_autofix"] is False


def test_the_orchestrator_routes_an_avatar_job_to_its_own_pipeline(monkeypatch):
    """Same routing a recording of your own gets: there is no script stage and
    no TTS stage here, so the generation pipeline must not be entered at all."""
    from app.pipeline import orchestrator
    from app.schemas import JobInput

    job_id = db.create_job(JobInput(
        source_type="roteiro", source="Roteiro do avatar falando disso.",
        edit_mode=avatar.MODE, avatar_id="a1", avatar_voice_id="v1",
    ).model_dump(), "avatar")

    seen: list[str] = []
    monkeypatch.setattr(orchestrator.avatar, "run",
                        lambda jid, *_a: seen.append(jid) or {"mode": "avatar"})
    monkeypatch.setattr(orchestrator.render, "ensure_ffmpeg", lambda: (_ for _ in ()).throw(
        AssertionError("the generation pipeline must not be entered")))

    assert orchestrator.run_job(job_id) == {"mode": "avatar"}
    assert seen == [job_id]


@needs_ffmpeg   # run() checks FFmpeg first, as every other pipeline does
def test_an_unconfigured_avatar_job_fails_with_the_instruction_on_the_job():
    """The refusal has to land on the job row, where the dashboard shows it —
    a traceback in the worker log helps nobody."""
    from app.pipeline import orchestrator
    from app.schemas import JobInput

    job_id = db.create_job(JobInput(
        source_type="roteiro", source="Roteiro do avatar falando disso.",
        edit_mode=avatar.MODE, avatar_id="a1", avatar_voice_id="v1",
    ).model_dump(), "avatar")

    with pytest.raises(avatar.AvatarNotConfigured):
        orchestrator.run_job(job_id)

    row = db.get_job(job_id)
    assert row["status"] == "error"
    assert "HEYGEN_API_KEY" in row["error"]


def test_cloning_a_voice_through_the_route_refuses_a_short_sample(client, tmp_path):
    if not has_ffmpeg():
        pytest.skip("FFmpeg is not on the PATH")
    sample = _tone(tmp_path / "tiny.wav", 1)
    answer = client.post("/api/voices/clone",
                         data={"name": "Minha voz"},
                         files={"sample": ("tiny.wav", sample.read_bytes(),
                                           "audio/wav")})
    assert answer.status_code == 400
    assert "minimum" in answer.json()["detail"]
    assert db.list_voices() == []


def test_cloning_a_voice_through_the_route_with_nothing_available(client, tmp_path,
                                                                  monkeypatch):
    """The state this machine is actually in: no key, no local server. It has
    to come back as an instruction, not a 500."""
    if not has_ffmpeg():
        pytest.skip("FFmpeg is not on the PATH")
    _xtts_down(monkeypatch)
    sample = _tone(tmp_path / "me.wav", 8)
    answer = client.post("/api/voices/clone",
                         data={"name": "Minha voz"},
                         files={"sample": ("me.wav", sample.read_bytes(),
                                           "audio/wav")})
    assert answer.status_code == 400
    detail = answer.json()["detail"]
    assert XTTS_URL in detail
    assert "FISHAUDIO_API_KEY" in detail


@needs_ffmpeg
def test_cloning_a_voice_through_the_route_returns_the_path_that_ran(client, tmp_path,
                                                                    monkeypatch):
    _xtts_up(monkeypatch)
    sample = _tone(tmp_path / "me.wav", 9)
    answer = client.post("/api/voices/clone",
                         data={"name": "Minha voz", "provider": "xtts"},
                         files={"sample": ("me.wav", sample.read_bytes(),
                                           "audio/wav")})
    assert answer.status_code == 200
    body = answer.json()
    assert body["cloned_with"] == "xtts"
    assert "never left here" in body["note"]

    # and the sample can be played back without the server being up
    played = client.get(f"/api/voices/{body['id']}/sample")
    assert played.status_code == 200
    assert played.headers["content-type"].startswith("audio/")


def test_the_sample_of_an_unknown_voice_is_a_404(client):
    assert client.get("/api/voices/voice_nope/sample").status_code == 404


def test_a_voice_with_no_stored_sample_says_so(client):
    voice_id = db.create_voice(name="Edge", provider="edge",
                               provider_voice_id="pt-BR-AntonioNeural")
    answer = client.get(f"/api/voices/{voice_id}/sample")
    assert answer.status_code == 404
    assert "no stored audio sample" in answer.json()["detail"]
