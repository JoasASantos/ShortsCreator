"""Caption sync — the heart of the product.

This does not test the TTS provider (network); it tests the math that turns
whatever the provider returns into word timings: weighted distribution,
conversion of sentence-level markers and the leading-silence trim.
"""
from __future__ import annotations

import httpx
import pytest

from app.pipeline import tts

from conftest import needs_ffmpeg


def test_estimate_words_covers_the_whole_duration():
    words = tts.estimate_words("uma frase curta de teste aqui", 5.0)
    assert words[0]["start"] == 0.0
    assert abs(words[-1]["end"] - 5.0) < 0.01
    # no gaps and no overlap between consecutive words
    for a, b in zip(words, words[1:]):
        assert abs(a["end"] - b["start"]) < 0.01


def test_estimate_words_weights_by_word_length():
    words = tts.estimate_words("a extraordinariamente", 4.0)
    short_word = words[0]["end"] - words[0]["start"]
    long_word = words[1]["end"] - words[1]["start"]
    assert long_word > short_word * 3


def test_estimate_words_with_empty_text():
    assert tts.estimate_words("", 3.0) == []
    assert tts.estimate_words("   ", 3.0) == []


def test_words_from_sentences_confines_the_error_to_the_sentence():
    """Providers with no per-word timing (fish/XTTS) return it per sentence. The
    start of every sentence has to match the measured value EXACTLY, so the
    drift does not accumulate over the course of the video."""
    sentences = [
        {"word": "primeira frase aqui", "start": 0.0, "end": 1.5},
        {"word": "segunda frase bem mais longa que a outra", "start": 1.5, "end": 5.0},
    ]
    words = tts.words_from_sentences(sentences)
    assert words[0]["start"] == 0.0
    # the word that opens the second sentence starts at the sentence's measured time
    idx = len(sentences[0]["word"].split())
    assert abs(words[idx]["start"] - 1.5) < 0.01
    assert abs(words[-1]["end"] - 5.0) < 0.05
    assert len(words) == sum(len(s["word"].split()) for s in sentences)


def _tone_with_silence(path, silence_ms: int = 800, tone_s: float = 1.2):
    """A tone preceded by silence. `adelay` inserts the silence inside a single
    input — chaining an infinite anullsrc with -t in the wrong place yields a
    file with no end."""
    import subprocess

    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={tone_s}",
         "-af", f"adelay={silence_ms}|{silence_ms}", "-c:a", "libmp3lame", str(path)],
        check=True, capture_output=True,
    )
    return path


@needs_ffmpeg
def test_trim_leading_silence_shifts_the_timings(tmp_path):
    audio = _tone_with_silence(tmp_path / "with_silence.mp3")
    narration = tts.Narration(audio, tts.audio_duration(audio),
                              [{"word": "oi", "start": 0.9, "end": 1.4}])
    trimmed = tts.trim_leading_silence(narration, tmp_path / "trimmed.mp3")

    assert trimmed is not narration, "the leading silence should have been trimmed"
    assert trimmed.duration < narration.duration
    # the hook starts almost immediately after the trim
    assert 0.0 <= trimmed.words[0]["start"] < 0.35
    # no negative timing — it would break the .ass
    assert all(w["start"] >= 0 and w["end"] >= 0 for w in trimmed.words)


@needs_ffmpeg
def test_trim_leading_silence_returns_the_same_narration_when_there_is_none(tmp_path):
    audio = _tone_with_silence(tmp_path / "without_silence.mp3", silence_ms=0)
    narration = tts.Narration(audio, tts.audio_duration(audio),
                              [{"word": "oi", "start": 0.0, "end": 0.5}])
    assert tts.trim_leading_silence(narration, tmp_path / "x.mp3") is narration


def test_split_sentences_respects_the_character_limit():
    text = ("Primeira frase. " * 30).strip()
    parts = tts._split_sentences(text, max_chars=100)  # noqa: SLF001
    assert parts
    assert all(len(p) <= 100 for p in parts)
    # no text lost along the way
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")


# --------------------------------------------- a paid voice running dry

def _refusal(status: int) -> httpx.Response:
    return httpx.Response(status, json={"status": status, "message": "nope"},
                          request=httpx.Request("POST", "https://api.fish.audio/v1/tts"))


def test_no_api_credit_explains_that_it_is_billed_separately():
    """402 is the confusing one: fish.audio bills API credit apart from the
    platform credit shown on the site, so a funded-looking account still gets
    refused. The message has to say that, or the user hunts the wrong balance."""
    reason = tts._fish_reason(_refusal(402))          # noqa: SLF001
    assert "API credit" in reason
    assert "separately" in reason
    assert "edge-tts" in reason                        # the free way out


def test_a_rejected_key_and_a_rate_limit_read_differently():
    assert "API key" in tts._fish_reason(_refusal(401))      # noqa: SLF001
    assert "rate limit" in tts._fish_reason(_refusal(429))   # noqa: SLF001


@needs_ffmpeg
def test_a_voice_without_credit_falls_back_instead_of_losing_the_short(tmp_path, monkeypatch):
    """A short must not be lost because a voice account ran dry: the narration
    still gets made in the system voice, and the log says why it changed."""
    def refuse(*a, **k):
        raise tts.VoiceUnavailable("no credit")

    monkeypatch.setattr(tts, "_fish_request", refuse)
    warnings = []
    out = tmp_path / "narration.mp3"

    narration = tts.synthesize(
        "Uma frase curta para narrar.", out,
        {"provider": "fishaudio", "provider_voice_id": "x"},
        log=lambda m, level="info": warnings.append((level, m)))

    assert out.exists() and narration.duration > 0
    assert narration.words, "the fallback still has to produce word timings"
    assert any(level == "warn" for level, _ in warnings)


def test_a_network_error_is_not_swallowed_by_the_fallback(tmp_path, monkeypatch):
    """Only a refusal falls back. A transient failure should surface — quietly
    swapping the voice of the video would be worse than failing."""
    def blow_up(*a, **k):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(tts, "_fish_request", blow_up)
    with pytest.raises(httpx.ConnectError):
        tts.synthesize("frase", tmp_path / "n.mp3", {"provider": "fishaudio"})


def test_edge_itself_never_falls_back_to_edge(tmp_path, monkeypatch):
    """Guards against an infinite bounce if the free provider is the one
    refusing."""
    def refuse(*a, **k):
        raise tts.VoiceUnavailable("edge refused")

    monkeypatch.setattr(tts, "_edge", refuse)
    with pytest.raises(tts.VoiceUnavailable):
        tts.synthesize("frase", tmp_path / "n.mp3", {"provider": "edge"})
