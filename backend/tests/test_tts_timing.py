"""Sincronia da legenda — o coração do produto.

Não testa provedor de TTS (rede); testa a matemática que transforma o que o
provedor devolve em timings de palavra: distribuição por peso, conversão de
marcadores de frase e o corte de silêncio inicial.
"""
from __future__ import annotations

from app.pipeline import tts

from conftest import needs_ffmpeg


def test_estimate_words_cobre_a_duracao_inteira():
    words = tts.estimate_words("uma frase curta de teste aqui", 5.0)
    assert words[0]["start"] == 0.0
    assert abs(words[-1]["end"] - 5.0) < 0.01
    # sem buracos nem sobreposição entre palavras consecutivas
    for a, b in zip(words, words[1:]):
        assert abs(a["end"] - b["start"]) < 0.01


def test_estimate_words_pesa_por_tamanho_da_palavra():
    words = tts.estimate_words("a extraordinariamente", 4.0)
    curta = words[0]["end"] - words[0]["start"]
    longa = words[1]["end"] - words[1]["start"]
    assert longa > curta * 3


def test_estimate_words_texto_vazio():
    assert tts.estimate_words("", 3.0) == []
    assert tts.estimate_words("   ", 3.0) == []


def test_words_from_sentences_confina_erro_na_frase():
    """Provedores sem timing por palavra (fish/XTTS) devolvem por frase. O
    início de cada frase precisa bater EXATO com o medido, para o desvio não
    acumular ao longo do vídeo."""
    sentences = [
        {"word": "primeira frase aqui", "start": 0.0, "end": 1.5},
        {"word": "segunda frase bem mais longa que a outra", "start": 1.5, "end": 5.0},
    ]
    words = tts.words_from_sentences(sentences)
    assert words[0]["start"] == 0.0
    # a palavra que abre a segunda frase começa no tempo medido da frase
    idx = len(sentences[0]["word"].split())
    assert abs(words[idx]["start"] - 1.5) < 0.01
    assert abs(words[-1]["end"] - 5.0) < 0.05
    assert len(words) == sum(len(s["word"].split()) for s in sentences)


def _tone_with_silence(path, silence_ms: int = 800, tone_s: float = 1.2):
    """Tom precedido de silêncio. `adelay` insere o silêncio num input só —
    encadear anullsrc infinito com -t no lugar errado gera arquivo sem fim."""
    import subprocess

    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={tone_s}",
         "-af", f"adelay={silence_ms}|{silence_ms}", "-c:a", "libmp3lame", str(path)],
        check=True, capture_output=True,
    )
    return path


@needs_ffmpeg
def test_trim_leading_silence_desloca_timings(tmp_path):
    audio = _tone_with_silence(tmp_path / "com_silencio.mp3")
    narration = tts.Narration(audio, tts.audio_duration(audio),
                              [{"word": "oi", "start": 0.9, "end": 1.4}])
    trimmed = tts.trim_leading_silence(narration, tmp_path / "cortado.mp3")

    assert trimmed is not narration, "o silêncio inicial deveria ter sido cortado"
    assert trimmed.duration < narration.duration
    # o hook começa quase imediatamente depois do corte
    assert 0.0 <= trimmed.words[0]["start"] < 0.35
    # nenhum timing negativo — quebraria o .ass
    assert all(w["start"] >= 0 and w["end"] >= 0 for w in trimmed.words)


@needs_ffmpeg
def test_trim_leading_silence_sem_silencio_devolve_o_mesmo(tmp_path):
    audio = _tone_with_silence(tmp_path / "sem_silencio.mp3", silence_ms=0)
    narration = tts.Narration(audio, tts.audio_duration(audio),
                              [{"word": "oi", "start": 0.0, "end": 0.5}])
    assert tts.trim_leading_silence(narration, tmp_path / "x.mp3") is narration


def test_split_sentences_respeita_limite_de_caracteres():
    texto = ("Primeira frase. " * 30).strip()
    partes = tts._split_sentences(texto, max_chars=100)  # noqa: SLF001
    assert partes
    assert all(len(p) <= 100 for p in partes)
    # nada de texto perdido no caminho
    assert "".join(partes).replace(" ", "") == texto.replace(" ", "")
