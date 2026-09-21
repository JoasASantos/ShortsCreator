"""Clonar a FORMA de um vídeo, e fazer outros com ela.

O que um vídeo que funcionou ensina não é o assunto — esse é dele. É a forma:
o tamanho do gancho, quantas palavras cabem antes do primeiro corte, onde a
virada acontece, quanto sobra para o fecho.

O que estes testes seguram:

  * a forma é medida da fala E da imagem — texto igual com corte a cada
    segundo é outro vídeo;
  * a batida é ancorada em PALAVRAS, porque um roteiro novo nunca tem o mesmo
    tamanho e uma batida em segundos obriga a encher ou cortar na marra;
  * o conteúdo da referência não vaza para o roteiro novo. O molde carrega
    números e papéis, nunca o que foi dito.
"""
from __future__ import annotations

import pytest

from app import db
from app.pipeline import molde


def _words(spec: list[tuple[str, float, float]]) -> list[dict]:
    return [{"word": w, "start": s, "end": e} for w, s, e in spec]


def _spoken(count: int, start: float, each: float = 0.3,
            label: str = "p") -> list[tuple[str, float, float]]:
    return [(f"{label}{n}", round(start + n * each, 2),
             round(start + n * each + each * 0.9, 2)) for n in range(count)]


def _template(beats: list[tuple[str, float, int, int]]) -> molde.Template:
    made = [molde.Beat(kind=k, seconds=s, words=w, cuts=c, starts_at=0.0)
            for k, s, w, c in beats]
    return molde.Template(name="ref", seconds=sum(b.seconds for b in made),
                          words=sum(b.words for b in made), beats=made,
                          cuts=sum(b.cuts for b in made), language="pt")


# ------------------------------------------------------- medindo a forma

def test_a_forma_vem_da_fala_e_da_imagem(monkeypatch, tmp_path):
    """Um monólogo de plano fixo e um vídeo com corte a cada segundo podem ter
    exatamente o mesmo texto: medir só a fala descreve metade do vídeo."""
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"x")
    words = _words(_spoken(6, 0.0) + _spoken(8, 3.0, label="q"))

    monkeypatch.setattr(molde.reels, "transcribe_words",
                        lambda v, log: (words, "pt"))
    monkeypatch.setattr(molde.highlights, "probe_duration", lambda v: 6.0)
    monkeypatch.setattr(molde.highlights, "detect_scene_cuts",
                        lambda v, **k: [1.2, 3.4, 4.9])

    template = molde.analyze(video, name="ref")
    assert template.words == 14
    assert template.cuts == 3
    assert template.seconds == 6.0
    assert template.pace > 0
    assert template.cuts_per_minute == 30.0


def test_a_batida_corta_onde_a_pessoa_respirou(monkeypatch, tmp_path):
    """Pontuação não serve: a transcrição raramente traz vírgula onde a pessoa
    de fato parou."""
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"x")
    # duas rajadas com 1.5s de silêncio entre elas
    words = _words(_spoken(5, 0.0) + _spoken(5, 3.0, label="q"))
    monkeypatch.setattr(molde.reels, "transcribe_words",
                        lambda v, log: (words, "pt"))
    monkeypatch.setattr(molde.highlights, "probe_duration", lambda v: 5.0)
    monkeypatch.setattr(molde.highlights, "detect_scene_cuts", lambda v, **k: [])

    template = molde.analyze(video)
    assert len(template.beats) == 2
    assert template.beats[0].words == 5 and template.beats[1].words == 5


def test_os_papeis_vem_da_posicao_nao_do_texto(monkeypatch, tmp_path):
    """O molde não lê o que foi dito — é assim que ele não carrega o assunto
    de ninguém."""
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"x")
    # quatro batidas; a terceira é a mais rápida (mais palavras no mesmo tempo)
    words = _words(_spoken(4, 0.0, 0.30)
                   + _spoken(4, 3.0, 0.30, "b")
                   + _spoken(9, 6.0, 0.12, "c")
                   + _spoken(4, 9.0, 0.30, "d"))
    monkeypatch.setattr(molde.reels, "transcribe_words",
                        lambda v, log: (words, "pt"))
    monkeypatch.setattr(molde.highlights, "probe_duration", lambda v: 11.0)
    monkeypatch.setattr(molde.highlights, "detect_scene_cuts", lambda v, **k: [])

    kinds = [beat.kind for beat in molde.analyze(video).beats]
    assert kinds[0] == "gancho"
    assert kinds[-1] == "fecho"
    assert "virada" in kinds, "a batida mais densa do miolo é a virada"


def test_os_cortes_sao_contados_dentro_da_batida_certa(monkeypatch, tmp_path):
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"x")
    words = _words(_spoken(4, 0.0) + _spoken(4, 4.0, label="q"))
    monkeypatch.setattr(molde.reels, "transcribe_words",
                        lambda v, log: (words, "pt"))
    monkeypatch.setattr(molde.highlights, "probe_duration", lambda v: 6.0)
    monkeypatch.setattr(molde.highlights, "detect_scene_cuts",
                        lambda v, **k: [0.5, 0.9, 4.5])

    beats = molde.analyze(video).beats
    assert beats[0].cuts == 2 and beats[1].cuts == 1


def test_um_video_longo_nao_vira_cem_batidas(monkeypatch, tmp_path):
    """Cem batidas não ajudam a escrever um short."""
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"x")
    spec: list[tuple[str, float, float]] = []
    for n in range(60):
        spec += _spoken(3, n * 2.0, 0.3, f"g{n}")
    monkeypatch.setattr(molde.reels, "transcribe_words",
                        lambda v, log: (_words(spec), "pt"))
    monkeypatch.setattr(molde.highlights, "probe_duration", lambda v: 120.0)
    monkeypatch.setattr(molde.highlights, "detect_scene_cuts", lambda v, **k: [])

    template = molde.analyze(video)
    assert len(template.beats) == molde.MAX_BEATS
    assert template.words == 180, "nenhuma palavra se perde ao juntar batidas"


def test_um_video_sem_fala_e_recusado_com_o_motivo(monkeypatch, tmp_path):
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(molde.reels, "transcribe_words", lambda v, log: ([], ""))

    with pytest.raises(ValueError, match="fala"):
        molde.analyze(video)


# ------------------------------------------------ escrevendo na mesma forma

def test_o_roteiro_novo_tem_uma_batida_por_batida_da_forma(monkeypatch):
    template = _template([("gancho", 2.0, 8, 1), ("corpo", 6.0, 18, 2),
                          ("fecho", 3.0, 9, 0)])
    monkeypatch.setattr(molde.llm, "complete_json", lambda *a, **k: {
        "title": "Novo", "description": "d", "hashtags": ["x"],
        "beats": [{"i": 0, "text": "abre", "broll_query": "server room"},
                  {"i": 1, "text": "desenvolve", "broll_query": "hands typing"},
                  {"i": 2, "text": "fecha", "broll_query": "city at night"}]})

    script = molde.write_variant(template, "golpes por PIX")
    assert [s.kind for s in script.segments] == ["hook", "corpo", "cta"]
    assert script.estimated_seconds == 11


def test_a_forma_no_prompt_e_so_numero_e_papel():
    """O prompt de escrita não pode carregar o conteúdo da referência: é a
    diferença entre clonar a forma e copiar o vídeo."""
    template = _template([("gancho", 2.0, 8, 1), ("corpo", 6.0, 18, 2)])
    template.reference_text = "o segredo que ninguém te conta sobre investir"

    brief = molde.shape_brief(template)
    assert "gancho" in brief and "8 palavras" in brief
    assert "segredo" not in brief, "o texto da referência vazou para a forma"
    assert "investir" not in brief


def test_o_texto_da_referencia_fica_guardado_para_auditoria(monkeypatch, tmp_path):
    """Guardado para você conferir o que foi medido — e fora do prompt."""
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(molde.reels, "transcribe_words", lambda v, log: (
        _words([("segredo", 0.0, 0.5), ("guardado", 0.5, 1.0)]), "pt"))
    monkeypatch.setattr(molde.highlights, "probe_duration", lambda v: 1.0)
    monkeypatch.setattr(molde.highlights, "detect_scene_cuts", lambda v, **k: [])

    template = molde.analyze(video)
    assert "segredo" in template.reference_text
    assert "segredo" not in molde.shape_brief(template)


def test_um_assunto_vazio_e_recusado():
    with pytest.raises(ValueError, match="sobre o que"):
        molde.write_variant(_template([("gancho", 2.0, 8, 0)]), "   ")


def test_uma_batida_faltando_na_resposta_nao_derruba_o_roteiro(monkeypatch):
    """O modelo pulou a batida 1: o que veio ainda é um roteiro."""
    template = _template([("gancho", 2.0, 8, 0), ("corpo", 5.0, 15, 0),
                          ("fecho", 2.0, 7, 0)])
    monkeypatch.setattr(molde.llm, "complete_json", lambda *a, **k: {
        "title": "t", "description": "", "hashtags": [],
        "beats": [{"i": 0, "text": "abre", "broll_query": "a"},
                  {"i": 2, "text": "fecha", "broll_query": "b"}]})

    script = molde.write_variant(template, "assunto")
    assert [s.kind for s in script.segments] == ["hook", "cta"]


def test_nenhuma_batida_de_volta_e_erro(monkeypatch):
    template = _template([("gancho", 2.0, 8, 0)])
    monkeypatch.setattr(molde.llm, "complete_json",
                        lambda *a, **k: {"title": "t", "beats": []})
    with pytest.raises(RuntimeError, match="nenhuma batida"):
        molde.write_variant(template, "assunto")


def test_o_roteiro_entra_pelo_caminho_normal():
    """Sem pipeline paralelo: vira texto de roteiro, que o job já aceita."""
    template = _template([("gancho", 2.0, 8, 0), ("corpo", 5.0, 15, 0)])
    script = molde.ShortScript(
        title="t", description="", hashtags=[],
        segments=[molde.ScriptSegment(kind="hook", text="primeira"),
                  molde.ScriptSegment(kind="corpo", text="segunda")],
        estimated_seconds=7)
    assert molde.as_script_text(script) == "primeira\n\nsegunda"


# ------------------------------------------------------------- as rotas

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def test_analisar_sem_fonte_nenhuma_e_400(client):
    answer = client.post("/api/moldes/analisar", json={})
    assert answer.status_code == 400
    assert "link" in answer.json()["detail"]


def test_um_molde_guardado_pode_ser_lido_e_apagado(client):
    template = _template([("gancho", 2.0, 8, 1), ("fecho", 3.0, 9, 0)])
    molde_id = db.create_molde("ref", "https://x", template.seconds,
                               template.words, template.as_dict())

    listed = client.get("/api/moldes").json()["moldes"]
    assert any(item["id"] == molde_id for item in listed)

    full = client.get(f"/api/moldes/{molde_id}").json()
    assert len(full["beats"]) == 2 and full["id"] == molde_id

    assert client.delete(f"/api/moldes/{molde_id}").status_code == 200
    assert client.get(f"/api/moldes/{molde_id}").status_code == 404


def test_variantes_escrevem_um_roteiro_por_assunto(client, monkeypatch):
    template = _template([("gancho", 2.0, 8, 1), ("fecho", 3.0, 9, 0)])
    molde_id = db.create_molde("ref", "", template.seconds, template.words,
                               template.as_dict())
    monkeypatch.setattr(molde.llm, "complete_json", lambda *a, **k: {
        "title": "t", "description": "", "hashtags": [],
        "beats": [{"i": 0, "text": "abre", "broll_query": "a"},
                  {"i": 1, "text": "fecha", "broll_query": "b"}]})

    answer = client.post(f"/api/moldes/{molde_id}/variantes",
                         json={"subjects": ["golpes por PIX", "senhas fracas"],
                               "dry_run": True})
    body = answer.json()
    assert answer.status_code == 200
    assert [v["subject"] for v in body["variants"]] == ["golpes por PIX",
                                                        "senhas fracas"]
    assert all(v["job_id"] == "" for v in body["variants"]), "dry run não renderiza"


def test_um_assunto_que_falha_nao_leva_os_outros(client, monkeypatch):
    template = _template([("gancho", 2.0, 8, 0)])
    molde_id = db.create_molde("ref", "", template.seconds, template.words,
                               template.as_dict())
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("sem cota")
        return {"title": "t", "description": "", "hashtags": [],
                "beats": [{"i": 0, "text": "abre", "broll_query": "a"}]}

    monkeypatch.setattr(molde.llm, "complete_json", flaky)
    body = client.post(f"/api/moldes/{molde_id}/variantes",
                       json={"subjects": ["um", "dois"], "dry_run": True}).json()

    assert len(body["variants"]) == 1 and len(body["failed"]) == 1
    assert "sem cota" in body["failed"][0]["error"]


def test_todos_falhando_e_um_erro_que_diz_por_que(client, monkeypatch):
    template = _template([("gancho", 2.0, 8, 0)])
    molde_id = db.create_molde("ref", "", template.seconds, template.words,
                               template.as_dict())

    def refuse(*a, **k):
        raise RuntimeError("sem cota em lugar nenhum")

    monkeypatch.setattr(molde.llm, "complete_json", refuse)
    answer = client.post(f"/api/moldes/{molde_id}/variantes",
                         json={"subjects": ["um"], "dry_run": True})
    assert answer.status_code == 502
    assert "sem cota" in answer.json()["detail"]


def test_o_teto_de_variantes_e_respeitado(client, monkeypatch):
    template = _template([("gancho", 2.0, 8, 0)])
    molde_id = db.create_molde("ref", "", template.seconds, template.words,
                               template.as_dict())
    monkeypatch.setattr(molde.llm, "complete_json", lambda *a, **k: {
        "title": "t", "description": "", "hashtags": [],
        "beats": [{"i": 0, "text": "abre", "broll_query": "a"}]})

    body = client.post(f"/api/moldes/{molde_id}/variantes",
                       json={"subjects": [f"assunto {n}" for n in range(20)],
                             "dry_run": True}).json()
    assert len(body["variants"]) <= 6
