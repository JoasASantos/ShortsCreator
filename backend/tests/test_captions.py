"""Captions: grouping, timing and the 340 px safe area QA enforces."""
from __future__ import annotations

import re

from app.pipeline import captions


def test_group_lines_respects_max_words(words):
    lines = captions.group_lines(words, max_words=2)
    assert all(len(line) <= 2 for line in lines)
    assert sum(len(line) for line in lines) == len(words)


def test_group_lines_preserves_order_and_does_not_overlap(words):
    lines = captions.group_lines(words, max_words=3)
    flat = [w["word"] for line in lines for w in line]
    assert flat == [w["word"] for w in words]
    # each line is contiguous in time and does not spill into the next one
    for line in lines:
        assert line[0]["start"] <= line[-1]["end"]
    for a, b in zip(lines, lines[1:]):
        assert a[-1]["end"] <= b[0]["start"] + 1e-6


def test_group_lines_breaks_on_a_long_pause():
    """A pause longer than half a second is the end of a spoken sentence: the
    caption has to break there, otherwise the line sits on screen with no
    matching audio."""
    words = [{"word": "antes", "start": 0.0, "end": 0.4},
             {"word": "depois", "start": 1.4, "end": 1.8}]
    lines = captions.group_lines(words, max_words=4)
    assert len(lines) == 2


def test_build_ass_karaoke_highlights_the_active_word(tmp_path, words):
    out = captions.build_ass(words, tmp_path / "c.ass", style="karaoke")
    text = out.read_text(encoding="utf-8")
    assert "[Script Info]" in text and "PlayResX: 1080" in text
    assert "PlayResY: 1920" in text
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert dialogues, "no caption event was generated"
    # the active word gets the amber color; the rest of the line stays white
    assert any(captions.ACTIVE_COLOR in l for l in dialogues)
    # one event per word: the whole line shows up, only the highlight moves
    assert len(dialogues) == len(words)


def test_build_ass_block_shows_the_whole_line_without_a_highlight(tmp_path, words):
    text = captions.build_ass(words, tmp_path / "c.ass", style="bloco").read_text("utf-8")
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert dialogues
    assert not any(captions.ACTIVE_COLOR in l for l in dialogues)
    # fewer events than words: each event is a whole sentence
    assert len(dialogues) < len(words)


def test_build_ass_word_style_emits_one_event_per_word(tmp_path, words):
    text = captions.build_ass(words, tmp_path / "c.ass", style="palavra").read_text("utf-8")
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == len(words)


def test_build_ass_title_and_watermark_get_their_own_styles(tmp_path, words):
    text = captions.build_ass(words, tmp_path / "c.ass", title="Meu título",
                              watermark="@canal").read_text("utf-8")
    assert any(l.startswith("Dialogue:") and ",Titulo," in l for l in text.splitlines())
    assert any(l.startswith("Dialogue:") and ",Marca," in l for l in text.splitlines())


def test_escape_neutralizes_braces_that_would_break_the_ass():
    """A brace in narrated text would be read as an override tag by libass."""
    assert "{" not in captions._escape("texto {com} chave")   # noqa: SLF001
    assert "}" not in captions._escape("texto {com} chave")   # noqa: SLF001


def test_build_srt_has_indices_and_timestamps(tmp_path, words):
    text = captions.build_srt(words, tmp_path / "c.srt").read_text("utf-8")
    assert text.strip().startswith("1")
    assert re.search(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", text)
    # every word shows up in the file
    for word in words:
        assert word["word"] in text


def test_vertical_margin_keeps_the_caption_clear_of_the_app_ui():
    """The bottom 340 px are covered by the app's own UI. Every position has to
    stay out of that band, otherwise the caption disappears behind the buttons."""
    assert captions.POSITION_MARGIN_V["baixo"] >= captions.SAFE_BOTTOM
    assert captions.POSITION_MARGIN_V["centro"] > captions.POSITION_MARGIN_V["baixo"]
    assert captions.POSITION_MARGIN_V["topo"] > captions.POSITION_MARGIN_V["centro"]


def test_empty_word_list_does_not_break(tmp_path):
    assert captions.group_lines([]) == []
    out = captions.build_ass([], tmp_path / "vazio.ass")
    assert out.exists()
    assert "[Events]" in out.read_text("utf-8")


# ------------------------------------------------------------- watermark

def _mark_style(ass_text: str) -> list[str]:
    """Fields of the `Marca` style line. Splitting on the first ':' drops the
    `Style` prefix, so index 0 is the style name and the rest lines up with the
    Format row: 2 = fontsize, 18 = alignment, 21 = MarginV."""
    line = next(l for l in ass_text.splitlines() if l.startswith("Style: Marca"))
    return [field.strip() for field in line.split(":", 1)[1].split(",")]


def test_watermark_alpha_is_inverted_in_ass():
    """ASS colours are &HAABBGGRR and the alpha byte runs backwards: 0x00 is
    fully opaque. Getting this the wrong way round makes the mark vanish."""
    assert captions._watermark_colour(1.0) == "&H00FFFFFF"   # noqa: SLF001
    assert captions._watermark_colour(0.0) == "&HFFFFFFFF"   # noqa: SLF001
    assert captions._watermark_colour(0.6) == "&H66FFFFFF"   # noqa: SLF001


def test_watermark_position_maps_to_the_ass_alignment(tmp_path, words):
    for position, align in captions.WATERMARK_ALIGN.items():
        text = captions.build_ass(words, tmp_path / f"{position}.ass",
                                  watermark="@channel",
                                  watermark_position=position).read_text("utf-8")
        style = _mark_style(text)
        assert style[18] == str(align), position


def test_watermark_size_changes_the_font_size(tmp_path, words):
    sizes = []
    for size in ("pequeno", "medio", "grande"):
        text = captions.build_ass(words, tmp_path / f"{size}.ass",
                                  watermark="@channel",
                                  watermark_size=size).read_text("utf-8")
        style = _mark_style(text)
        sizes.append(float(style[2]))
    assert sizes == sorted(sizes) and sizes[0] < sizes[-1]


def test_top_anchored_watermark_clears_the_app_interface(tmp_path, words):
    """Both anchors have to stay out of the app's own chrome — the bottom band
    it covers and the top row of buttons."""
    for position in ("topo_centro", "baixo_centro"):
        text = captions.build_ass(words, tmp_path / f"{position}.ass",
                                  watermark="@channel",
                                  watermark_position=position).read_text("utf-8")
        style = _mark_style(text)
        assert float(style[21]) >= 120, position


def test_no_watermark_means_no_mark_event(tmp_path, words):
    text = captions.build_ass(words, tmp_path / "none.ass").read_text("utf-8")
    assert not [l for l in text.splitlines()
                if l.startswith("Dialogue:") and ",Marca," in l]
