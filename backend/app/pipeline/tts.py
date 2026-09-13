"""Voice synthesis with per-word timings.

The core trick: instead of transcribing the audio afterwards (ASR), we take the
timings straight from the TTS provider — edge-tts emits WordBoundary events and
ElevenLabs returns `character_start_times_seconds`. Exact timings, zero cost.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import settings

EDGE_MAX_ATTEMPTS = 4


@dataclass
class Narration:
    audio_path: Path
    duration: float
    words: list[dict]     # [{"word": str, "start": float, "end": float}]


class VoiceUnavailable(RuntimeError):
    """A paid voice provider refused the request for a reason retrying will
    not fix: no credit, an invalid key, a quota that is spent. Distinct from a
    transient network error, because it decides whether falling back is the
    right move."""


# The free voice each language gets when nobody picked one. Verified against
# edge-tts's own catalogue rather than guessed: a name that does not exist
# fails at synthesis, which is the worst moment to find out.
#
# This exists because of a quiet bug, not for completeness: EDGE_VOICE is a
# single pt-BR name, so a short written in English was narrated by a Brazilian
# voice reading English words. The script already followed `job.language`; the
# voice did not.
DEFAULT_EDGE_VOICES = {
    "pt": "pt-BR-AntonioNeural",
    "en": "en-US-AndrewNeural",
    "es": "es-ES-AlvaroNeural",
    "fr": "fr-FR-HenriNeural",
    "de": "de-DE-ConradNeural",
    "it": "it-IT-DiegoNeural",
    "ru": "ru-RU-DmitryNeural",
    "zh": "zh-CN-YunxiNeural",
    "ja": "ja-JP-KeitaNeural",
}


def default_voice_for(language: str) -> str:
    """The system voice for a language tag ('es-ES' -> the Spanish one).

    Falls back to EDGE_VOICE, which is what someone who set it deliberately
    expects — and to Portuguese only when even that is empty.
    """
    tag = (language or "").strip().lower()
    if tag:
        chosen = DEFAULT_EDGE_VOICES.get(tag.split("-")[0])
        if chosen:
            return chosen
    return settings.edge_voice or DEFAULT_EDGE_VOICES["pt"]


def synthesize(text: str, out_path: Path, voice: dict | None = None,
               log=lambda m, level="info": None, language: str = "") -> Narration:
    """`language` is the job's, and it only decides anything when no voice was
    chosen: a registered voice is a deliberate choice and is never overridden
    by the language of the script."""
    voice = voice or {}
    provider = voice.get("provider") or settings.tts_provider
    if language and not voice.get("provider_voice_id") and not voice.get("provider"):
        voice = {**voice, "provider_voice_id": default_voice_for(language)}
    narration = _synthesize_with(provider, text, out_path, voice, log)

    if not narration.words:
        narration.words = estimate_words(text, narration.duration)
    return narration


# A paid provider's HTTP request that timed out or dropped, retried. Not a
# refusal and not the provider's fault: one read timeout used to leave a block
# of a documentary with no voice at all, which reads as a broken file, and the
# next attempt of the same request nearly always works.
PAID_MAX_ATTEMPTS = 3


def _synthesize_with(provider: str, text: str, out_path: Path, voice: dict,
                     log) -> Narration:
    """Runs the chosen provider, falling back to edge-tts when a paid one is
    out of credit.

    A short should not be lost because a voice account ran dry — the narration
    still gets made, in the system voice, and the log says why it changed. The
    fallback is deliberate for `VoiceUnavailable` only: a network blip is worth
    surfacing rather than silently swapping the voice of the video. What a blip
    gets instead is another attempt at the same voice.
    """
    last_error: Exception | None = None
    for attempt in range(1, PAID_MAX_ATTEMPTS + 1):
        try:
            if provider == "edge":
                return _edge(text, out_path, voice, log)
            if provider == "elevenlabs":
                return _elevenlabs(text, out_path, voice, log)
            if provider == "xtts":
                return _xtts(text, out_path, voice, log)
            if provider == "fishaudio":
                return _fishaudio(text, out_path, voice, log)
            if provider == "voicestudio":
                return _voicestudio(text, out_path, voice, log)
            raise RuntimeError(f"Unknown TTS_PROVIDER: {provider}")
        except VoiceUnavailable as exc:
            if provider == "edge":
                raise
            log(f"{exc} Falling back to the free edge-tts voice.", "warn")
            return _edge(text, out_path, {}, log)
        except Exception as exc:  # noqa: BLE001 — see the two re-raises below
            # Broad on purpose: the failure that made a documentary block
            # silent was `httpx.ReadTimeout`, which is neither RuntimeError
            # nor OSError. edge-tts retries inside itself, and an unknown
            # provider name is not going to become known on the second attempt.
            if provider == "edge" or "Unknown TTS_PROVIDER" in str(exc):
                raise
            last_error = exc
            if attempt < PAID_MAX_ATTEMPTS:
                delay = 2 ** (attempt - 1)
                log(f"{provider} failed ({exc}); retrying in {delay}s "
                    f"[{attempt}/{PAID_MAX_ATTEMPTS}]", "warn")
                time.sleep(delay)
    # The original exception, not a wrapper: a transient failure has to reach
    # the caller as what it was, so `except httpx.ConnectError` upstream still
    # means what it says.
    raise last_error if last_error else RuntimeError(f"{provider} failed")


def trim_leading_silence(narration: Narration, out_path: Path,
                         threshold_db: str = "-45dB", min_dur: float = 0.15) -> Narration:
    """Cuts the dead silence at the start of the audio — used by the QA autofix
    when the hook starts late and kills retention in the first few seconds."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(narration.audio_path),
         "-af", f"silencedetect=noise={threshold_db}:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(v) for v in re.findall(r"silence_start:\s*(-?\d+\.?\d*)", proc.stderr)]
    ends = [float(v) for v in re.findall(r"silence_end:\s*(-?\d+\.?\d*)", proc.stderr)]

    cut = ends[0] if starts and ends and starts[0] <= 0.4 else 0.0
    if cut <= 0.05:
        return narration  # nothing worth cutting

    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{cut:.3f}", "-i", str(narration.audio_path),
         "-c", "copy", str(out_path)],
        check=True, capture_output=True,
    )
    shifted_words = [
        {"word": w["word"], "start": round(max(w["start"] - cut, 0.0), 3),
         "end": round(max(w["end"] - cut, 0.0), 3)}
        for w in narration.words
    ]
    return Narration(out_path, audio_duration(out_path), shifted_words)


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def words_from_sentences(sentences: list[dict]) -> list[dict]:
    """Converts SENTENCE markers into WORD markers.

    Each sentence has a real start and end measured by the TTS; within it the
    words are distributed by weight. The error stays confined to the sentence
    (tenths of a second) instead of piling up across the whole video, which is
    what happens when you estimate over the total duration.
    """
    out: list[dict] = []
    for sentence in sentences:
        span = max(sentence["end"] - sentence["start"], 0.0)
        inner = estimate_words(sentence["word"], span)
        for word in inner:
            out.append({
                "word": word["word"],
                "start": round(sentence["start"] + word["start"], 3),
                "end": round(sentence["start"] + word["end"], 3),
            })
    return out


def estimate_words(text: str, duration: float) -> list[dict]:
    """Fallback: spreads the time in proportion to the length of each word."""
    tokens = [w for w in re.findall(r"\S+", text) if w]
    if not tokens:
        return []
    weights = [max(len(re.sub(r"\W", "", w)), 1) + 1 for w in tokens]
    total = sum(weights)
    words: list[dict] = []
    cursor = 0.0
    for token, weight in zip(tokens, weights):
        span = duration * (weight / total)
        words.append({"word": token, "start": round(cursor, 3),
                      "end": round(cursor + span, 3)})
        cursor += span
    return words


# ---------------- edge-tts (free, pt-BR neural voices) ----------------

def _edge(text: str, out_path: Path, voice: dict, log) -> Narration:
    import edge_tts

    voice_name = voice.get("provider_voice_id") or settings.edge_voice
    # A voice registered with its own rate keeps it; everything else follows
    # the house pace, which is slightly under the provider's default so the
    # narration lands instead of running.
    rate = voice.get("rate") or settings.narration_rate
    pitch = voice.get("pitch", "+0Hz")
    log(f"edge-tts voice={voice_name} rate={rate} pitch={pitch}")

    words: list[dict] = []

    sentences: list[dict] = []

    async def run() -> None:
        # boundary="WordBoundary" is what gives per-WORD timing; the library's
        # default is SentenceBoundary, which only marks whole sentences and would
        # drop the karaoke caption into the estimated fallback (out of sync).
        comm = edge_tts.Communicate(text, voice_name, rate=rate, pitch=pitch,
                                    boundary="WordBoundary")
        with out_path.open("wb") as fh:
            async for chunk in comm.stream():
                kind = chunk.get("type")
                if kind == "audio":
                    fh.write(chunk["data"])
                elif kind in ("WordBoundary", "SentenceBoundary"):
                    start = chunk["offset"] / 1e7
                    end = start + chunk["duration"] / 1e7
                    entry = {"word": chunk["text"],
                             "start": round(start, 3), "end": round(end, 3)}
                    (words if kind == "WordBoundary" else sentences).append(entry)

    # Microsoft's public endpoint drops the connection fairly often; without a
    # retry, one transient failure kills the whole job mid-pipeline.
    last_error: Exception | None = None
    for attempt in range(1, EDGE_MAX_ATTEMPTS + 1):
        words.clear()
        sentences.clear()
        try:
            asyncio.run(run())
            if out_path.exists() and out_path.stat().st_size > 1024:
                duration = audio_duration(out_path)
                timings = words or words_from_sentences(sentences)
                if not timings:
                    log("No time markers from the TTS — caption will use an estimate")
                return Narration(out_path, duration, timings)
            raise RuntimeError("edge-tts returned empty audio")
        except Exception as exc:  # noqa: BLE001 — any network failure goes to retry
            last_error = exc
            if attempt < EDGE_MAX_ATTEMPTS:
                delay = 2 ** (attempt - 1)
                log(f"edge-tts failed ({exc}); retrying in {delay}s "
                    f"[{attempt}/{EDGE_MAX_ATTEMPTS}]")
                time.sleep(delay)

    raise RuntimeError(
        f"edge-tts failed after {EDGE_MAX_ATTEMPTS} attempts: {last_error}"
    )


# ---------------- ElevenLabs (character voice cloning) ----------------

def _elevenlabs(text: str, out_path: Path, voice: dict, log) -> Narration:
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    voice_id = voice.get("provider_voice_id")
    if not voice_id:
        raise RuntimeError("ElevenLabs voice with no provider_voice_id")

    log(f"elevenlabs voice={voice_id}")
    extra = json.loads(voice.get("settings_json") or "{}") if isinstance(
        voice.get("settings_json"), str) else (voice.get("settings_json") or {})

    resp = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
        headers={"xi-api-key": settings.elevenlabs_api_key},
        json={
            "text": text,
            "model_id": settings.elevenlabs_model,
            "voice_settings": {
                "stability": extra.get("stability", 0.45),
                "similarity_boost": extra.get("similarity_boost", 0.8),
                "style": extra.get("style", 0.35),
                "use_speaker_boost": True,
            },
        },
        timeout=300,
    )
    resp.raise_for_status()
    payload = resp.json()
    out_path.write_bytes(base64.b64decode(payload["audio_base64"]))

    alignment = payload.get("alignment") or payload.get("normalized_alignment") or {}
    words = _words_from_chars(
        alignment.get("characters", []),
        alignment.get("character_start_times_seconds", []),
        alignment.get("character_end_times_seconds", []),
    )
    return Narration(out_path, audio_duration(out_path), words)


def _words_from_chars(chars: list[str], starts: list[float],
                      ends: list[float]) -> list[dict]:
    words: list[dict] = []
    buf, start = "", None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if buf:
                words.append({"word": buf, "start": round(start, 3), "end": round(prev_e, 3)})
                buf, start = "", None
            continue
        if start is None:
            start = s
        buf += ch
        prev_e = e
    if buf and start is not None:
        words.append({"word": buf, "start": round(start, 3), "end": round(prev_e, 3)})
    return words


# ---------------- local XTTS (cloning from an audio sample) ----------------

def _xtts(text: str, out_path: Path, voice: dict, log) -> Narration:
    """Local XTTS. It returns no timings either, so it synthesizes sentence by
    sentence and measures each file — the same strategy used for fish.audio."""
    sample = voice.get("sample_path")
    if not sample or not Path(sample).exists():
        raise RuntimeError("An XTTS voice requires sample_path with reference audio")
    log(f"xtts sample={Path(sample).name}")

    sentences = _split_sentences(text)
    work = out_path.parent / f"{out_path.stem}_xtts"
    work.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    spans: list[dict] = []
    cursor = 0.0
    for index, sentence in enumerate(sentences):
        part = work / f"part_{index:03d}.wav"
        resp = httpx.post(
            f"{settings.xtts_server}/tts_to_audio/",
            json={"text": sentence, "speaker_wav": str(sample),
                  "language": voice.get("language", "pt")},
            timeout=600,
        )
        resp.raise_for_status()
        part.write_bytes(resp.content)
        length = audio_duration(part)
        spans.append({"word": sentence, "start": round(cursor, 3),
                      "end": round(cursor + length, 3)})
        cursor += length
        parts.append(part)

    _concat_audio(parts, out_path)
    return Narration(out_path, audio_duration(out_path), words_from_sentences(spans))


# ---------------- VoiceStudio (local) ----------------

def voicestudio_url() -> str:
    from . import connectors

    saved = connectors.credentials("voicestudio").get("base_url", "")
    return (saved or settings.voicestudio_url).rstrip("/")


def _voicestudio(text: str, out_path: Path, voice: dict, log) -> Narration:
    """VoiceStudio on this machine, through its OpenAI-shaped speech API.

    `voice` is a profile id — including one cloned from the user's own
    recording, which is the whole point: their voice never leaves the machine
    and costs nothing per word.

    Like XTTS and fish.audio it answers with audio and no timings, so the text
    is synthesized sentence by sentence and each file measured. Estimating over
    the total instead would let the error pile up across a 90-second narration,
    and the captions are word-synced.
    """
    base = voicestudio_url()
    profile = voice.get("provider_voice_id") or "default"
    engine = (voice.get("settings", {}) or {}).get("engine") if isinstance(
        voice.get("settings"), dict) else ""
    model = engine or settings.voicestudio_engine or "tts-1"
    log(f"voicestudio voice={profile} engine={model} at {base}")

    sentences = _split_sentences(text)
    work = out_path.parent / f"{out_path.stem}_vs"
    work.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    spans: list[dict] = []
    cursor = 0.0
    for index, sentence in enumerate(sentences):
        part = work / f"part_{index:03d}.wav"
        try:
            resp = httpx.post(
                f"{base}/v1/audio/speech",
                json={"model": model, "input": sentence, "voice": profile,
                      "response_format": "wav"},
                timeout=600,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"VoiceStudio is not answering at {base} ({type(exc).__name__}). "
                f"Start the app, or point VOICESTUDIO_URL at the machine that "
                f"runs it.") from exc
        if resp.status_code == 404:
            # A profile that was deleted in VoiceStudio: no retry fixes it, and
            # falling back to the system voice is better than a failed render.
            raise VoiceUnavailable(
                f"VoiceStudio does not know the voice '{profile}' any more — "
                f"it was probably deleted there.")
        resp.raise_for_status()
        part.write_bytes(resp.content)
        length = audio_duration(part)
        spans.append({"word": sentence, "start": round(cursor, 3),
                      "end": round(cursor + length, 3)})
        cursor += length
        parts.append(part)

    _concat_audio(parts, out_path)
    return Narration(out_path, audio_duration(out_path), words_from_sentences(spans))


# ---------------- Fish Audio ----------------

FISH_BASE = "https://api.fish.audio"


def _fishaudio(text: str, out_path: Path, voice: dict, log) -> Narration:
    """Fish Audio TTS.

    The API returns audio only, with no time markers. Synthesizing the whole
    text in one go would force us to estimate the times over the total duration
    — the error piles up and the karaoke caption comes out of sync. That is why
    we synthesize sentence by sentence and measure each file: every sentence
    becomes a real anchor.
    """
    if not settings.fishaudio_api_key:
        raise RuntimeError("FISHAUDIO_API_KEY is not set")

    reference_id = voice.get("provider_voice_id") or settings.fishaudio_model
    extra = _voice_settings(voice)
    model = extra.get("fish_model") or settings.fishaudio_backend
    log(f"fish.audio model={model} voice={reference_id or '(default)'}")

    sentences = _split_sentences(text)
    work = out_path.parent / f"{out_path.stem}_fish"
    work.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    spans: list[dict] = []
    cursor = 0.0

    for index, sentence in enumerate(sentences):
        part = work / f"part_{index:03d}.mp3"
        _fish_request(sentence, part, reference_id, model, extra)
        length = audio_duration(part)
        spans.append({"word": sentence, "start": round(cursor, 3),
                      "end": round(cursor + length, 3)})
        cursor += length
        parts.append(part)

    _concat_audio(parts, out_path)
    return Narration(out_path, audio_duration(out_path), words_from_sentences(spans))


def _fish_request(text: str, dest: Path, reference_id: str, model: str,
                  extra: dict) -> None:
    body: dict = {
        "text": text,
        "format": "mp3",
        "mp3_bitrate": 128,
        "normalize": True,
        "latency": extra.get("latency", "normal"),
        "temperature": float(extra.get("temperature", 0.7)),
        "top_p": float(extra.get("top_p", 0.7)),
    }
    if reference_id:
        body["reference_id"] = reference_id

    prosody = {}
    if extra.get("speed") is not None:
        prosody["speed"] = float(extra["speed"])
    if extra.get("volume") is not None:
        prosody["volume"] = float(extra["volume"])
    if prosody:
        body["prosody"] = prosody

    resp = httpx.post(
        f"{FISH_BASE}/v1/tts",
        headers={
            "Authorization": f"Bearer {settings.fishaudio_api_key}",
            "Content-Type": "application/json",
            # selects the model family (s1, s2-pro, s2.1-pro, s2.1-pro-free)
            "model": model,
        },
        json=body,
        timeout=300,
    )
    if resp.status_code in (401, 402, 403, 429):
        raise VoiceUnavailable(_fish_reason(resp))
    if resp.status_code >= 400:
        raise RuntimeError(
            f"fish.audio returned {resp.status_code}: {resp.text[:300]}")
    dest.write_bytes(resp.content)


def _fish_reason(resp: httpx.Response) -> str:
    """Plain reading of a refusal from fish.audio.

    402 is the one that confuses people: API credit is billed separately from
    the platform credit shown on the website, so an account that looks funded
    still gets refused here.
    """
    if resp.status_code == 402:
        return ("fish.audio has no API credit left. It is billed separately "
                "from the platform credit shown on the site — top it up at "
                "fish.audio/app/developers, or switch the voice to edge-tts "
                "(free) under Voices.")
    if resp.status_code in (401, 403):
        return ("fish.audio rejected the API key. Check FISHAUDIO_API_KEY, or "
                "the key saved under Accounts.")
    if resp.status_code == 429:
        return "fish.audio rate limit reached."
    return f"fish.audio refused the request ({resp.status_code})."


def list_fish_voices(query: str = "", language: str = "pt",
                     page_size: int = 30) -> list[dict]:
    """fish.audio voice catalog (marketplace + your own voices).

    The public endpoint answers without authentication, so you can browse the
    voices before having a key. With the key, the listing also includes the
    private voices on your account.
    """
    params = {"page_size": page_size, "page_number": 1}
    if query:
        params["title"] = query
    if language:
        params["language"] = language

    headers = ({"Authorization": f"Bearer {settings.fishaudio_api_key}"}
               if settings.fishaudio_api_key else {})
    resp = httpx.get(f"{FISH_BASE}/model", headers=headers,
                     params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    items = payload.get("items", payload if isinstance(payload, list) else [])
    return [_fish_voice_summary(item) for item in items
            if item.get("_id") or item.get("id")]


def _fish_voice_summary(item: dict) -> dict:
    """Summarizes a fish.audio model for the interface.

    `samples[0].audio` is a ready-made sample hosted by them — you can listen to
    the voice before installing it, without spending API credit on synthesis.
    """
    samples = item.get("samples") or []
    sample = samples[0] if samples else {}
    return {
        "id": item.get("_id") or item.get("id"),
        "name": item.get("title") or item.get("name") or "(unnamed)",
        "languages": item.get("languages", []),
        "likes": item.get("like_count", 0),
        "author": (item.get("author") or {}).get("nickname", ""),
        "description": (item.get("description") or "")[:160],
        "sample_text": (sample.get("text") or item.get("default_text") or "")[:120],
        "has_sample": bool(sample.get("audio")),
    }


def fish_sample_url(reference_id: str) -> str | None:
    """URL of a voice's sample audio (it disappears after a while, which is why
    it is fetched on demand instead of stored)."""
    headers = ({"Authorization": f"Bearer {settings.fishaudio_api_key}"}
               if settings.fishaudio_api_key else {})
    resp = httpx.get(f"{FISH_BASE}/model/{reference_id}", headers=headers, timeout=45)
    if resp.status_code != 200:
        return None
    samples = resp.json().get("samples") or []
    return samples[0].get("audio") if samples else None


# ---------------- shared synthesis utilities ----------------

def _voice_settings(voice: dict) -> dict:
    """settings_json comes in as a string from the database and as a dict when
    it is assembled by hand."""
    raw = voice.get("settings_json")
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    return raw or {}


def _split_sentences(text: str, max_chars: int = 220) -> list[str]:
    """Splits into sentences to synthesize in parts; very long sentences are
    broken at commas so as not to blow the provider's chunk limit."""
    rough = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    out: list[str] = []
    for sentence in rough:
        if len(sentence) <= max_chars:
            out.append(sentence)
            continue
        buffer = ""
        for piece in sentence.split(", "):
            candidate = f"{buffer}, {piece}" if buffer else piece
            if len(candidate) > max_chars and buffer:
                out.append(buffer)
                buffer = piece
            else:
                buffer = candidate
        if buffer:
            out.append(buffer)
    return out or [text.strip()]


def _concat_audio(parts: list[Path], out_path: Path) -> None:
    if len(parts) == 1:
        shutil.copy(parts[0], out_path)
        return
    listing = parts[0].parent / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing.name,
         "-c", "copy", str(out_path.resolve())],
        cwd=parts[0].parent, check=True, capture_output=True,
    )


def list_edge_voices(language_prefix: str = "pt-BR") -> list[dict]:
    import edge_tts

    async def run() -> list[dict]:
        voices = await edge_tts.list_voices()
        return [
            {"id": v["ShortName"], "name": v["FriendlyName"],
             "gender": v["Gender"], "locale": v["Locale"]}
            for v in voices if v["Locale"].startswith(language_prefix)
        ]

    return asyncio.run(run())
