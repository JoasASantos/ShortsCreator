"""Legenda: agrupamento, tempos e a safe area de 340px que o QA cobra."""
from __future__ import annotations

import re

from app.pipeline import captions


def test_group_lines_respeita_max_palavras(words):
    lines = captions.group_lines(words, max_words=2)
    assert all(len(line) <= 2 for line in lines)
    assert sum(len(line) for line in lines) == len(words)


def test_group_lines_preserva_ordem_e_nao_sobrepoe(words):
    lines = captions.group_lines(words, max_words=3)
    flat = [w["word"] for line in lines for w in line]
    assert flat == [w["word"] for w in words]
    # cada linha é contígua no tempo e não invade a seguinte
    for line in lines:
        assert line[0]["start"] <= line[-1]["end"]
    for a, b in zip(lines, lines[1:]):
        assert a[-1]["end"] <= b[0]["start"] + 1e-6


def test_group_lines_quebra_em_pausa_longa():
    """Uma pausa de mais de meio segundo é fim de frase falada: a legenda
    precisa quebrar ali, senão a linha fica na tela sem áudio correspondente."""
    words = [{"word": "antes", "start": 0.0, "end": 0.4},
             {"word": "depois", "start": 1.4, "end": 1.8}]
    lines = captions.group_lines(words, max_words=4)
    assert len(lines) == 2


def test_build_ass_karaoke_destaca_a_palavra_ativa(tmp_path, words):
    out = captions.build_ass(words, tmp_path / "c.ass", style="karaoke")
    text = out.read_text(encoding="utf-8")
    assert "[Script Info]" in text and "PlayResX: 1080" in text
    assert "PlayResY: 1920" in text
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert dialogues, "nenhum evento de legenda gerado"
    # a palavra ativa recebe a cor âmbar; o resto da linha fica em branco
    assert any(captions.ACTIVE_COLOR in l for l in dialogues)
    # um evento por palavra: a linha inteira aparece, só o destaque anda
    assert len(dialogues) == len(words)


def test_build_ass_bloco_mostra_a_linha_inteira_sem_destaque(tmp_path, words):
    text = captions.build_ass(words, tmp_path / "c.ass", style="bloco").read_text("utf-8")
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert dialogues
    assert not any(captions.ACTIVE_COLOR in l for l in dialogues)
    # menos eventos que palavras: cada evento é uma frase inteira
    assert len(dialogues) < len(words)


def test_build_ass_palavra_gera_um_evento_por_palavra(tmp_path, words):
    text = captions.build_ass(words, tmp_path / "c.ass", style="palavra").read_text("utf-8")
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == len(words)


def test_build_ass_titulo_e_marca_entram_como_estilos_proprios(tmp_path, words):
    text = captions.build_ass(words, tmp_path / "c.ass", title="Meu título",
                              watermark="@canal").read_text("utf-8")
    assert any(l.startswith("Dialogue:") and ",Titulo," in l for l in text.splitlines())
    assert any(l.startswith("Dialogue:") and ",Marca," in l for l in text.splitlines())


def test_escape_neutraliza_chaves_que_quebrariam_o_ass():
    """Chave em texto narrado seria lida como tag de override pelo libass."""
    assert "{" not in captions._escape("texto {com} chave")   # noqa: SLF001
    assert "}" not in captions._escape("texto {com} chave")   # noqa: SLF001


def test_build_srt_tem_indices_e_timestamps(tmp_path, words):
    text = captions.build_srt(words, tmp_path / "c.srt").read_text("utf-8")
    assert text.strip().startswith("1")
    assert re.search(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", text)
    # todas as palavras aparecem no arquivo
    for word in words:
        assert word["word"] in text


def test_margem_vertical_mantem_legenda_fora_da_interface():
    """Os 340px de baixo são cobertos pela UI do app. Toda posição precisa ficar
    fora dessa faixa, senão a legenda some atrás dos botões."""
    assert captions.POSITION_MARGIN_V["baixo"] >= captions.SAFE_BOTTOM
    assert captions.POSITION_MARGIN_V["centro"] > captions.POSITION_MARGIN_V["baixo"]
    assert captions.POSITION_MARGIN_V["topo"] > captions.POSITION_MARGIN_V["centro"]


def test_words_vazio_nao_quebra(tmp_path):
    assert captions.group_lines([]) == []
    out = captions.build_ass([], tmp_path / "vazio.ass")
    assert out.exists()
    assert "[Events]" in out.read_text("utf-8")
