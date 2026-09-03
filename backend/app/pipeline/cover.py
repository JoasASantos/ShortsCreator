"""Capa do short: o frame mais rico do vídeo + título grande + faixa do nicho.

Thumbnail padrão (thumb.jpg) é um frame fixo do começo — quase sempre o fundo
antes de qualquer coisa acontecer. Aqui amostramos frames ao longo do vídeo,
medimos detalhe (desvio-padrão da luminância, um proxy barato de "tem coisa
na imagem") e escolhemos o melhor fora dos primeiros 300 ms. Sobre ele vai um
gradiente escuro na base e o título quebrado em até 3 linhas, no mesmo estilo
das legendas para a identidade visual bater.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageStat

from ..config import settings
from . import broll
from .captions import SAFE_BOTTOM
from .overlays import _font

W, H = settings.width, settings.height
SAMPLE_EVERY = 0.7            # segundos entre frames candidatos
SKIP_HEAD = 0.3               # o começo costuma ser fade-in ou fundo cru
TITLE_BAND_TOP = int(H * 0.56)
# O título respeita a MESMA safe area das legendas: os 340px de baixo somem
# atrás da interface do app, e uma capa cortada é pior que uma capa sem graça.
TITLE_BOTTOM = H - SAFE_BOTTOM - 40


def build(video: Path, title: str, niche: str, out: Path, duration: float,
          work_dir: Path | None = None) -> tuple[Path, float]:
    """Gera cover.jpg. Retorna (caminho, instante do frame escolhido em segundos)."""
    work = (work_dir or out.parent) / "cover_frames"
    work.mkdir(parents=True, exist_ok=True)

    at = pick_frame_time(video, duration, work)
    frame = work / "chosen.jpg"
    _extract(video, at, frame)

    image = Image.open(frame).convert("RGB").resize((W, H))
    _compose(image, title, niche).save(out, "JPEG", quality=92, optimize=True)
    return out, at


def pick_frame_time(video: Path, duration: float, work: Path) -> float:
    """Frame com mais detalhe visual entre SKIP_HEAD e 85% do vídeo."""
    end = max(duration * 0.85, SKIP_HEAD + 0.5)
    best_at, best_score = SKIP_HEAD, -1.0
    t = SKIP_HEAD
    index = 0
    while t < end and index < 60:
        sample = work / f"s_{index:02d}.jpg"
        try:
            _extract(video, t, sample, small=True)
            gray = Image.open(sample).convert("L")
            stat = ImageStat.Stat(gray)
            std = stat.stddev[0]
            mean = stat.mean[0]
            # penaliza frames muito escuros ou estourados — aparecem bem só no feed
            penalty = abs(mean - 118) / 118
            score = std * (1 - 0.5 * penalty)
            if score > best_score:
                best_score, best_at = score, t
        except Exception:  # noqa: BLE001 — frame ruim só é pulado
            pass
        t += SAMPLE_EVERY
        index += 1
    return round(best_at, 2)


def _extract(video: Path, at: float, out: Path, small: bool = False) -> None:
    vf = "scale=270:480" if small else f"scale={W}:{H}"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
         "-vf", vf, "-q:v", "3", str(out)],
        check=True, capture_output=True,
    )


def _compose(image: Image.Image, title: str, niche: str) -> Image.Image:
    c0, _ = broll.palette(niche)
    accent = _hex(c0)

    # desfoque + escurecimento na faixa do título: sem isso o texto disputa
    # atenção com o fundo e some em qualquer imagem clara
    # 1. Onde o texto vai ficar. Precisa vir ANTES do escurecimento: senão o
    # gradiente não sabe até onde subir e um título de 3 linhas nasce na parte
    # clara da faixa, ilegível sobre fundo branco.
    draw = ImageDraw.Draw(image)
    clean = " ".join(title.split())
    size = 132
    while size > 72:
        font = _font(size)
        lines = textwrap.wrap(clean.upper(), width=max(8, int(W * 0.86 / (size * 0.56))))
        if len(lines) <= 3 and all(
                draw.textlength(line, font=font) <= W * 0.88 for line in lines):
            break
        size -= 8
    else:
        font = _font(size)
        lines = textwrap.wrap(clean.upper(), width=18)[:3]

    line_h = int(size * 1.08)
    total_h = line_h * len(lines)
    # ancorado pelo RODAPÉ útil: com 1, 2 ou 3 linhas o texto nunca entra na
    # faixa da interface do app
    top = TITLE_BOTTOM - total_h

    # 2. Desfoque e escurecimento: opaco de onde o texto começa para baixo,
    # desbotando para cima ao longo de 220px.
    fade_start = max(min(top - 220, TITLE_BAND_TOP), 0)
    band_h = H - fade_start
    band = image.crop((0, fade_start, W, H)).filter(ImageFilter.GaussianBlur(10))
    image.paste(band, (0, fade_start))

    solid_from = top - fade_start
    gradient = Image.new("L", (1, band_h))
    for y in range(band_h):
        ratio = 1.0 if solid_from <= 0 else min(y / solid_from, 1.0)
        gradient.putpixel((0, y), int(225 * ratio ** 1.4))
    shade = Image.new("RGB", (W, band_h), (6, 8, 10))
    image.paste(shade, (0, fade_start), gradient.resize((W, band_h)))

    # 3. Título, com a faixa de cor do nicho à esquerda do bloco
    x_left = int(W * 0.06)
    draw = ImageDraw.Draw(image)
    draw.rectangle([x_left - 34, top + 8, x_left - 18, top + total_h - 8], fill=accent)

    y = top
    for line in lines:
        draw.text((x_left, y), line, font=font, fill=(255, 255, 255),
                  stroke_width=max(4, size // 22), stroke_fill=(0, 0, 0))
        y += line_h
    return image


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) != 6:
        return (255, 196, 0)
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
