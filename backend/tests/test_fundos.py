"""Footage sua por baixo da narração: gameplay, parkour, satisfying.

O formato mais visto de short narrado é uma história falada por cima de um
vídeo que não tem nada a ver com ela. A imagem não ilustra: existe para a mão
não subir a tela. Nenhum banco de stock tem parkour de Minecraft, então é
material que o usuário traz.

O que estes testes seguram é tudo sobre repetição, que é onde esse formato
quebra:

  * o arquivo é baixado UMA vez — duas horas de parkour a cada short é o custo
    que mataria a ideia;
  * cada short pega um trecho DIFERENTE, e o mesmo job re-renderizado pega o
    mesmo trecho;
  * o áudio do jogo nunca entra;
  * arquivo mais curto que a narração dá a volta em vez de acabar em preto.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.pipeline import fundos

from conftest import needs_ffmpeg


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path, monkeypatch):
    """Cada teste com seu próprio cache: o de verdade é do usuário."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)


def _clip(path: Path, seconds: float, colour: str = "green") -> Path:
    """Um vídeo de verdade — o corte, a volta e o silêncio são medidos nele."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=size=640x360:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(path)],
        check=True, capture_output=True)
    return path


def _has_audio(video: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", str(video)],
        check=True, capture_output=True, text=True)
    return bool(out.stdout.strip())


def _duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True)
    return float(out.stdout.strip())


def _size(video: Path) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=s=x:p=0", str(video)],
        check=True, capture_output=True, text=True)
    return out.stdout.strip()


# --------------------------------------------------------- baixar uma vez

def test_o_arquivo_e_baixado_uma_vez_e_reusado(monkeypatch, tmp_path):
    """Duas horas de parkour baixadas a cada short é o custo que mataria a
    ideia. Dez shorts do mesmo canal usam o mesmo arquivo."""
    downloads: list[str] = []

    def fake_download(url, home):
        downloads.append(url)
        path = home / "source.mp4"
        path.write_bytes(b"video")
        return path, {"title": "Parkour 2h"}

    monkeypatch.setattr(fundos.ingest, "download_video", fake_download)
    monkeypatch.setattr(fundos.render, "probe_duration", lambda p: 7200.0)

    first = fundos.fetch("https://youtu.be/parkour")
    second = fundos.fetch("https://youtu.be/parkour")

    assert downloads == ["https://youtu.be/parkour"], "baixou de novo"
    assert first.id == second.id and first.path == second.path
    assert first.name == "Parkour 2h" and first.seconds == 7200.0


def test_o_registro_sem_o_arquivo_baixa_de_novo(monkeypatch, tmp_path):
    """Disco limpo pela metade: o registro sobreviveu e o arquivo não. Falhar
    apontando para um caminho que não existe seria pior."""
    downloads: list[str] = []

    def fake_download(url, home):
        downloads.append(url)
        path = home / "source.mp4"
        path.write_bytes(b"video")
        return path, {"title": "Parkour"}

    monkeypatch.setattr(fundos.ingest, "download_video", fake_download)
    monkeypatch.setattr(fundos.render, "probe_duration", lambda p: 600.0)

    first = fundos.fetch("https://youtu.be/x")
    first.path.unlink()
    fundos.fetch("https://youtu.be/x")

    assert len(downloads) == 2


def test_um_link_invalido_e_recusado_antes_de_baixar(monkeypatch):
    monkeypatch.setattr(fundos.ingest, "download_video",
                        lambda *a: pytest.fail("não devia tentar baixar"))
    with pytest.raises(ValueError, match="link"):
        fundos.fetch("minecraft parkour")


def test_o_que_esta_guardado_pode_ser_listado_e_apagado(monkeypatch):
    def fake_download(url, home):
        path = home / "s.mp4"
        path.write_bytes(b"v")      # write_bytes devolve a contagem, não o path
        return path, {"title": "P"}

    monkeypatch.setattr(fundos.ingest, "download_video", fake_download)
    monkeypatch.setattr(fundos.render, "probe_duration", lambda p: 300.0)
    fundo = fundos.fetch("https://youtu.be/x")

    listed = fundos.listar()
    assert [item["id"] for item in listed] == [fundo.id]
    assert listed[0]["seconds"] == 300.0

    assert fundos.remove(fundo.id) is True
    assert fundos.listar() == []
    assert fundos.remove(fundo.id) is False


# ------------------------------------------------------ trecho diferente

def test_dois_jobs_abrem_em_trechos_diferentes():
    """Dez vídeos abrindo no mesmo frame é o que faz um perfil parecer
    automático."""
    fundo = fundos.Fundo(id="f", name="p", path=Path("x.mp4"), seconds=1800.0)
    starts = {fundos.window(fundo, 90.0, seed=f"job_{n}") for n in range(8)}
    assert len(starts) >= 7, f"trechos repetidos demais: {starts}"


def test_o_mesmo_job_re_renderizado_pega_o_mesmo_trecho():
    """Senão o QA re-renderiza e o vídeo vira outro."""
    fundo = fundos.Fundo(id="f", name="p", path=Path("x.mp4"), seconds=1800.0)
    assert fundos.window(fundo, 90.0, "job_42") == fundos.window(fundo, 90.0, "job_42")


def test_o_trecho_nunca_encosta_nas_pontas():
    """Começo é intro e logo, fim é tela de inscrever-se."""
    fundo = fundos.Fundo(id="f", name="p", path=Path("x.mp4"), seconds=600.0)
    for n in range(30):
        start = fundos.window(fundo, 60.0, seed=str(n))
        assert start >= fundos.EDGE
        assert start + 60.0 <= 600.0 - fundos.EDGE + 0.01


def test_um_arquivo_curto_comeca_do_inicio():
    """Não há folga para sortear: começa do zero e o render dá a volta."""
    fundo = fundos.Fundo(id="f", name="p", path=Path("x.mp4"), seconds=20.0)
    assert fundos.window(fundo, 90.0, "job") == 0.0


# ----------------------------------------------------------- o render

@needs_ffmpeg
def test_o_fundo_sai_mudo_no_quadro_do_short(tmp_path):
    """Som de jogo por baixo de uma história é a diferença entre 'fundo' e
    'dois vídeos ao mesmo tempo'."""
    source = _clip(tmp_path / "gameplay.mp4", 30)
    assert _has_audio(source), "o fixture tem que ter áudio para o teste valer"
    fundo = fundos.Fundo(id="f", name="gameplay", path=source, seconds=30.0)

    out = fundos.build(fundo, 6.0, tmp_path / "bg.mp4", seed="job_1")

    assert _has_audio(out) is False
    assert _size(out) == "1080x1920"
    assert _duration(out) == pytest.approx(6.0, abs=0.3)


@needs_ffmpeg
def test_um_fundo_mais_curto_que_a_narracao_da_a_volta(tmp_path):
    """Vinte segundos de parkour por baixo de noventa de história não podem
    acabar em preto."""
    source = _clip(tmp_path / "curto.mp4", 4)
    fundo = fundos.Fundo(id="f", name="curto", path=source, seconds=4.0)

    out = fundos.build(fundo, 12.0, tmp_path / "bg.mp4", seed="job_1")
    assert _duration(out) == pytest.approx(12.0, abs=0.3)


# ------------------------------------------------------------- as rotas

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def test_guardar_sem_link_nem_arquivo_e_400(client):
    assert client.post("/api/fundos", json={}).status_code == 400


def test_o_fundo_escolhido_e_exigido_quando_o_modo_pede(tmp_path, monkeypatch):
    """Pedir footage própria e não escolher nenhuma tem que dizer o que falta,
    não desenhar um gradiente que parece pronto."""
    from app.pipeline import orchestrator
    from app.pipeline.ingest import SourceMaterial
    from app.schemas import JobInput, ScriptSegment, ShortScript

    job = JobInput(source_type="tema", source="uma história",
                   background="video_fundo")
    script = ShortScript(title="t", description="d", hashtags=[],
                         segments=[ScriptSegment(kind="corpo", text="frase")],
                         estimated_seconds=20)

    class _Narration:
        duration = 20.0
        words = [{"word": "frase", "start": 0.0, "end": 0.4}]

    with pytest.raises(RuntimeError, match="nenhum foi"):
        orchestrator._build_background(  # noqa: SLF001
            job, script, _Narration(), SourceMaterial(kind="tema", title="x"),
            tmp_path, 20.0, lambda m, level="info": None)
