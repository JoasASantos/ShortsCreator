"""Reciclar: achar o que já funcionou num perfil, e dublar para outro idioma.

Two features that meet at one workflow. What is worth guarding:

  * a handle is parsed from whatever someone pastes, and a pasted link beats
    the dropdown — they meant the link;
  * a listing is metadata only, because forty downloads to choose one is the
    whole cost of the feature;
  * a dub is placed line by line on the original timing. A single track for
    the whole video drifts seconds away from the mouth by the third minute,
    and that is the failure the feature exists to avoid.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.pipeline import dub, recycle


@pytest.fixture(autouse=True)
def _no_ambient_cookies(monkeypatch):
    monkeypatch.setattr(settings, "ytdlp_cookies", "")
    monkeypatch.setattr(settings, "ytdlp_cookies_browser", "")


class _Proc:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


# ------------------------------------------------------- reading a handle

@pytest.mark.parametrize("pasted,chosen,expected", [
    ("@alguem", "", ("alguem", "tiktok")),
    ("alguem", "instagram", ("alguem", "instagram")),
    ("  @Alguem.Oficial ", "youtube", ("Alguem.Oficial", "youtube")),
    ("https://www.tiktok.com/@viral", "", ("viral", "tiktok")),
    ("https://www.instagram.com/fulano/", "", ("fulano", "instagram")),
])
def test_a_handle_is_read_from_whatever_was_pasted(pasted, chosen, expected):
    assert recycle.parse_target(pasted, chosen) == expected


def test_a_pasted_link_beats_the_dropdown():
    """Someone who pasted a TikTok link while the selector said Instagram
    meant the link."""
    assert recycle.parse_target("https://www.tiktok.com/@viral",
                                "instagram") == ("viral", "tiktok")


def test_an_empty_field_says_what_to_type():
    with pytest.raises(recycle.ProfileUnavailable, match="@alguem"):
        recycle.parse_target("   ")


def test_something_that_is_not_a_handle_is_refused():
    with pytest.raises(recycle.ProfileUnavailable):
        recycle.parse_target("dois nomes com espaço")


# ------------------------------------------------------ listing a profile

def _listing(monkeypatch, entries, code=0, err=""):
    seen: dict = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        if code != 0:
            return _Proc(code, "", err)
        return _Proc(0, json.dumps({"uploader": "Alguém", "entries": entries}))

    monkeypatch.setattr(recycle.subprocess, "run", run)
    return seen


def test_the_listing_is_metadata_only(monkeypatch):
    """Downloading forty videos to choose one is the entire cost of the
    feature; the view count is in the listing."""
    seen = _listing(monkeypatch, [])
    recycle.scan("@alguem")
    assert "--flat-playlist" in seen["cmd"]
    assert not any(flag.startswith("-o") for flag in seen["cmd"]), "nothing is written"


def test_the_most_watched_come_first(monkeypatch):
    _listing(monkeypatch, [
        {"id": "a", "url": "https://t/1", "title": "médio", "view_count": 5000},
        {"id": "b", "url": "https://t/2", "title": "viral", "view_count": 900000},
        {"id": "c", "url": "https://t/3", "title": "fraco", "view_count": 12},
    ])
    found = recycle.scan("@alguem")
    assert [item["title"] for item in found["items"]] == ["viral", "médio", "fraco"]
    assert found["items"][0]["views"] == 900000


def test_an_unknown_view_count_sinks_rather_than_leading(monkeypatch):
    """"unknown" is not "zero", but it is not evidence of anything either."""
    _listing(monkeypatch, [
        {"id": "a", "url": "https://t/1", "title": "sem contagem"},
        {"id": "b", "url": "https://t/2", "title": "com contagem", "view_count": 10},
    ])
    found = recycle.scan("@alguem")
    assert [item["title"] for item in found["items"]] == ["com contagem",
                                                          "sem contagem"]
    assert found["note"], "the gap in the data is said out loud"


def test_the_limit_is_respected_and_capped(monkeypatch):
    seen = _listing(monkeypatch, [])
    recycle.scan("@alguem", limit=999)
    assert seen["cmd"][seen["cmd"].index("--playlist-end") + 1] == str(recycle.MAX_ITEMS)


def test_entries_without_a_url_are_dropped(monkeypatch):
    _listing(monkeypatch, [{"id": "a", "title": "sem url"},
                           {"id": "b", "url": "https://t/2", "title": "ok"}])
    assert [i["title"] for i in recycle.scan("@x")["items"]] == ["ok"]


def test_cookies_are_passed_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "ytdlp_cookies", "/tmp/cookies.txt")
    seen = _listing(monkeypatch, [])
    recycle.scan("@alguem", "instagram")
    assert "--cookies" in seen["cmd"]
    assert "/tmp/cookies.txt" in seen["cmd"]


# ------------------------------------------------- why a listing failed

def test_a_login_wall_says_how_to_get_past_it(monkeypatch):
    _listing(monkeypatch, [], code=1,
             err="ERROR: [Instagram] Requested content is not available, "
                 "rate-limit reached or login required")
    with pytest.raises(recycle.ProfileUnavailable) as exc:
        recycle.scan("@alguem", "instagram")
    assert "YTDLP_COOKIES" in str(exc.value)


def test_a_private_account_is_said_plainly(monkeypatch):
    _listing(monkeypatch, [], code=1, err="ERROR: This account is private")
    with pytest.raises(recycle.ProfileUnavailable, match="private"):
        recycle.scan("@alguem")


def test_a_handle_that_does_not_exist_is_told_apart(monkeypatch):
    _listing(monkeypatch, [], code=1, err="ERROR: Unable to find profile: 404")
    with pytest.raises(recycle.ProfileUnavailable, match="does not exist"):
        recycle.scan("@alguem")


def test_a_platform_that_hangs_does_not_hang_the_screen(monkeypatch):
    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=180)

    monkeypatch.setattr(recycle.subprocess, "run", hang)
    with pytest.raises(recycle.ProfileUnavailable, match="too long"):
        recycle.scan("@alguem")


# --------------------------------------------------------- dubbing lines

def _words(pairs):
    return [{"word": w, "start": s, "end": e} for w, s, e in pairs]


def test_lines_are_split_where_the_speaker_paused():
    """A dub that respects the pauses lands on the mouth. Punctuation alone
    is not enough — whisper's word list often carries none."""
    words = _words([("Olá", 0.0, 0.4), ("mundo", 0.4, 0.9),
                    ("Hoje", 2.0, 2.3), ("eu", 2.3, 2.5), ("falo", 2.5, 2.9)])
    lines = dub._segments_from(words)  # noqa: SLF001
    assert len(lines) == 2
    assert lines[0]["text"] == "Olá mundo" and lines[1]["start"] == 2.0


def test_a_continuous_sentence_is_not_chopped():
    words = _words([(f"palavra{n}", n * 0.3, n * 0.3 + 0.28) for n in range(8)])
    assert len(dub._segments_from(words)) == 1  # noqa: SLF001


def test_a_line_longer_than_its_slot_is_sped_up_to_fit(monkeypatch, tmp_path):
    """The translation of a short English line is routinely longer in
    Portuguese; without fitting, line four starts over line five."""
    line = dub.Line(start=0.0, end=2.0, original="short", text="bem mais longo")
    sped: list[float] = []

    def fake_tts(text, out, voice, log, language=""):
        out.write_bytes(b"audio")
        return dub.tts.Narration(out, 2.4, [])

    monkeypatch.setattr(dub.tts, "synthesize", fake_tts)
    monkeypatch.setattr(dub, "_speed_up",
                        lambda src, tempo, dest: sped.append(tempo)
                        or dest.write_bytes(b"x") or dest)
    monkeypatch.setattr(dub.tts, "audio_duration", lambda p: 2.0)

    dub.synthesize([line], tmp_path, None, "pt-BR", lambda m, level="info": None)

    # 2.4s of speech in a 2.0s slot: 1.2x, comfortably under the cap
    assert sped and sped[0] == pytest.approx(1.2, abs=0.01)
    assert line.tempo == pytest.approx(1.2, abs=0.01)


def test_speeding_up_stops_before_it_becomes_a_disclaimer(monkeypatch, tmp_path):
    """Past a point a rushed line is unintelligible, and a late line is
    better than one nobody can follow."""
    line = dub.Line(start=0.0, end=1.0, original="hi", text="uma frase enorme")
    sped: list[float] = []

    def long_take(text, out, voice, log, language=""):
        out.write_bytes(b"a")
        return dub.tts.Narration(out, 9.0, [])

    monkeypatch.setattr(dub.tts, "synthesize", long_take)
    monkeypatch.setattr(dub, "_speed_up",
                        lambda src, tempo, dest: sped.append(tempo)
                        or dest.write_bytes(b"x") or dest)
    monkeypatch.setattr(dub.tts, "audio_duration", lambda p: 6.6)
    said: list[tuple[str, str]] = []

    dub.synthesize([line], tmp_path, None, "pt-BR",
                   lambda m, level="info": said.append((level, m)))

    assert sped[0] == dub.MAX_TEMPO
    assert any(level == "warn" and "run past" in m for level, m in said)


def test_a_line_that_already_fits_is_not_re_encoded(monkeypatch, tmp_path):
    line = dub.Line(start=0.0, end=3.0, original="x", text="cabe")
    def fits(text, out, voice, log, language=""):
        out.write_bytes(b"a")
        return dub.tts.Narration(out, 2.9, [])

    monkeypatch.setattr(dub.tts, "synthesize", fits)
    monkeypatch.setattr(dub, "_speed_up",
                        lambda *a: pytest.fail("a line that fits was re-encoded"))

    dub.synthesize([line], tmp_path, None, "pt-BR", lambda m, level="info": None)
    assert line.tempo == 1.0


# ------------------------------------------------------ the translation

def test_a_failed_batch_leaves_those_lines_in_the_original(monkeypatch):
    """A dub with three untranslated lines is a fixable video; a failed render
    is not."""
    lines = [dub.Line(start=0.0, end=1.0, original="hello"),
             dub.Line(start=1.0, end=2.0, original="world")]

    def refuse(*_a, **_k):
        raise RuntimeError("every model is out of quota")

    monkeypatch.setattr(dub.llm, "complete_json", refuse)
    said: list[str] = []
    dub.translate(lines, "pt-BR", lambda m, level="info": said.append(m))

    assert [line.text for line in lines] == ["hello", "world"]
    assert any("not translated" in m for m in said)


def test_each_line_keeps_its_own_slot(monkeypatch):
    """The pairing between line and timing is the whole dub: a translation
    that returns them out of order would put the punchline on the setup."""
    lines = [dub.Line(start=0.0, end=1.0, original="one"),
             dub.Line(start=1.0, end=2.0, original="two")]
    monkeypatch.setattr(dub.llm, "complete_json", lambda *a, **k: {
        "lines": [{"i": 1, "text": "dois"}, {"i": 0, "text": "um"}]})

    dub.translate(lines, "pt-BR", lambda m, level="info": None)
    assert [line.text for line in lines] == ["um", "dois"]


# ----------------------------------------------------------- the timeline

class _Job:
    keep_audio = True
    caption_style = "karaoke"
    caption_position = "centro"
    watermark = ""
    watermark_position = "baixo_centro"
    watermark_size = "medio"
    watermark_opacity = 0.6


def _dubbed(tmp_path) -> dub.Dubbed:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    audio = tmp_path / "dub" / "line_000.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"a")
    line = dub.Line(start=1.5, end=3.0, original="hello", text="olá",
                    audio=audio, seconds=1.4)
    return dub.Dubbed(source=source, duration=10.0, language="en",
                      lines=[line], words=dub.caption_words([line]))


def test_the_new_voice_sits_on_the_original_moment(tmp_path):
    timeline = dub.build_timeline(tmp_path, _dubbed(tmp_path), _Job())
    voice = next(a for a in timeline.audio if a.role == "narration")
    assert voice.start == 1.5, "the line lands where the original line was"


def test_the_original_stays_underneath_ducked(tmp_path):
    """The music, the laugh and the room are what make this a dub of something
    real instead of a stranger talking over a muted clip."""
    timeline = dub.build_timeline(tmp_path, _dubbed(tmp_path), _Job())
    original = next(a for a in timeline.audio if a.role == "original")
    assert original.gain == dub.ORIGINAL_GAIN
    assert 0 < original.gain < 0.3
    assert timeline.video[0].mute is True, "the picture's own track is not doubled"


def test_the_original_can_be_dropped_entirely(tmp_path):
    job = _Job()
    job.keep_audio = False
    timeline = dub.build_timeline(tmp_path, _dubbed(tmp_path), job)
    assert not [a for a in timeline.audio if a.role == "original"]
    assert [a.role for a in timeline.audio] == ["narration"]


def test_the_picture_is_the_original_untouched(tmp_path):
    timeline = dub.build_timeline(tmp_path, _dubbed(tmp_path), _Job())
    assert len(timeline.video) == 1
    assert timeline.video[0].source == "source.mp4"
    assert timeline.duration == 10.0


def test_captions_are_the_translation_not_the_original(tmp_path):
    timeline = dub.build_timeline(tmp_path, _dubbed(tmp_path), _Job())
    assert [c.text for c in timeline.captions] == ["olá"]


def test_caption_words_stay_inside_their_own_line():
    """Estimated within the line, never across the video: the error stays in
    tenths of a second instead of accumulating."""
    line = dub.Line(start=5.0, end=7.0, original="x",
                    text="uma frase com várias palavras", seconds=2.0)
    words = dub.caption_words([line])
    assert words[0]["start"] >= 5.0
    assert words[-1]["end"] <= 7.2
