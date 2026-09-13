"""Registering YOUR voice, from a sample you provide.

Two paths, and the answer always names the one that ran:

  * XTTS on your own machine — open source, free, no key. The sample *is* the
    voice: it is kept as the voice row's `sample_path`, and `tts._xtts` sends
    it as `speaker_wav` on every synthesis, so nothing is uploaded anywhere.
    What it does need is the XTTS server actually running: a URL written down
    proves nothing about a server that is not there, exactly as for a local
    image generator (see generators/registry.py), so reachability is checked
    before the voice is registered rather than at the first render.
  * fish.audio — it trains a reference model from the sample and answers with
    a `reference_id`, the very same kind of id the curated presets carry. A
    voice cloned there is indistinguishable from any other fish voice
    afterwards.

Either way what comes out is an ordinary row in the `voices` table, usable
everywhere a voice is usable today: the job form, the editor's re-render, the
preview endpoint. A cloned voice is a voice with a sample, not a new concept.

A sample is checked BEFORE any provider is touched. Two seconds of near
silence trains a voice that sounds like nothing, and finding that out after a
paid upload — or after a whole short has been narrated — is the expensive way
to learn it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import httpx

from .. import db
from ..config import settings
from . import render
from .generators.registry import (NOT_CONFIGURED, READY, UNREACHABLE,
                                  LocalServerDown)

XTTS = "xtts"
FISHAUDIO = "fishaudio"
VOICESTUDIO = "voicestudio"
PROVIDERS = (VOICESTUDIO, XTTS, FISHAUDIO)

# Below this there is not enough voice in the file for either engine to model
# a timbre; XTTS's own guidance is a handful of seconds of clean speech, and
# what comes out of two seconds is a caricature rather than the person.
MIN_SECONDS = 3.0
# What actually sounds like you. Not enforced — a 4s sample is allowed and
# merely warned about, because refusing it would be us deciding for someone
# who may only have that.
RECOMMENDED_SECONDS = 10.0
# Anything past this is trimmed, not refused: extra minutes of reference audio
# make the upload slower without making the clone better.
MAX_SECONDS = 120.0
# Mean level of a file with no usable voice in it — a muted recording, a
# microphone that was never armed. Measured with ffmpeg's volumedetect.
SILENCE_DBFS = -50.0

# A local server answers at once or it is not there; waiting longer only makes
# the screen feel broken.
PROBE_TIMEOUT = 8.0
# Training a reference model is an upload plus a training pass on their side,
# not a plain request — minutes, not seconds.
CLONE_TIMEOUT = 300.0

FISH_BASE = "https://api.fish.audio"

# Speech, not music: 24 kHz mono is what XTTS resamples to internally anyway,
# and it keeps the reference file small enough to live next to the database.
SAMPLE_RATE = 24000


class SampleRejected(RuntimeError):
    """The sample cannot become a voice, and nothing was registered. The
    message says the minimum and how to get past it — this is a normal answer
    to a bad recording, not a failure of the feature."""


class CloneUnavailable(RuntimeError):
    """No path can clone a voice right now. Lists what each one is missing,
    the way registry.unavailable_message does for the image generators."""


# ------------------------------------------------------------ the sample

def xtts_url() -> str:
    """Effective XTTS address. `settings` only, since XTTS is not a connector:
    it has no credential to keep, just a URL."""
    return (settings.xtts_server or "http://localhost:8020").rstrip("/")


def mean_dbfs(path: Path) -> float:
    """Average level of the file, in dBFS. Returns 0.0 when ffmpeg reported
    nothing measurable, which the caller reads as 'do not judge it'."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    found = re.findall(r"mean_volume:\s*(-?\d+\.?\d*) dB", proc.stderr)
    return float(found[-1]) if found else 0.0


def inspect(path: Path) -> dict:
    """(duration, level) of a sample, with no opinion attached."""
    return {"seconds": round(render.probe_duration(path), 2),
            "mean_dbfs": round(mean_dbfs(path), 1)}


def check_sample(path: Path) -> dict:
    """Refuses a sample nothing can be built from. Raises before any provider
    is called, so a rejected sample costs neither an upload nor a credit."""
    if not path.exists() or path.stat().st_size == 0:
        raise SampleRejected(
            "The audio sample did not arrive. Send the file again.")

    facts = inspect(path)
    seconds = facts["seconds"]
    if seconds <= 0:
        raise SampleRejected(
            "FFmpeg could not read any audio in this file. Send a WAV, MP3, "
            "M4A or a video with a real audio track.")
    if seconds < MIN_SECONDS:
        raise SampleRejected(
            f"This sample is {seconds:.1f}s long and the minimum is "
            f"{MIN_SECONDS:.0f}s. Record around "
            f"{RECOMMENDED_SECONDS:.0f}s of you speaking normally, in one go, "
            f"with no music behind it — that is what a voice is modelled from.")
    if facts["mean_dbfs"] <= SILENCE_DBFS:
        raise SampleRejected(
            f"This sample is silent (average level {facts['mean_dbfs']:.0f} "
            f"dBFS). Check that the microphone was the one recording and that "
            f"the track is not muted, then send it again.")
    return facts


def prepare_sample(source: Path, dest_dir: Path, stem: str) -> Path:
    """The reference audio as its own mono WAV.

    A video is accepted on purpose: the most common sample anyone actually has
    is a clip of themselves talking to camera, and asking them to extract the
    audio first is asking them to install something. `-vn` does it here.
    """
    render.ensure_ffmpeg()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}.wav"
    render._run([  # noqa: SLF001 — same ffmpeg wrapper the rest of the pipeline uses
        "ffmpeg", "-y", "-i", str(source), "-vn",
        "-t", f"{MAX_SECONDS:.0f}",
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(dest),
    ])
    return dest


# ----------------------------------------------------------- availability

def probe_xtts(base_url: str = "") -> str:
    """Proves the XTTS server is really there. Returns a line to show.

    Raises LocalServerDown — the same exception a local image generator
    raises, carrying the same kind of message: the URL that was tried, and the
    command that brings the server up.
    """
    url = (base_url or xtts_url()).rstrip("/")
    try:
        resp = httpx.get(f"{url}/speakers_list", timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        raise LocalServerDown(
            f"The XTTS server is not answering at {url}. Start it — "
            f"`pip install xtts-api-server` then `python -m xtts_api_server "
            f"--listen --port 8020` — or point XTTS_SERVER at the machine that "
            f"runs it. ({type(exc).__name__})") from exc
    if resp.status_code == 404:
        raise LocalServerDown(
            f"Something is answering at {url} but it is not an XTTS server "
            f"(/speakers_list is unknown to it). Check XTTS_SERVER.")
    resp.raise_for_status()
    return f"XTTS answering at {url} — local cloning available, no key needed"


def voicestudio_url() -> str:
    from .tts import voicestudio_url as url

    return url()


def probe_voicestudio(base_url: str = "") -> str:
    """Proves VoiceStudio is running, and says what it can speak with.

    Same contract as `probe_xtts`: a URL written down proves nothing about a
    server that is not there, so this runs before a voice is registered rather
    than at the first render.
    """
    url = (base_url or voicestudio_url()).rstrip("/")
    try:
        resp = httpx.get(f"{url}/v1/audio/voices", timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        raise LocalServerDown(
            f"VoiceStudio is not answering at {url}. Start the app (or "
            f"`docker run -d -p 127.0.0.1:3900:3900 "
            f"palashdeb/omnivoice-studio:stable`), or point VOICESTUDIO_URL at "
            f"the machine that runs it. ({type(exc).__name__})") from exc
    if resp.status_code == 404:
        raise LocalServerDown(
            f"Something is answering at {url} but it is not VoiceStudio "
            f"(/v1/audio/voices is unknown to it). Check VOICESTUDIO_URL.")
    resp.raise_for_status()
    try:
        profiles = resp.json()
        count = len(profiles.get("voices") if isinstance(profiles, dict) else profiles)
    except Exception:  # noqa: BLE001 — a live server answering oddly
        count = 0
    return (f"VoiceStudio answering at {url} — {count} voice(s) available, "
            f"cloning on your own hardware, no key")


def list_voicestudio_profiles(base_url: str = "") -> list[dict]:
    """The voices VoiceStudio already has, ready to be used here.

    Cloning is not the only way in: someone who built a voice in VoiceStudio's
    own interface — designed rather than cloned, or cloned before installing
    this — should not have to do it twice.
    """
    url = (base_url or voicestudio_url()).rstrip("/")
    resp = httpx.get(f"{url}/v1/audio/voices", timeout=PROBE_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    raw = payload.get("voices") if isinstance(payload, dict) else payload
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        voice_id = str(item.get("voice_id") or item.get("id") or "").strip()
        if not voice_id:
            continue
        out.append({"id": voice_id,
                    "name": str(item.get("name") or voice_id),
                    "language": str(item.get("language") or ""),
                    "description": str(item.get("description") or "")})
    return out


def _voicestudio_create_profile(name: str, sample: Path, language: str,
                                base_url: str = "") -> str:
    """Clone a profile from the sample. Returns its id.

    `kind=clone` with `ref_audio` is VoiceStudio's own cloning path — the same
    one its interface uses — so the profile that comes out is an ordinary
    VoiceStudio voice afterwards, usable there as well as here.
    """
    url = (base_url or voicestudio_url()).rstrip("/")
    with sample.open("rb") as handle:
        resp = httpx.post(
            f"{url}/profiles",
            data={"name": name, "kind": "clone",
                  "language": language or "Auto"},
            files={"ref_audio": (sample.name, handle, "audio/wav")},
            timeout=CLONE_TIMEOUT,
        )
    if resp.status_code in (400, 422):
        raise SampleRejected(
            f"VoiceStudio refused this sample: {_detail(resp)}")
    resp.raise_for_status()
    profile_id = str((resp.json() or {}).get("id") or "").strip()
    if not profile_id:
        raise CloneUnavailable(
            "VoiceStudio accepted the sample but returned no profile id.")
    return profile_id


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail or body)[:200]


def fish_configured() -> bool:
    from . import connectors

    return connectors.is_configured(FISHAUDIO)


def fish_key() -> str:
    from . import connectors

    return connectors.credentials(FISHAUDIO).get("api_key", "")


VOICESTUDIO_SETUP = (
    "Install VoiceStudio and leave it running — it is the local, open-source "
    "ElevenLabs alternative (github.com/debpalash/VoiceStudio): "
    "`docker run -d -p 127.0.0.1:3900:3900 palashdeb/omnivoice-studio:stable`, "
    "or the desktop app. No key, no bill, and the sample never leaves this "
    "machine. Point VOICESTUDIO_URL at it if it runs elsewhere.")

XTTS_SETUP = (
    "Start the local XTTS server (`pip install xtts-api-server`, then "
    "`python -m xtts_api_server --listen --port 8020`), or point XTTS_SERVER "
    "at the machine that runs it. No key and no bill — it clones on your own "
    "hardware.")

FISH_SETUP = (
    "Register the fish.audio API key on the Accounts screen (or "
    "FISHAUDIO_API_KEY in .env). Cloning there is billed as API credit, which "
    "is separate from the platform credit shown on their site.")


def describe_providers(probe: bool = True) -> list[dict]:
    """State of each cloning path, in the shape the generators registry uses:
    a state, the reason it is in that state, and what having it working buys.
    """
    if probe:
        try:
            xtts_state, xtts_reason = READY, probe_xtts()
        except LocalServerDown as exc:
            xtts_state, xtts_reason = UNREACHABLE, str(exc)
        except Exception as exc:  # noqa: BLE001 — a live server misbehaving
            xtts_state = UNREACHABLE
            xtts_reason = (f"{xtts_url()} answered, but not like an XTTS "
                           f"server: {exc}")
    else:
        xtts_state = READY
        xtts_reason = f"local server at {xtts_url()} (not probed)"

    if probe:
        try:
            vs_state, vs_reason = READY, probe_voicestudio()
        except LocalServerDown as exc:
            vs_state, vs_reason = UNREACHABLE, str(exc)
        except Exception as exc:  # noqa: BLE001 — a live server misbehaving
            vs_state = UNREACHABLE
            vs_reason = (f"{voicestudio_url()} answered, but not like "
                         f"VoiceStudio: {exc}")
    else:
        vs_state = READY
        vs_reason = f"local server at {voicestudio_url()} (not probed)"

    fish_ready = fish_configured()
    return [
        {
            "id": VOICESTUDIO, "label": "VoiceStudio (local)",
            "kind": "voice_clone", "hosting": "local", "cost": "free",
            "speed": "seconds to clone, seconds per sentence to speak",
            "state": vs_state, "reason": vs_reason, "setup": VOICESTUDIO_SETUP,
            "docs": "https://github.com/debpalash/VoiceStudio",
            "connector": VOICESTUDIO, "base_url": voicestudio_url(),
            "unlocks": ["your own voice, cloned and spoken entirely on this "
                        "machine, in 600+ languages and with no key"],
        },
        {
            "id": XTTS, "label": "XTTS (local)", "kind": "voice_clone",
            "hosting": "local", "cost": "free",
            "speed": "instant to register, seconds per sentence to speak",
            "state": xtts_state, "reason": xtts_reason, "setup": XTTS_SETUP,
            "docs": "https://github.com/daswer123/xtts-api-server",
            "connector": "", "base_url": xtts_url(),
            "unlocks": ["narrating a short in your own voice, cloned on your "
                        "machine from one sample"],
        },
        {
            "id": FISHAUDIO, "label": "fish.audio", "kind": "voice_clone",
            "hosting": "hosted", "cost": "paid",
            "speed": "under a minute to train the reference model",
            "state": READY if fish_ready else NOT_CONFIGURED,
            "reason": ("credential in place" if fish_ready else FISH_SETUP),
            "setup": FISH_SETUP,
            "docs": "https://docs.fish.audio",
            "connector": FISHAUDIO, "base_url": "",
            "unlocks": ["your voice as a hosted reference model, usable "
                        "without a GPU of your own"],
        },
    ]


def unavailable_message(providers: list[dict]) -> str:
    blockers = [f"{p['label']}: {p['reason']}" for p in providers
                if p["state"] != READY]
    detail = " ".join(blockers) if blockers else "no path declares itself."
    return f"No way to clone a voice right now. {detail}"


# ------------------------------------------------------------- fish.audio

def _fish_headers() -> dict:
    return {"Authorization": f"Bearer {fish_key()}"}


def _fish_create_model(name: str, sample: Path, description: str = "") -> str:
    """Trains a reference model on fish.audio and returns its reference_id.

    POST /model, multipart, because the audio goes up with the metadata.
    `type`, `title`, `train_mode` and `voices` are the required fields, and
    `train_mode` accepts only "fast". Success is 201 and the new id arrives as
    `_id`. `visibility` is forced to private: a voice someone cloned of
    themselves must not land in a public marketplace because a default said so.

    On refusals their docs only promise 401 (no permission) and 503 (high
    load) here — unlike /v1/tts, a spent balance is not documented as a 402 on
    this route. The paid codes are still read as refusals rather than crashes,
    because a real 402 arriving would otherwise surface as a raw HTTP error.
    """
    from .tts import VoiceUnavailable, _fish_reason  # noqa: PLC0415

    with sample.open("rb") as fh:
        resp = httpx.post(
            f"{FISH_BASE}/model",
            headers=_fish_headers(),
            data={
                "title": name,
                "description": description,
                "type": "tts",
                "train_mode": "fast",
                "visibility": "private",
                "enhance_audio_quality": "true",
            },
            files={"voices": (sample.name, fh, "audio/wav")},
            timeout=CLONE_TIMEOUT,
        )

    if resp.status_code in (401, 402, 403, 429):
        raise VoiceUnavailable(_fish_reason(resp))
    if resp.status_code == 503:
        # Their own wording for this one is "high load" — a transient state,
        # so it propagates instead of switching provider behind your back.
        raise RuntimeError(
            "fish.audio is under high load and could not train the voice "
            "right now. Try again in a few minutes.")
    if resp.status_code >= 400:
        raise RuntimeError(
            f"fish.audio refused the sample ({resp.status_code}): "
            f"{resp.text[:300]}")

    payload = resp.json() if resp.content else {}
    reference_id = payload.get("_id") or payload.get("id") or ""
    if not reference_id:
        raise RuntimeError(
            "fish.audio accepted the sample but returned no model id, so "
            "there is nothing to point a voice at. Try again in a moment.")
    return reference_id


# ------------------------------------------------------------- the whole job

def register(name: str, source: Path, provider: str = "",
             language: str = "pt", log=lambda m, level="info": None) -> dict:
    """Turn a sample into a usable voice row.

    `provider` empty means "whichever can": XTTS first, because it is free and
    the audio never leaves the machine. A fish.audio *refusal* (no credit, a
    dead key) falls through to XTTS when XTTS is up — the same rule
    `tts._synthesize_with` follows, and for the same reason: only a refusal
    justifies changing provider, while a network error is worth surfacing.

    The fallback only ever goes towards the free local path, never towards a
    paid one: spending someone's credit because their first choice refused is
    not a fallback, it is a bill they did not agree to.
    """
    label = (name or "").strip()
    if not label:
        raise SampleRejected("Give this voice a name so you can find it later.")
    if provider and provider not in PROVIDERS:
        raise SampleRejected(
            f"Unknown cloning provider: {provider}. Use "
            f"{' or '.join(PROVIDERS)}.")

    prepared = prepare_sample(source, settings.voices_dir,
                              db.new_id("sample"))
    try:
        facts = check_sample(prepared)
    except SampleRejected:
        prepared.unlink(missing_ok=True)
        raise

    if facts["seconds"] < RECOMMENDED_SECONDS:
        log(f"The sample is {facts['seconds']:.1f}s. It will work, but "
            f"{RECOMMENDED_SECONDS:.0f}s of continuous speech sounds "
            f"noticeably more like you.", "warn")

    providers = describe_providers()
    by_id = {p["id"]: p for p in providers}
    if not provider:
        # Local first, and VoiceStudio ahead of XTTS: both keep the sample on
        # this machine, and VoiceStudio also speaks it without a GPU server of
        # its own having to be wired up separately.
        wanted = [VOICESTUDIO, XTTS, FISHAUDIO]
    elif provider in (VOICESTUDIO, XTTS):
        wanted = [provider]
    else:
        wanted = [provider, VOICESTUDIO, XTTS]

    from .tts import VoiceUnavailable  # noqa: PLC0415

    refusals: list[str] = []
    for candidate in wanted:
        info = by_id[candidate]
        if info["state"] != READY:
            refusals.append(f"{info['label']}: {info['reason']}")
            continue
        try:
            voice = _register_with(candidate, label, prepared, facts,
                                   language, log)
        except VoiceUnavailable as exc:
            # A refusal, not a failure: try the next path and say why the
            # provider changed.
            refusals.append(f"{info['label']}: {exc}")
            log(f"{exc}", "warn")
            continue
        return voice

    prepared.unlink(missing_ok=True)
    detail = " ".join(refusals) if refusals else "no path declares itself."
    raise CloneUnavailable(f"Could not clone this voice. {detail}")


def _register_with(provider: str, name: str, sample: Path, facts: dict,
                   language: str, log) -> dict:
    if provider == VOICESTUDIO:
        log(f"Cloning locally with VoiceStudio from {sample.name} "
            f"({facts['seconds']:.1f}s) — nothing is uploaded.")
        profile_id = _voicestudio_create_profile(name, sample, language)
        voice_id = db.create_voice(
            name=name, provider=VOICESTUDIO, provider_voice_id=profile_id,
            sample_path=str(sample),
            settings_json={"language": language, "cloned": True,
                           "engine": settings.voicestudio_engine,
                           "sample_seconds": facts["seconds"],
                           "sample_dbfs": facts["mean_dbfs"]})
        return _described(voice_id, provider, facts,
                          f"Cloned on this machine by VoiceStudio at "
                          f"{voicestudio_url()} (profile {profile_id}). The "
                          f"sample never left here.")

    if provider == XTTS:
        log(f"Cloning locally with XTTS from {sample.name} "
            f"({facts['seconds']:.1f}s) — nothing is uploaded.")
        voice_id = db.create_voice(
            name=name, provider=XTTS, provider_voice_id="",
            sample_path=str(sample),
            settings_json={"language": language, "cloned": True,
                           "sample_seconds": facts["seconds"],
                           "sample_dbfs": facts["mean_dbfs"]})
        return _described(voice_id, provider, facts,
                          f"Cloned on this machine by XTTS at {xtts_url()}. "
                          f"The sample never left here.")

    log(f"Training a reference model on fish.audio from {sample.name} "
        f"({facts['seconds']:.1f}s).")
    reference_id = _fish_create_model(name, sample)
    voice_id = db.create_voice(
        name=name, provider=FISHAUDIO, provider_voice_id=reference_id,
        sample_path=str(sample),
        settings_json={"fish_model": settings.fishaudio_backend,
                       "language": language, "cloned": True,
                       "sample_seconds": facts["seconds"],
                       "sample_dbfs": facts["mean_dbfs"]})
    return _described(voice_id, FISHAUDIO, facts,
                      "Trained as a private reference model on fish.audio.")


def _described(voice_id: str, provider: str, facts: dict, note: str) -> dict:
    """The new row plus the two things the screen has to say: which path ran,
    and what the sample was."""
    voice = db.get_voice(voice_id) or {}
    return {**voice, "cloned_with": provider, "note": note,
            "sample_seconds": facts["seconds"],
            "sample_dbfs": facts["mean_dbfs"]}


def reference_audio(voice: dict) -> Path | None:
    """The stored sample of a cloned voice, so it can be played back.

    Worth having on its own: previewing an XTTS voice means synthesizing, and
    that needs the server up. Hearing what was actually uploaded works whether
    or not anything else does.
    """
    stored = (voice.get("sample_path") or "").strip()
    if not stored:
        return None
    # Path("") is Path("."), which exists — an empty column would otherwise
    # answer this route with a directory.
    path = Path(stored)
    return path if path.is_file() else None
