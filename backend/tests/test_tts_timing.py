"""Caption sync — the heart of the product.

This does not test the TTS provider (network); it tests the math that turns
whatever the provider returns into word timings: weighted distribution,
conversion of sentence-level markers and the leading-silence trim.
"""
from __future__ import annotations

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
