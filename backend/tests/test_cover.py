"""Capa: escolha do frame, safe area e legibilidade do título."""
from __future__ import annotations

from PIL import Image, ImageStat

from app.config import settings
from app.pipeline import cover, render
from app.pipeline.captions import SAFE_BOTTOM

from conftest import needs_ffmpeg

W, H = settings.width, settings.height


@needs_ffmpeg
def test_capa_sai_em_9x16(sample_video, tmp_path):
    out, at = cover.build(sample_video, "Título de teste", "tecnologia",
                          tmp_path / "cover.jpg", 4.0, tmp_path)
    assert out.exists()
    assert Image.open(out).size == (W, H)
    assert at >= cover.SKIP_HEAD


@needs_ffmpeg
def test_frame_escolhido_evita_o_comeco_e_o_fim(sample_video, tmp_path):
    """O começo costuma ser fade-in e o fim, corte seco — nenhum dos dois
    representa o vídeo na miniatura."""
    at = cover.pick_frame_time(sample_video, 4.0, tmp_path)
    assert cover.SKIP_HEAD <= at <= 4.0 * 0.85


def test_titulo_nunca_entra_na_area_da_interface():
    """Mesma regra que o QA cobra da legenda: os 340px de baixo são da UI."""
    assert cover.TITLE_BOTTOM <= H - SAFE_BOTTOM


def test_titulo_longo_e_curto_ficam_na_safe_area():
    """Ancorado pelo rodapé: 1 ou 3 linhas, o bloco termina no mesmo lugar."""
    base = Image.new("RGB", (W, H), (255, 255, 255))
    limite = H - SAFE_BOTTOM

    for titulo in ("Vazou", "Como o ataque da SolarWinds funcionou de verdade"):
        img = cover._compose(base.copy(), titulo, "tecnologia")  # noqa: SLF001
        # nenhuma marca escura de texto abaixo do limite da safe area
        faixa = img.crop((0, limite + 20, W, H)).convert("L")
        assert ImageStat.Stat(faixa).stddev[0] < 40, (
            f"'{titulo[:20]}' desenhou conteúdo dentro da faixa da interface")


def test_titulo_fica_legivel_sobre_fundo_branco():
    """Sem o escurecimento, texto branco sobre imagem clara desaparece. O
    contraste é medido onde o título realmente é desenhado."""
    branco = Image.new("RGB", (W, H), (255, 255, 255))
    img = cover._compose(branco, "Como o ataque funcionou", "tecnologia")  # noqa: SLF001

    faixa = img.crop((0, cover.TITLE_BOTTOM - 200, W, cover.TITLE_BOTTOM)).convert("L")
    # a região do título deixou de ser branca (foi escurecida)
    assert ImageStat.Stat(faixa).mean[0] < 130
    # e ainda tem contraste — o texto branco por cima do escuro
    assert ImageStat.Stat(faixa).stddev[0] > 25


def test_compose_aceita_titulo_gigante_sem_estourar():
    base = Image.new("RGB", (W, H), (40, 40, 40))
    img = cover._compose(base, " ".join(["palavra"] * 40), "generico")  # noqa: SLF001
    assert img.size == (W, H)


def test_hex_invalido_cai_no_ambar_padrao():
    assert cover._hex("#ffc400") == (255, 196, 0)   # noqa: SLF001
    assert cover._hex("lixo") == (255, 196, 0)      # noqa: SLF001


@needs_ffmpeg
def test_preview_gif_e_pequeno_e_animado(sample_video, tmp_path):
    gif = render.make_preview_gif(sample_video, tmp_path / "p.gif", seconds=2.0)
    assert gif.exists()
    # miniatura do painel: precisa ser leve o suficiente para carregar em lista
    assert gif.stat().st_size < 900_000
    with Image.open(gif) as im:
        assert im.n_frames > 1, "o GIF saiu com um quadro só"
        assert im.width == 270
