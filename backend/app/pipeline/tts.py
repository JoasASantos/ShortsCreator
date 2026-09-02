"""Síntese de voz com timings por palavra.

O truque central: em vez de transcrever o áudio depois (ASR), pegamos os
timings direto do provedor de TTS — edge-tts emite eventos WordBoundary e a
ElevenLabs devolve `character_start_times_seconds`. Timings exatos, custo zero.
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


def synthesize(text: str, out_path: Path, voice: dict | None = None,
               log=lambda m: None) -> Narration:
    voice = voice or {}
    provider = voice.get("provider") or settings.tts_provider

    if provider == "edge":
        narration = _edge(text, out_path, voice, log)
    elif provider == "elevenlabs":
        narration = _elevenlabs(text, out_path, voice, log)
    elif provider == "xtts":
        narration = _xtts(text, out_path, voice, log)
    elif provider == "fishaudio":
        narration = _fishaudio(text, out_path, voice, log)
    else:
        raise RuntimeError(f"TTS_PROVIDER desconhecido: {provider}")

    if not narration.words:
        narration.words = estimate_words(text, narration.duration)
    return narration


def trim_leading_silence(narration: Narration, out_path: Path,
                         threshold_db: str = "-45dB", min_dur: float = 0.15) -> Narration:
    """Corta o silêncio morto no início do áudio — usado pelo autoajuste de QA
    quando o hook começa atrasado e mata a retenção nos primeiros segundos."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(narration.audio_path),
         "-af", f"silencedetect=noise={threshold_db}:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(v) for v in re.findall(r"silence_start:\s*(-?\d+\.?\d*)", proc.stderr)]
    ends = [float(v) for v in re.findall(r"silence_end:\s*(-?\d+\.?\d*)", proc.stderr)]

    cut = ends[0] if starts and ends and starts[0] <= 0.4 else 0.0
    if cut <= 0.05:
        return narration  # nada relevante para cortar

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
    """Converte marcadores de FRASE em marcadores de PALAVRA.

    Cada frase tem início e fim reais medidos pelo TTS; dentro dela as palavras
    são distribuídas por peso. O erro fica confinado à frase (décimos de
    segundo) em vez de acumular ao longo do vídeo inteiro, que é o que
    acontece quando se estima sobre a duração total.
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
    """Fallback: distribui o tempo proporcional ao tamanho de cada palavra."""
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


# ---------------- edge-tts (grátis, vozes neurais pt-BR) ----------------

def _edge(text: str, out_path: Path, voice: dict, log) -> Narration:
    import edge_tts

    voice_name = voice.get("provider_voice_id") or settings.edge_voice
    rate = voice.get("rate", "+0%")
    pitch = voice.get("pitch", "+0Hz")
    log(f"edge-tts voz={voice_name} rate={rate} pitch={pitch}")

    words: list[dict] = []

    sentences: list[dict] = []

    async def run() -> None:
        # boundary="WordBoundary" é o que dá timing por PALAVRA; o padrão da
        # biblioteca é SentenceBoundary, que só marca frases inteiras e faria a
        # legenda karaokê cair no fallback estimado (dessincronizada).
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

    # O endpoint público da Microsoft derruba conexão com alguma frequência;
    # sem retry uma falha transiente mata o job inteiro no meio do pipeline.
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
                    log("Nenhum marcador de tempo do TTS — legenda usará estimativa")
                return Narration(out_path, duration, timings)
            raise RuntimeError("edge-tts devolveu áudio vazio")
        except Exception as exc:  # noqa: BLE001 — qualquer falha de rede entra no retry
            last_error = exc
            if attempt < EDGE_MAX_ATTEMPTS:
                delay = 2 ** (attempt - 1)
                log(f"edge-tts falhou ({exc}); nova tentativa em {delay}s "
                    f"[{attempt}/{EDGE_MAX_ATTEMPTS}]")
                time.sleep(delay)

    raise RuntimeError(
        f"edge-tts falhou após {EDGE_MAX_ATTEMPTS} tentativas: {last_error}"
    )


# ---------------- ElevenLabs (clonagem de voz de personagem) ----------------

def _elevenlabs(text: str, out_path: Path, voice: dict, log) -> Narration:
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY não configurada")
    voice_id = voice.get("provider_voice_id")
    if not voice_id:
        raise RuntimeError("Voz ElevenLabs sem provider_voice_id")

    log(f"elevenlabs voz={voice_id}")
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


# ---------------- XTTS local (clonagem por sample de áudio) ----------------

def _xtts(text: str, out_path: Path, voice: dict, log) -> Narration:
    """XTTS local. Também não devolve timings, então sintetiza frase a frase e
    mede cada arquivo — mesma estratégia usada no fish.audio."""
    sample = voice.get("sample_path")
    if not sample or not Path(sample).exists():
        raise RuntimeError("Voz XTTS exige sample_path com áudio de referência")
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


# ---------------- Fish Audio ----------------

FISH_BASE = "https://api.fish.audio"


def _fishaudio(text: str, out_path: Path, voice: dict, log) -> Narration:
    """Fish Audio TTS.

    A API devolve só áudio, sem marcação de tempo. Sintetizar o texto inteiro
    de uma vez obrigaria a estimar os tempos sobre a duração total — o erro se
    acumula e a legenda karaokê sai dessincronizada. Por isso sintetizamos
    frase a frase e medimos cada arquivo: cada frase vira uma âncora real.
    """
    if not settings.fishaudio_api_key:
        raise RuntimeError("FISHAUDIO_API_KEY não configurada")

    reference_id = voice.get("provider_voice_id") or settings.fishaudio_model
    extra = _voice_settings(voice)
    model = extra.get("fish_model") or settings.fishaudio_backend
    log(f"fish.audio modelo={model} voz={reference_id or '(padrão)'}")

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
            # seleciona a família do modelo (s1, s2-pro, s2.1-pro, s2.1-pro-free)
            "model": model,
        },
        json=body,
        timeout=300,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"fish.audio devolveu {resp.status_code}: {resp.text[:300]}")
    dest.write_bytes(resp.content)


def list_fish_voices(query: str = "", language: str = "pt",
                     page_size: int = 30) -> list[dict]:
    """Catálogo de vozes do fish.audio (marketplace + suas vozes).

    O endpoint público responde sem autenticação, então dá para navegar as
    vozes antes de ter a chave. Com a chave, a listagem também inclui as
    vozes privadas da sua conta.
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
    return [{
        "id": item.get("_id") or item.get("id"),
        "name": item.get("title") or item.get("name") or "(sem nome)",
        "languages": item.get("languages", []),
        "likes": item.get("like_count", 0),
        "author": (item.get("author") or {}).get("nickname", ""),
        "description": (item.get("description") or "")[:120],
    } for item in items if item.get("_id") or item.get("id")]


# ---------------- utilidades compartilhadas de síntese ----------------

def _voice_settings(voice: dict) -> dict:
    """settings_json vem como string do banco e como dict quando montado na mão."""
    raw = voice.get("settings_json")
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    return raw or {}


def _split_sentences(text: str, max_chars: int = 220) -> list[str]:
    """Divide em frases para sintetizar por partes; frases muito longas são
    quebradas em vírgulas para não estourar o limite de chunk do provedor."""
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
