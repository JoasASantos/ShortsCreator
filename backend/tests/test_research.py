"""Material that informs the script without appearing in it.

The case that asked for it: a film trailer is the footage, and three review
videos are what the writer read before writing. Both were "links" before, and
pasting them in the same field made them all footage — the short would cut to a
reviewer's webcam.

So the two are separated at the input, and what has to hold is: background is
read, never shown; one item failing costs that item and nothing else; and the
prompt is told which is which, because a model handed four transcripts treats
the loudest one as the subject.
"""
from __future__ import annotations

import pytest

from app.pipeline import ingest, script as script_mod
from app.pipeline.ingest import SourceMaterial
from app.schemas import JobInput


def _job(**kwargs) -> JobInput:
    base = {"source_type": "video", "source": "https://youtu.be/trailer"}
    base.update(kwargs)
    return JobInput(**base)


def _material(**kwargs) -> SourceMaterial:
    base = {"kind": "video", "title": "Trailer", "transcript": "o que o trailer diz"}
    base.update(kwargs)
    return SourceMaterial(**base)


# ------------------------------------------------------------ what is read

def test_a_review_link_is_transcribed_and_never_downloaded_as_footage(tmp_path,
                                                                     monkeypatch):
    """The whole point: the trailer is what plays, the review is what informs."""
    monkeypatch.setattr(ingest, "download_video",
                        lambda url, work: (work / "v.mp4", {"title": "Review do canal X"}))
    monkeypatch.setattr(ingest, "_read_subtitles", lambda work: "")
    monkeypatch.setattr(ingest, "_whisper_transcribe",
                        lambda path, log: "o terceiro ato salva o filme")

    items = ingest.gather_research(
        _job(research="https://youtu.be/review-1"), tmp_path)

    assert [i["kind"] for i in items] == ["video"]
    assert items[0]["status"] == "ok"
    assert items[0]["title"] == "Review do canal X"
    assert "terceiro ato" in items[0]["text"]


def test_an_article_link_is_read_as_text(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "_ingest_article", lambda url: SourceMaterial(
        kind="artigo", title="Crítica", text="a direção de arte carrega tudo"))

    items = ingest.gather_research(
        _job(research="https://revista.test/critica"), tmp_path)

    assert items[0]["kind"] == "artigo"
    assert "direção de arte" in items[0]["text"]


def test_notes_pasted_next_to_the_links_are_material_too(tmp_path, monkeypatch):
    """Someone types what they know alongside the links, and it costs nothing
    to read."""
    monkeypatch.setattr(ingest, "_ingest_article", lambda url: SourceMaterial(
        kind="artigo", title="Crítica", text="texto"))

    items = ingest.gather_research(
        _job(research="foco no roteiro, não no elenco\nhttps://revista.test/x"),
        tmp_path)

    kinds = [i["kind"] for i in items]
    assert kinds == ["texto", "artigo"]
    assert "foco no roteiro" in items[0]["text"]


def test_nothing_given_costs_nothing(tmp_path, monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("nothing to read must touch nothing")

    monkeypatch.setattr(ingest, "download_video", explode)
    monkeypatch.setattr(ingest, "_ingest_article", explode)
    assert ingest.gather_research(_job(), tmp_path) == []


# ------------------------------------------------- one failure is one item

def test_a_private_video_costs_that_item_and_not_the_short(tmp_path, monkeypatch):
    calls: list[str] = []

    def download(url, work):
        calls.append(url)
        if "privado" in url:
            raise RuntimeError("Private video. Sign in if you've been granted access")
        return work / "v.mp4", {"title": url}

    monkeypatch.setattr(ingest, "download_video", download)
    monkeypatch.setattr(ingest, "_read_subtitles", lambda work: "fala transcrita")

    items = ingest.gather_research(
        _job(research="https://youtu.be/privado\nhttps://youtu.be/ok"), tmp_path)

    assert len(calls) == 2, "the second link was still tried"
    assert [i["status"] for i in items] == ["failed", "ok"]
    assert "Private video" in items[0]["error"]


def test_a_failed_item_never_reaches_the_prompt(tmp_path):
    material = _material(research=[
        {"kind": "video", "title": "privado", "url": "", "text": "",
         "status": "failed", "error": "Private video"},
        {"kind": "artigo", "title": "Crítica", "url": "", "text": "o que vale",
         "status": "ok", "error": ""},
    ])
    context = material.context()
    assert "o que vale" in context
    assert "Private video" not in context


def test_the_count_read_is_reported(tmp_path, monkeypatch):
    """"three of four read" is the difference between a short written from
    less and a short written from less without anyone knowing."""
    monkeypatch.setattr(ingest, "_ingest_article",
                        lambda url: SourceMaterial(kind="artigo", text="t"))
    said: list[str] = []

    ingest.gather_research(_job(research="https://a.test/1 https://a.test/2"),
                           tmp_path, lambda m, level="info": said.append(m))
    assert any("2 of 2" in m for m in said)


# ---------------------------------------------------------------- the caps

def test_only_so_many_items_are_read(tmp_path, monkeypatch):
    """Each one is a download plus a transcription: this is minutes of
    someone's time, not a list length."""
    monkeypatch.setattr(ingest, "_ingest_article",
                        lambda url: SourceMaterial(kind="artigo", text="t"))
    links = "\n".join(f"https://a.test/{n}" for n in range(9))
    said: list[str] = []

    items = ingest.gather_research(_job(research=links), tmp_path,
                                   lambda m, level="info": said.append(m))
    assert len(items) == ingest.MAX_RESEARCH
    assert any("ignored" in m for m in said), "the cut is said out loud"


def test_one_long_item_cannot_eat_the_whole_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "_ingest_article", lambda url: SourceMaterial(
        kind="artigo", text="palavra " * 40000))

    items = ingest.gather_research(_job(research="https://a.test/1"), tmp_path)
    assert len(items[0]["text"]) <= ingest.RESEARCH_CHARS


def test_four_reviews_are_four_voices_not_one_and_three_stubs():
    """Splitting the budget evenly is what keeps the fourth reviewer audible."""
    material = _material(research=[
        {"kind": "video", "title": f"Review {n}", "url": "", "status": "ok",
         "error": "", "text": f"opiniao {n} " * 3000}
        for n in range(4)])

    text = material.research_text(limit=8000)
    for n in range(4):
        assert f"opiniao {n}" in text


# -------------------------------------------------- telling the two apart

def test_the_prompt_says_which_one_is_on_screen(tmp_path):
    """Handed four transcripts with no labels, a model narrates the reviewer's
    opinion in the first person and describes shots the viewer will never
    see."""
    material = _material(research=[
        {"kind": "video", "title": "Review", "url": "", "text": "achei fraco",
         "status": "ok", "error": ""}])

    rules = script_mod._research_rules(material)  # noqa: SLF001
    assert "MATERIAL DE APOIO" in material.context()
    assert "NÃO é o que aparece na tela" in material.context()
    assert "não descreva" in rules.lower() or "Não descreva" in rules
    assert "opinião" in rules
    assert "instruções que o usuário escreveu mandam" in rules


def test_no_background_material_adds_no_rules(tmp_path):
    """A prompt that explains how to use material nobody supplied is noise."""
    assert script_mod._research_rules(_material()) == ""  # noqa: SLF001
    assert script_mod._research_rules(_material(research=[  # noqa: SLF001
        {"kind": "video", "status": "failed", "text": "", "title": "", "url": "",
         "error": "x"}])) == ""


def test_the_source_still_comes_first(tmp_path):
    """The background is background: the thing the short is about keeps the
    front of the prompt and most of the budget."""
    material = _material(transcript="ESTA É A FONTE", research=[
        {"kind": "artigo", "title": "apoio", "url": "", "text": "isto é apoio",
         "status": "ok", "error": ""}])

    context = material.context()
    assert context.index("ESTA É A FONTE") < context.index("isto é apoio")


# ----------------------------------------------------------- end to end

def test_ingest_carries_the_background_along_with_the_source(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "_ingest_source", lambda job, d, log: _material())
    monkeypatch.setattr(ingest, "_ingest_article", lambda url: SourceMaterial(
        kind="artigo", title="Crítica", text="o filme divide opiniões"))

    material = ingest.ingest(_job(research="https://revista.test/x"), tmp_path)

    assert material.kind == "video", "the source is untouched"
    assert len(material.research) == 1
    assert "divide opiniões" in material.context()
