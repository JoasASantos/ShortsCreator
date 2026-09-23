"""Personagens conversando, e a conversa contando a história.

O formato: dois ou três personagens na tela, cada um com a sua voz e a sua
cara, revezando falas curtas por cima de footage que segura o dedo.

O que estes testes seguram é o que faz o formato ser o formato:

  * cada fala é sintetizada com a voz DO SEU DONO — uma voz só é narração com
    passos extras;
  * o personagem aparece enquanto fala, e do lado que é dele do começo ao fim:
    trocar de lado no meio faz o espectador perder quem é quem;
  * um nome que o modelo inventou não custa a fala;
  * sai uma Timeline igual à de todo mundo, então editor, QA e publicação
    continuam funcionando.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import db
from app.pipeline import dialogo, elenco


@pytest.fixture()
def cast():
    """Dois personagens com vozes de verdade do banco."""
    voices = db.list_voices()
    if len(voices) < 2:
        first = db.create_voice(name="Voz A", provider="edge",
                                provider_voice_id="pt-BR-AntonioNeural")
        second = db.create_voice(name="Voz B", provider="edge",
                                 provider_voice_id="pt-BR-FranciscaNeural")
        voices = [db.get_voice(first), db.get_voice(second)]
    peter = elenco.create("Peter", voices[0]["id"], side="esquerda",
                          note="pergunta as coisas")
    stewie = elenco.create("Stewie", voices[1]["id"], side="direita",
                           note="explica com desdém")
    yield [peter, stewie]
    for person in (peter, stewie):
        elenco.remove(person.id)


class _Job:
    duration = 45
    instruction = ""
    language = "pt-BR"
    source = "a teoria de que Saturno é um símbolo"
    caption_style = "karaoke"
    caption_position = "centro"
    watermark = ""
    watermark_position = "baixo_centro"
    watermark_size = "medio"
    watermark_opacity = 0.6
    background_fill = "preencher"
    niche = "curiosidades"
    fundo = ""
    edit_mode = "dialogo"
    cast: list[str] = []


def _answer(lines):
    return {"title": "t", "description": "d", "hashtags": ["x"], "lines": lines}


# ------------------------------------------------------- escrevendo a conversa

def test_cada_fala_fica_com_o_personagem_que_a_disse(cast, monkeypatch):
    monkeypatch.setattr(dialogo.llm, "complete_json", lambda *a, **k: _answer([
        {"speaker": "Peter", "text": "por que Saturno?"},
        {"speaker": "Stewie", "text": "porque marcava o tempo"},
    ]))
    conversa = dialogo.write("Saturno", cast)
    assert [line.name for line in conversa.lines] == ["Peter", "Stewie"]


def test_o_nome_e_casado_sem_exigir_igualdade_exata(cast, monkeypatch):
    """O modelo escreve "peter" e o elenco tem "Peter" — insistir em igualdade
    exata transformaria isso num erro que ninguém entende."""
    monkeypatch.setattr(dialogo.llm, "complete_json", lambda *a, **k: _answer([
        {"speaker": "peter", "text": "oi"},
        {"speaker": "STEWIE", "text": "oi"},
    ]))
    conversa = dialogo.write("x", cast)
    assert [line.name for line in conversa.lines] == ["Peter", "Stewie"]


def test_um_nome_inventado_nao_custa_a_fala(cast, monkeypatch):
    """Perder uma fala boa por causa de um nome é pior do que reatribuí-la."""
    monkeypatch.setattr(dialogo.llm, "complete_json", lambda *a, **k: _answer([
        {"speaker": "Brian", "text": "uma fala boa"},
        {"speaker": "Stewie", "text": "outra"},
    ]))
    said: list[tuple[str, str]] = []
    conversa = dialogo.write("x", cast,
                             log=lambda m, level="info": said.append((level, m)))

    assert len(conversa.lines) == 2
    assert conversa.lines[0].name == "Peter", "foi para o primeiro do elenco"
    assert any(level == "warn" and "Brian" in m for level, m in said)


def test_uma_conversa_de_um_personagem_so_e_avisada(cast, monkeypatch):
    """Isso é narração com passos extras, e quem pediu conversa merece saber."""
    monkeypatch.setattr(dialogo.llm, "complete_json", lambda *a, **k: _answer([
        {"speaker": "Peter", "text": "a"}, {"speaker": "Peter", "text": "b"}]))
    said: list[tuple[str, str]] = []
    dialogo.write("x", cast, log=lambda m, level="info": said.append((level, m)))
    assert any(level == "warn" and "narração" in m for level, m in said)


def test_menos_de_dois_personagens_e_recusado_com_o_caminho(cast):
    with pytest.raises(ValueError, match="Elenco"):
        dialogo.write("x", cast[:1])


def test_um_assunto_vazio_e_recusado(cast):
    with pytest.raises(ValueError, match="sobre o que"):
        dialogo.write("   ", cast)


def test_nenhuma_fala_de_volta_e_erro(cast, monkeypatch):
    monkeypatch.setattr(dialogo.llm, "complete_json", lambda *a, **k: _answer([]))
    with pytest.raises(RuntimeError, match="nenhuma fala"):
        dialogo.write("x", cast)


def test_o_elenco_vai_no_prompt_e_o_alvo_de_falas_tambem(cast, monkeypatch):
    seen: dict = {}

    def capture(system, prompt, schema=None, max_tokens=8000, purpose=""):
        seen["system"], seen["prompt"] = system, prompt
        return _answer([{"speaker": "Peter", "text": "a"},
                        {"speaker": "Stewie", "text": "b"}])

    monkeypatch.setattr(dialogo.llm, "complete_json", capture)
    dialogo.write("Saturno", cast, seconds=60)

    assert "Peter" in seen["prompt"] and "Stewie" in seen["prompt"]
    assert "pergunta as coisas" in seen["prompt"], "a nota do personagem guia o papel"
    assert "60s" in seen["prompt"]
    assert "português" in seen["system"]


# ------------------------------------------------------------- dando voz

def test_cada_fala_usa_a_voz_do_seu_dono(cast, monkeypatch, tmp_path):
    """Uma voz só para todos é narração com passos extras."""
    used: list[str] = []

    def fake_tts(text, out, voice, log, language=""):
        used.append(voice["name"] if voice else "(sistema)")
        out.write_bytes(b"audio")
        return dialogo.tts.Narration(out, 1.5, [])

    monkeypatch.setattr(dialogo.tts, "synthesize", fake_tts)
    conversa = dialogo.Conversa(lines=[
        dialogo.Fala(person_id=cast[0].id, name="Peter", text="a"),
        dialogo.Fala(person_id=cast[1].id, name="Stewie", text="b"),
    ])
    dialogo.speak(conversa, tmp_path, {p.id: p for p in cast}, "pt-BR")

    assert len(set(used)) == 2, f"a mesma voz para os dois: {used}"


def test_as_falas_ficam_em_sequencia_com_respiro(cast, monkeypatch, tmp_path):
    """Sem o respiro a conversa atropela e soa como uma pessoa lendo dois
    papéis."""
    monkeypatch.setattr(dialogo.tts, "synthesize",
                        lambda text, out, voice, log, language="":
                        _fake_audio(out, 2.0))
    conversa = dialogo.Conversa(lines=[
        dialogo.Fala(person_id=cast[0].id, name="Peter", text="a"),
        dialogo.Fala(person_id=cast[1].id, name="Stewie", text="b"),
    ])
    dialogo.speak(conversa, tmp_path, {p.id: p for p in cast}, "pt-BR")

    assert conversa.lines[0].start == 0.0
    assert conversa.lines[1].start == pytest.approx(2.0 + dialogo.GAP, abs=0.01)
    assert conversa.duration == pytest.approx(4.0 + dialogo.GAP, abs=0.01)


def _fake_audio(out: Path, seconds: float):
    out.write_bytes(b"audio")
    return dialogo.tts.Narration(out, seconds, [])


# ------------------------------------------------------------ a montagem

def _spoken(cast, tmp_path) -> dialogo.Conversa:
    falas = []
    for index, (person, text) in enumerate([(cast[0], "por que Saturno"),
                                            (cast[1], "porque marcava o tempo")]):
        audio = tmp_path / "falas" / f"fala_{index:03d}.mp3"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"a")
        falas.append(dialogo.Fala(person_id=person.id, name=person.name,
                                  text=text, audio=audio,
                                  start=index * 2.2, seconds=2.0))
    return dialogo.Conversa(lines=falas, title="t")


def test_o_personagem_aparece_enquanto_fala(cast, tmp_path):
    for person in cast:
        picture = tmp_path / f"{person.id}.png"
        picture.write_bytes(b"png")
        person.image = str(picture)

    background = tmp_path / "background.mp4"
    background.write_bytes(b"bg")
    timeline = dialogo.build_timeline(tmp_path, _spoken(cast, tmp_path),
                                      {p.id: p for p in cast}, background, _Job())

    assert len(timeline.media) == 2
    first, second = timeline.media
    assert first.start == 0.0 and first.end > 2.0, "some depois da última sílaba"
    assert second.start == pytest.approx(2.2, abs=0.01)


def test_cada_um_fica_do_seu_lado(cast, tmp_path):
    """Trocar de lado no meio faz o espectador perder quem é quem."""
    for person in cast:
        picture = tmp_path / f"{person.id}.png"
        picture.write_bytes(b"png")
        person.image = str(picture)

    background = tmp_path / "background.mp4"
    background.write_bytes(b"bg")
    timeline = dialogo.build_timeline(tmp_path, _spoken(cast, tmp_path),
                                      {p.id: p for p in cast}, background, _Job())

    assert timeline.media[0].x < 0.5, "esquerda"
    assert timeline.media[1].x > 0.5, "direita"


def test_um_personagem_sem_imagem_ainda_fala(cast, tmp_path):
    """Voz sem cara é um personagem válido — off-screen é um recurso, não um
    erro."""
    background = tmp_path / "background.mp4"
    background.write_bytes(b"bg")
    timeline = dialogo.build_timeline(tmp_path, _spoken(cast, tmp_path),
                                      {p.id: p for p in cast}, background, _Job())

    assert timeline.media == []
    assert len([a for a in timeline.audio if a.role == "narration"]) == 2


def test_a_legenda_e_a_fala_dentro_do_tempo_dela(cast, tmp_path):
    background = tmp_path / "background.mp4"
    background.write_bytes(b"bg")
    conversa = _spoken(cast, tmp_path)
    timeline = dialogo.build_timeline(tmp_path, conversa,
                                      {p.id: p for p in cast}, background, _Job())

    assert timeline.captions
    assert timeline.captions[0].start >= 0.0
    assert timeline.captions[-1].end <= conversa.duration + 0.1


def test_o_fundo_cobre_a_conversa_inteira_e_fica_mudo(cast, tmp_path):
    background = tmp_path / "background.mp4"
    background.write_bytes(b"bg")
    conversa = _spoken(cast, tmp_path)
    timeline = dialogo.build_timeline(tmp_path, conversa,
                                      {p.id: p for p in cast}, background, _Job())

    assert len(timeline.video) == 1
    assert timeline.video[0].mute is True, "o áudio é das falas"
    assert timeline.video[0].duration == pytest.approx(conversa.duration, abs=0.01)


# --------------------------------------------------------------- o elenco

def test_um_personagem_precisa_de_uma_voz_registrada():
    with pytest.raises(ValueError, match="voz"):
        elenco.create("Sem voz", "voice_inexistente")


def test_um_personagem_precisa_de_nome():
    voices = db.list_voices()
    with pytest.raises(ValueError, match="nome"):
        elenco.create("   ", voices[0]["id"] if voices else "x")


def test_a_geometria_vem_do_lado(cast):
    assert cast[0].geometry()["x"] < cast[1].geometry()["x"]
    assert all(0 < p.geometry()["width"] < 1 for p in cast)


# ----------------------------------------------------------------- rotas

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def test_a_rota_lista_personagens_e_lados(client, cast):
    body = client.get("/api/elenco").json()
    names = {p["name"] for p in body["personagens"]}
    assert {"Peter", "Stewie"} <= names
    assert "esquerda" in body["lados"] and "direita" in body["lados"]
    assert all(p["voice_name"] for p in body["personagens"]), "a voz é nomeada"


def test_criar_sem_voz_valida_e_400(client):
    answer = client.post("/api/elenco",
                         data={"name": "X", "voice_id": "voice_nao_existe"})
    assert answer.status_code == 400
    assert "voz" in answer.json()["detail"]


def test_um_personagem_sem_imagem_responde_404_na_imagem(client, cast):
    assert client.get(f"/api/elenco/{cast[0].id}/imagem").status_code == 404
