"""Tutorial / explainer reels with motion graphics.

Three input modes, all optional:

1. **Face cam** (video upload): split-screen — motion on top, face cam on bottom.
   Audio transcribed from the video. Captions in the centre.

2. **Audio only** (audio upload or recording): full-screen motion graphics.
   Audio transcribed for word timings. Captions over motion.

3. **Script only** (text or topic): LLM writes script, TTS speaks it (edge-tts,
   ElevenLabs, or HeyGen voice), full-screen motion. Pure generation.

Storyboard: the LLM plans which visual panels to show at each moment, with
stock image queries. Images are fetched from Pexels/Pixabay and composited
into the panels alongside text, emojis and diagrams.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import settings
from ..schemas import JobInput
from . import broll, captions as captions_mod, llm, notify, reels, render
from . import timeline as timeline_mod, timeline_render, tts as tts_mod
from .overlays import find_font
from .timeline import AudioClip, Timeline, VideoClip

MODE = "tutorial"

W, H, FPS = settings.width, settings.height, settings.fps

SPLIT_MOTION_H = int(H * 0.50)
SPLIT_FACECAM_H = H - SPLIT_MOTION_H

WHITE = (255, 255, 255, 255)
GREY = (200, 200, 200, 255)
AMBER = (245, 166, 35, 255)
ORANGE = (232, 116, 79, 255)
BG_COLOR = (10, 10, 10, 255)


# ============================================================================
#  Storyboard planning
# ============================================================================

STORYBOARD_SYSTEM = """Você planeja motion graphics para vídeos tutoriais verticais (Reels / Shorts).

Você recebe a transcrição com tempos e retorna STEPS: painéis visuais com
ícones, diagramas, imagens de stock e texto grande — tudo sincronizado com a
fala. Cada step pode ter uma `stock_query` para buscar imagem real da internet.

Responda somente JSON."""

STORYBOARD_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "style": {
            "type": "string",
            "enum": ["keynote", "hud", "line", "screencast"],
            "description": "Visual do vídeo inteiro. keynote: palco tech escuro, "
                           "sweep de luz, número gigante. hud: monospace ciano/âmbar, "
                           "número faz scramble. line: papel claro, traço contínuo. "
                           "screencast: interface em HTML com cursor animado.",
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "label": {
                        "type": "string",
                        "description": "Texto do topo. Formato 'Passo N. Ação' "
                                       "(prefixo curto + ação). STRING, nunca objeto.",
                    },
                    "icons": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "emoji": {"type": "string"},
                            },
                            "required": ["name", "emoji"],
                        },
                    },
                    "layout": {
                        "type": "string",
                        "enum": ["icons_row", "connection", "list", "highlight",
                                 "big_text", "counter", "screencast"],
                    },
                    "subtitle": {"type": "string"},
                    "stock_query": {
                        "type": "string",
                        "description": "Busca de FOTO real em INGLÊS, concreta e "
                                       "fotográfica (pessoa, lugar, objeto, cena). "
                                       "NUNCA peça screenshot de app, print de tela, "
                                       "logo em fundo branco ou UI. Vazio se emoji basta.",
                    },
                    "big_words": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "layout big_text: 1-3 palavras CURTAS (máx ~8 "
                                       "caracteres cada, uma palavra por item, sem "
                                       "quebrar). Cada item é uma linha enorme.",
                    },
                    "number": {
                        "type": "number",
                        "description": "layout counter: valor exato que o número "
                                       "gigante atinge contando (ex: 60, 1500).",
                    },
                    "number_prefix": {"type": "string",
                                      "description": "Ex: 'R$', '+'. Opcional."},
                    "number_suffix": {"type": "string",
                                      "description": "Ex: '%', 'x', 'seg'. Opcional."},
                    "number_label": {"type": "string",
                                     "description": "Legenda do número no counter."},
                    "accent": {"type": "string",
                               "description": "Em big_text, a palavra (de big_words) "
                                              "que recebe a cor de destaque. Só uma."},
                    "ui": {
                        "type": "object",
                        "description": "layout screencast: interface a reconstruir.",
                        "properties": {
                            "title": {"type": "string"},
                            "rows": {"type": "array", "items": {"type": "string"},
                                     "description": "Itens do menu/lista da tela."},
                            "click_row": {"type": "integer",
                                          "description": "Índice (0-based) da linha clicada."},
                        },
                    },
                },
                "required": ["start", "end", "label", "icons", "layout"],
            },
        },
    },
    "required": ["title", "style", "steps"],
}


def plan_storyboard(words: list[dict], duration: float,
                    instruction: str = "", language: str = "pt-BR",
                    full_screen: bool = False,
                    log=lambda m, level="info": None) -> dict:
    from .script import language_name

    transcript = reels._timed_transcript(words)
    wanted = instruction.strip()
    mode_desc = ("tela cheia com fundo escuro" if full_screen
                 else "metade superior (metade de baixo é face cam)")

    prompt = f"""Vídeo tutorial, {duration:.0f} segundos. Motion graphics ocupam {mode_desc}.

{"INSTRUÇÃO DO CRIADOR: " + wanted if wanted else ""}

TRANSCRIÇÃO COM TEMPOS:
{transcript}

Escolha um `style` para o vídeo inteiro (coerente com o tema):
- keynote: palco tech escuro, elegante. Padrão para tech/produto/geral.
- hud: ciano e âmbar monospace, cara de painel/dados. Para números, métricas,
  segurança, "sistema".
- line: papel claro, traço de caneta. Para histórias, conceitos, didático leve.
- screencast: reconstrói uma interface. Só quando o vídeo mostra um app/site.

Planeje os STEPS (painéis visuais):
- Cada step cobre um trecho contínuo sem sobreposição; cobrir toda a duração; máx 12.
- `label`: STRING "Passo N. Ação" (prefixo curto + ação). Nunca objeto.
- `layout` por step, VARIE para dar ritmo:
  - big_text: 1-3 palavras CURTAS e de impacto (big_words). Uma palavra por linha,
    máx ~8 caracteres, nunca palavra que quebraria. Marque uma em `accent`.
  - counter: UM número que importa (number + number_label, number_prefix/suffix).
    Use sempre que a fala citar uma quantidade, prazo, preço, % ou "350+".
  - highlight: um conceito central (1 ícone grande + nome).
  - icons_row: 2-5 conceitos lado a lado.
  - list: passos/itens (3-6). connection: fluxo A->B->C.
  - screencast: só com style=screencast, quando mostra uma tela (ui.title, ui.rows,
    ui.click_row).
- `stock_query`: FOTO real, fotográfica e concreta, em INGLÊS (pessoa agindo, lugar,
  objeto, cena). NUNCA screenshot de app, print de UI, logo em fundo branco ou
  "website homepage". Vazio quando o emoji/número já basta.
- `subtitle`: uma linha curta de apoio.
- Todo texto visível em {language_name(language)}."""

    data = llm.complete_json(STORYBOARD_SYSTEM, prompt, STORYBOARD_SCHEMA,
                             max_tokens=6000, purpose="tutorial_storyboard")
    steps = data.get("steps") or []
    steps = [s for s in steps
             if isinstance(s, dict) and s.get("start") is not None
             and s.get("end") is not None
             and float(s["end"]) > float(s["start"])]
    for step in steps:
        step["start"] = max(round(float(step["start"]), 2), 0.0)
        step["end"] = min(round(float(step["end"]), 2), duration)
        step["label"] = _normalize_label(step.get("label"))

    style = str(data.get("style") or "keynote").lower()
    if style not in ("keynote", "hud", "line", "screencast"):
        style = "keynote"
    log(f"Storyboard: {len(steps)} step(s), style={style}")
    return {"title": str(data.get("title") or ""), "style": style, "steps": steps}


def _normalize_label(label) -> str:
    """The LLM sometimes returns a structured label ({'italic':.., 'bold':..})
    instead of a plain 'Passo N. Ação' string. Flatten it back to text."""
    if isinstance(label, dict):
        italic = str(label.get("italic") or label.get("prefix") or "").strip()
        bold = str(label.get("bold") or label.get("action") or
                   label.get("text") or "").strip()
        return f"{italic} {bold}".strip()
    return str(label or "").strip()


# ============================================================================
#  Stock image fetcher — three sources, in priority order:
#  1. Google Images via Playwright (real logos, screenshots, institutions)
#  2. Pexels API (high-quality stock photos)
#  3. AI image generation (imagegen module — creates what doesn't exist)
# ============================================================================

def _fetch_via_bing_images(query: str, dest: Path,
                           log=lambda m, level="info": None) -> bool:
    """Headless Chromium via Bing Images — works for logos, institutions, etc."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    import json as _json
    import time as _time
    import httpx
    from urllib.parse import quote_plus

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            url = f"https://www.bing.com/images/search?q={quote_plus(query)}&form=HDRSC2"
            page.goto(url, timeout=15000)
            page.wait_for_load_state("domcontentloaded")
            _time.sleep(2)

            links = page.query_selector_all("a.iusc")
            for link in links[:8]:
                m_attr = link.get_attribute("m") or ""
                if not m_attr:
                    continue
                try:
                    m_data = _json.loads(m_attr)
                except Exception:
                    continue
                img_url = m_data.get("murl", "")
                if not img_url or any(x in img_url.lower() for x in [".svg", ".gif", ".ico"]):
                    continue
                try:
                    resp = httpx.get(img_url, timeout=12.0, follow_redirects=True)
                    if resp.status_code == 200 and len(resp.content) > 5000:
                        dest.write_bytes(resp.content)
                        browser.close()
                        return True
                except Exception:
                    continue
            browser.close()
    except Exception as exc:
        log(f"Bing Images failed for '{query}': {exc}", "warn")
    return False


def _fetch_via_pexels(query: str, dest: Path,
                      log=lambda m, level="info": None) -> bool:
    """Pexels photo API — high quality stock images."""
    import httpx

    pexels_key = getattr(settings, "pexels_api_key", "")
    if not pexels_key:
        return False
    try:
        resp = httpx.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": pexels_key},
            params={"query": query, "per_page": 1, "orientation": "portrait"},
            timeout=15.0,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos") or []
        if photos:
            img_url = (photos[0].get("src", {}).get("large2x")
                       or photos[0].get("src", {}).get("large", ""))
            if img_url:
                img_resp = httpx.get(img_url, timeout=20.0, follow_redirects=True)
                img_resp.raise_for_status()
                if len(img_resp.content) > 5000:
                    dest.write_bytes(img_resp.content)
                    return True
    except Exception as exc:
        log(f"Pexels failed for '{query}': {exc}", "warn")
    return False


def _fetch_via_imagegen(query: str, dest: Path,
                        log=lambda m, level="info": None) -> bool:
    """AI-generated image via configured provider (GPT Image, Gemini, etc.)."""
    try:
        from . import imagegen
        if not imagegen.providers_ready():
            return False
        prompt = (f"Clean, professional illustration for a tutorial video panel. "
                  f"Dark background (#0A0A0A). Subject: {query}. "
                  f"Flat design, bold colors, centered composition. "
                  f"No text overlays. 9:16 portrait.")
        imagegen.generate_image(prompt, dest, aspect="9:16", log=log)
        return dest.exists() and dest.stat().st_size > 1000
    except Exception as exc:
        log(f"AI imagegen failed for '{query}': {exc}", "warn")
    return False


def fetch_stock_images(storyboard: dict, out_dir: Path,
                       log=lambda m, level="info": None) -> dict[int, Path]:
    """Download one image per step that requested it.

    Tries three sources in order:
    1. Google Images (Playwright) — best for logos, real screenshots, institutions
    2. Pexels API — high quality stock photos
    3. AI image generation — creates what doesn't exist in stock
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    images: dict[int, Path] = {}

    for index, step in enumerate(storyboard.get("steps") or []):
        query = str(step.get("stock_query") or "").strip()
        if not query:
            continue

        dest = out_dir / f"stock_{index:03d}.jpg"

        if _fetch_via_bing_images(query, dest, log):
            log(f"Step {index} image via Bing: {query}")
            images[index] = dest
            continue

        if _fetch_via_pexels(query, dest, log):
            log(f"Step {index} image via Pexels: {query}")
            images[index] = dest
            continue

        if _fetch_via_imagegen(query, dest, log):
            log(f"Step {index} image via AI: {query}")
            images[index] = dest
            continue

        log(f"No image found for step {index}: {query} — using emoji", "warn")

    return images


# ============================================================================
#  Motion panel rendering
# ============================================================================

@dataclass
class MotionPanel:
    path: Path
    start: float
    end: float


def render_motion_panels(storyboard: dict, out_dir: Path,
                         panel_height: int = 0,
                         stock_images: dict[int, Path] | None = None,
                         log=lambda m, level="info": None) -> list[MotionPanel]:
    out_dir.mkdir(parents=True, exist_ok=True)
    panels: list[MotionPanel] = []
    ph = panel_height or H
    stock = stock_images or {}

    try:
        font_path = find_font()
    except RuntimeError:
        font_path = None

    for index, step in enumerate(storyboard.get("steps") or []):
        path = out_dir / f"motion_{index:03d}.png"
        _draw_panel(step, path, font_path, ph, stock.get(index))
        panels.append(MotionPanel(
            path=path,
            start=float(step["start"]),
            end=float(step["end"]),
        ))

    log(f"Rendered {len(panels)} motion panel(s)")
    return panels


def _draw_panel(step: dict, out: Path, font_path: str | None,
                panel_h: int, stock_image: Path | None = None) -> None:
    image = Image.new("RGBA", (W, panel_h), BG_COLOR)
    draw = ImageDraw.Draw(image)

    def font(size: int) -> ImageFont.FreeTypeFont:
        if font_path:
            return ImageFont.truetype(font_path, size)
        return ImageFont.load_default()

    label = str(step.get("label") or "")
    icons = step.get("icons") or []
    layout = str(step.get("layout") or "icons_row")
    subtitle = str(step.get("subtitle") or "")
    big_words = step.get("big_words") or []

    # --- Stock image as background if available ---
    if stock_image and stock_image.exists():
        try:
            bg = Image.open(stock_image).convert("RGBA")
            bg = bg.resize((W, panel_h), Image.LANCZOS)
            dark = Image.new("RGBA", (W, panel_h), (0, 0, 0, 160))
            image = Image.alpha_composite(bg, dark)
            draw = ImageDraw.Draw(image)
        except Exception:
            pass

    # --- Big text mode ---
    if layout == "big_text" and big_words:
        _draw_big_text(draw, big_words, font, label, subtitle, panel_h)
        image.save(out)
        return

    # --- Step label at the top ---
    label_font = font(46)
    parts = label.split(".", 1)
    label_y = 40
    if len(parts) == 2 and parts[0].strip():
        prefix = parts[0].strip() + "."
        action = parts[1].strip()
        pw = draw.textlength(prefix, font=font(42))
        total_w = pw + 8 + draw.textlength(action, font=label_font)
        start_x = max((W - total_w) // 2, 20)
        draw.text((start_x, label_y), prefix, font=font(42), fill=GREY)
        draw.text((start_x + pw + 8, label_y), action, font=label_font, fill=WHITE)
    elif label:
        tw = draw.textlength(label, font=label_font)
        draw.text((max((W - tw) // 2, 20), label_y), label, font=label_font, fill=WHITE)

    # --- Icons ---
    if icons:
        icon_font = font(60)
        name_font = font(22)
        icon_y = panel_h // 2 - 40

        if layout == "icons_row":
            _draw_icons_row(draw, icons, icon_font, name_font, icon_y)
        elif layout == "tree":
            _draw_tree(draw, icons, icon_font, name_font, icon_y)
        elif layout == "list":
            _draw_list(draw, icons, icon_font, name_font, 120)
        elif layout == "connection":
            _draw_connection(draw, icons, icon_font, name_font, icon_y)
        elif layout == "comparison":
            _draw_comparison(draw, icons, font, icon_y, panel_h)
        elif layout == "highlight":
            _draw_highlight(draw, icons, font, icon_y)
        else:
            _draw_icons_row(draw, icons, icon_font, name_font, icon_y)

    # --- Subtitle ---
    if subtitle:
        sub_font = font(28)
        tw = draw.textlength(subtitle, font=sub_font)
        draw.text((max((W - tw) // 2, 20), panel_h - 70),
                  subtitle, font=sub_font, fill=GREY)

    image.save(out)


def _draw_big_text(draw, words: list[str], font_fn, label: str,
                   subtitle: str, panel_h: int) -> None:
    if label:
        lf = font_fn(32)
        tw = draw.textlength(label, font=lf)
        draw.text((max((W - tw) // 2, 20), 60), label, font=lf, fill=GREY)

    y = panel_h // 2 - len(words) * 50
    for word in words[:3]:
        bf = font_fn(96)
        tw = draw.textlength(word, font=bf)
        draw.text((max((W - tw) // 2, 20), y), word, font=bf, fill=WHITE,
                  stroke_width=4, stroke_fill=(0, 0, 0, 200))
        y += 120

    if subtitle:
        sf = font_fn(28)
        tw = draw.textlength(subtitle, font=sf)
        draw.text((max((W - tw) // 2, 20), panel_h - 80), subtitle, font=sf, fill=GREY)


def _draw_comparison(draw, icons: list[dict], font_fn, y: int, panel_h: int) -> None:
    card_w = W // 2 - 40
    card_h = panel_h // 3
    for i, icon in enumerate(icons[:2]):
        cx = 30 + i * (card_w + 20)
        draw.rounded_rectangle(
            [(cx, y), (cx + card_w, y + card_h)],
            radius=16, fill=(30, 30, 30, 255), outline=(60, 60, 60, 255))
        emoji = str(icon.get("emoji") or "")
        name = str(icon.get("name") or "")
        ef = font_fn(48)
        nf = font_fn(22)
        if emoji:
            ew = draw.textlength(emoji, font=ef)
            draw.text((cx + (card_w - ew) // 2, y + 20), emoji, font=ef, fill=WHITE)
        if name:
            nw = draw.textlength(name, font=nf)
            draw.text((cx + (card_w - nw) // 2, y + 90), name, font=nf,
                      fill=ORANGE if i == 1 else GREY)


def _draw_highlight(draw, icons: list[dict], font_fn, y: int) -> None:
    if not icons:
        return
    icon = icons[0]
    emoji = str(icon.get("emoji") or "")
    name = str(icon.get("name") or "")
    ef = font_fn(96)
    nf = font_fn(36)
    if emoji:
        ew = draw.textlength(emoji, font=ef)
        draw.text(((W - ew) // 2, y - 20), emoji, font=ef, fill=WHITE)
    if name:
        nw = draw.textlength(name, font=nf)
        draw.text(((W - nw) // 2, y + 100), name, font=nf, fill=WHITE)


def _draw_icons_row(draw, icons: list[dict], icon_font, name_font, y: int) -> None:
    count = min(len(icons), 5)
    spacing = W // (count + 1)
    for i, icon in enumerate(icons[:count]):
        cx = spacing * (i + 1)
        emoji = str(icon.get("emoji") or "")
        name = str(icon.get("name") or "")
        if emoji:
            ew = draw.textlength(emoji, font=icon_font)
            draw.text((cx - ew // 2, y), emoji, font=icon_font, fill=WHITE)
        if name:
            nw = draw.textlength(name, font=name_font)
            draw.text((cx - nw // 2, y + 80), name, font=name_font, fill=GREY)


def _draw_tree(draw, icons: list[dict], icon_font, name_font, y: int) -> None:
    if not icons:
        return
    root = icons[0]
    children = icons[1:5]
    root_x = W // 2
    root_y = y - 40
    emoji = str(root.get("emoji") or "")
    name = str(root.get("name") or "")
    if emoji:
        ew = draw.textlength(emoji, font=icon_font)
        draw.text((root_x - ew // 2, root_y), emoji, font=icon_font, fill=WHITE)
    if name:
        nw = draw.textlength(name, font=name_font)
        draw.text((root_x - nw // 2, root_y + 75), name, font=name_font, fill=GREY)
    if children:
        draw.line([(root_x, root_y + 95), (root_x, root_y + 130)],
                  fill=(100, 100, 100, 255), width=2)
        child_spacing = W // (len(children) + 1)
        child_y = root_y + 140
        for i, child in enumerate(children):
            cx = child_spacing * (i + 1)
            draw.line([(root_x, root_y + 130), (cx, child_y)],
                      fill=(100, 100, 100, 255), width=2)
            cemoji = str(child.get("emoji") or "")
            cname = str(child.get("name") or "")
            if cemoji:
                ew = draw.textlength(cemoji, font=icon_font)
                draw.text((cx - ew // 2, child_y + 5), cemoji, font=icon_font, fill=WHITE)
            if cname:
                nw = draw.textlength(cname, font=name_font)
                draw.text((cx - nw // 2, child_y + 85), cname, font=name_font, fill=GREY)


def _draw_connection(draw, icons: list[dict], icon_font, name_font, y: int) -> None:
    count = min(len(icons), 4)
    spacing = W // (count + 1)
    positions = []
    for i, icon in enumerate(icons[:count]):
        cx = spacing * (i + 1)
        positions.append(cx)
        emoji = str(icon.get("emoji") or "")
        name = str(icon.get("name") or "")
        if emoji:
            ew = draw.textlength(emoji, font=icon_font)
            draw.text((cx - ew // 2, y), emoji, font=icon_font, fill=WHITE)
        if name:
            nw = draw.textlength(name, font=name_font)
            draw.text((cx - nw // 2, y + 80), name, font=name_font, fill=GREY)
    for i in range(len(positions) - 1):
        x1 = positions[i] + 35
        x2 = positions[i + 1] - 35
        for dot_x in range(int(x1), int(x2), 12):
            draw.ellipse([(dot_x, y + 30), (dot_x + 4, y + 34)],
                         fill=(120, 120, 120, 255))


def _draw_list(draw, icons: list[dict], icon_font, name_font, start_y: int) -> None:
    for i, icon in enumerate(icons[:6]):
        y = start_y + i * 70
        emoji = str(icon.get("emoji") or "")
        name = str(icon.get("name") or "")
        x = 120
        if emoji:
            draw.text((x, y), emoji, font=icon_font, fill=WHITE)
        if name:
            draw.text((x + 80, y + 20), name, font=name_font, fill=GREY)


# ============================================================================
#  Video composition
# ============================================================================

def frame_facecam(source: Path, duration: float, out: Path) -> Path:
    render._run([
        "ffmpeg", "-y", "-i", str(source),
        "-t", f"{duration:.3f}",
        "-vf", (f"scale={W}:{SPLIT_FACECAM_H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{SPLIT_FACECAM_H},setsar=1,fps={FPS},format=yuv420p"),
        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "21", "-pix_fmt", "yuv420p", "-an", str(out),
    ])
    return out


def _vstack_videos(job_dir: Path, motion_video: Path, facecam: Path,
                   duration: float, log=lambda m, level="info": None) -> Path:
    """Stack a pre-rendered motion video (top) over face cam (bottom)."""
    out = job_dir / "split_screen.mp4"
    render._run([
        "ffmpeg", "-y",
        "-i", str(motion_video), "-i", str(facecam),
        "-filter_complex",
        f"[0:v]scale={W}:{SPLIT_MOTION_H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{SPLIT_MOTION_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[top];"
        f"[1:v]scale={W}:{SPLIT_FACECAM_H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{SPLIT_FACECAM_H},setsar=1[bot];"
        f"[top][bot]vstack=inputs=2,fps={FPS},format=yuv420p[v]",
        "-map", "[v]", "-t", f"{duration:.3f}",
        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-pix_fmt", "yuv420p", str(out),
    ])
    log(f"Split-screen composed: {out.name}")
    return out


def compose_split_screen(job_dir: Path, facecam: Path, panels: list[MotionPanel],
                         duration: float, log=lambda m, level="info": None) -> Path:
    out = job_dir / "split_screen.mp4"
    motion_video = _panels_to_video(job_dir, panels, duration, SPLIT_MOTION_H)
    render._run([
        "ffmpeg", "-y",
        "-i", str(motion_video), "-i", str(facecam),
        "-filter_complex",
        f"[0:v]scale={W}:{SPLIT_MOTION_H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{SPLIT_MOTION_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[top];"
        f"[1:v]scale={W}:{SPLIT_FACECAM_H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{SPLIT_FACECAM_H},setsar=1[bot];"
        f"[top][bot]vstack=inputs=2,fps={FPS},format=yuv420p[v]",
        "-map", "[v]", "-t", f"{duration:.3f}",
        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-pix_fmt", "yuv420p", str(out),
    ])
    log(f"Split-screen composed: {out.name}")
    return out


def compose_full_motion(job_dir: Path, panels: list[MotionPanel],
                        duration: float, log=lambda m, level="info": None) -> Path:
    out = job_dir / "full_motion.mp4"
    motion_video = _panels_to_video(job_dir, panels, duration, H)
    shutil.copy(motion_video, out)
    log(f"Full-motion composed: {out.name}")
    return out


def _panels_to_video(job_dir: Path, panels: list[MotionPanel],
                     duration: float, height: int) -> Path:
    work = job_dir / "motion_work"
    work.mkdir(exist_ok=True)

    def _black_clip(name: str, dur: float) -> Path:
        p = work / name
        render._run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=black:s={W}x{height}:d={dur:.3f}:r={FPS}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p", str(p),
        ])
        return p

    if not panels:
        return _black_clip("motion.mp4", duration)

    parts: list[tuple[Path, float, float]] = []
    for i, panel in enumerate(panels):
        clip_dur = panel.end - panel.start
        if clip_dur <= 0:
            continue
        clip_path = work / f"pclip_{i:03d}.mp4"
        render._run([
            "ffmpeg", "-y", "-loop", "1",
            "-i", str(panel.path),
            "-t", f"{clip_dur:.3f}",
            "-vf", f"scale={W}:{height},setsar=1,fps={FPS},format=yuv420p",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "21", "-pix_fmt", "yuv420p", str(clip_path),
        ])
        parts.append((clip_path, panel.start, clip_dur))

    if not parts:
        return _black_clip("motion.mp4", duration)

    listing = work / "concat.txt"
    segments: list[str] = []
    cursor = 0.0
    for clip_path, start, dur in parts:
        if start > cursor + 0.04:
            gap = _black_clip(f"gap_{len(segments):03d}.mp4", start - cursor)
            segments.append(f"file '{gap.name}'\n")
            cursor = start
        segments.append(f"file '{clip_path.name}'\n")
        cursor += dur

    if cursor < duration - 0.04:
        gap = _black_clip("gap_tail.mp4", duration - cursor)
        segments.append(f"file '{gap.name}'\n")

    listing.write_text("".join(segments), encoding="utf-8")
    out = work / "motion.mp4"
    render._run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(listing), "-c", "copy", str(out)])
    return out


# ============================================================================
#  Timeline
# ============================================================================

def build_timeline(job_dir: Path, duration: float, words: list[dict],
                   video_source: str, audio_source: str,
                   caption_style: str, caption_position: str,
                   watermark: str = "") -> Timeline:
    video = [VideoClip(
        id=timeline_mod._new_id("v"),
        source=video_source,
        in_point=0.0, out_point=round(duration, 3), start=0.0,
    )]
    audio = [AudioClip(
        id=timeline_mod._new_id("a"),
        source=audio_source,
        in_point=0.0, out_point=round(duration, 3), start=0.0,
        gain=1.0, role="narration",
    )]
    return Timeline(
        duration=round(duration, 3),
        video=video, audio=audio,
        captions=[],
        caption_style=caption_style,
        caption_position=caption_position,
        watermark=watermark,
    ).normalize()


# ============================================================================
#  Detect input mode
# ============================================================================

def _has_video_track(path: Path) -> bool:
    import subprocess
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    return "video" in result.stdout


def _detect_mode(job: JobInput, source: Path | None) -> str:
    """Decide: 'facecam' | 'audio' | 'script'"""
    if source and source.exists():
        if _has_video_track(source):
            return "facecam"
        return "audio"
    return "script"


def _pick_motion_renderer(log=lambda m, level="info": None):
    """The best available motion renderer, or None to fall back to Pillow panels.

    Order: HyperFrames (HTML+GSAP, pro-grade) → motion_html (hand-rolled
    HTML+Playwright) → None (static Pillow). Each returns a callable with the
    signature (storyboard, stock_images, out_path, w, h, fps, duration, log).
    """
    from . import motion_html, motion_hyperframes

    if motion_hyperframes.available():
        log("Motion renderer: HyperFrames (HTML + GSAP)")
        return motion_hyperframes.render_video
    if motion_html.available():
        log("Motion renderer: HTML/Playwright (animated)")
        return motion_html.render_video
    log("Motion renderer: Pillow (static panels — no HTML renderer available)", "warn")
    return None


# ============================================================================
#  Main run
# ============================================================================

def run(job_id: str, job: JobInput, job_dir: Path, log, stage) -> dict:
    from .. import db
    from . import ingest, qa as qa_mod, script as script_mod

    render.ensure_ffmpeg()
    job_dir.mkdir(parents=True, exist_ok=True)

    title = ((db.get_job(job_id) or {}).get("title") or "").strip()

    # ---- 1. Get source material ----
    stage("ingest")
    source = None
    mode = "script"

    if job.source_type in ("video", "url") or job.attachments:
        try:
            source, fetched_title = reels.fetch_source(job, job_dir, log)
            title = title or fetched_title
            mode = _detect_mode(job, source)
        except Exception as exc:
            log(f"Could not fetch source, falling back to script mode: {exc}", "warn")

    if mode == "script" and not job.source.strip() and not job.instruction.strip():
        raise RuntimeError(
            "Tutorial precisa de pelo menos um: vídeo, áudio, tema ou instrução.")

    log(f"Tutorial mode: {mode}")

    # ---- 2. Get words + audio ----
    stage("legendas" if mode != "script" else "roteiro")
    words: list[dict] = []
    duration = 0.0
    audio_file = job_dir / "tutorial_audio.mp3"
    language = ""

    if mode == "facecam":
        duration = render.probe_duration(source)
        words, language = reels.transcribe_words(source, log)
        reels.extract_audio(source, audio_file)
        if not words:
            raise RuntimeError("Nenhuma fala detectada no vídeo.")

    elif mode == "audio":
        duration = tts_mod.audio_duration(source)
        words, language = reels.transcribe_words(source, log)
        shutil.copy(source, audio_file)
        if not words:
            raise RuntimeError("Nenhuma fala detectada no áudio.")

    elif mode == "script":
        stage("roteiro")
        subject = job.source.strip() or job.instruction.strip()
        try:
            material = ingest.ingest(job, job_dir, log)
        except Exception:
            material = ingest.SourceMaterial(kind="tema", text=subject)

        short_script = script_mod.build_script(job, material)
        title = title or short_script.title
        script_text = "\n\n".join(s.text for s in short_script.segments)

        stage("voz")
        voice: dict = {}
        if job.voice_id:
            voice_row = db.get_voice(job.voice_id)
            if voice_row:
                voice_settings = json.loads(voice_row.get("settings_json") or "{}")
                # Fish's text normalization is English-tuned — it mangles PT
                # accents and number reading. Off for Portuguese.
                if job.language.lower().startswith("pt"):
                    voice_settings.setdefault("normalize", False)
                voice = {
                    "provider": voice_row.get("provider", ""),
                    "provider_voice_id": voice_row.get("provider_voice_id", ""),
                    "settings_json": json.dumps(voice_settings),
                }
                log(f"Voice: {voice_row.get('name', job.voice_id)}")
            else:
                voice["provider_voice_id"] = job.voice_id
        narration = tts_mod.synthesize(
            script_text, audio_file,
            voice=voice, language=job.language, log=log)
        words = narration.words
        duration = narration.duration
        language = job.language

    # ---- 3. Plan storyboard ----
    if mode != "script":
        stage("roteiro")
    full_screen = mode != "facecam"
    storyboard = plan_storyboard(
        words, duration, instruction=job.instruction,
        language=job.language, full_screen=full_screen, log=log)
    (job_dir / "storyboard.json").write_text(
        json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
    title = title or storyboard.get("title", "Tutorial")

    # ---- 4. Fetch stock images ----
    stock_images = fetch_stock_images(storyboard, job_dir / "stock_images", log)

    # ---- 5+6. Render motion graphics and compose video ----
    stage("fundo")
    renderer = _pick_motion_renderer(log)

    if mode == "facecam":
        if renderer is not None:
            motion_video = job_dir / "motion_top.mp4"
            renderer(storyboard, stock_images, motion_video,
                     W, SPLIT_MOTION_H, FPS, duration, log)
            facecam = frame_facecam(source, duration, job_dir / "facecam.mp4")
            _vstack_videos(job_dir, motion_video, facecam, duration, log)
        else:
            panels = render_motion_panels(
                storyboard, job_dir / "motion_panels",
                panel_height=SPLIT_MOTION_H, stock_images=stock_images, log=log)
            facecam = frame_facecam(source, duration, job_dir / "facecam.mp4")
            compose_split_screen(job_dir, facecam, panels, duration, log)
        video_source = "split_screen.mp4"
    else:
        if renderer is not None:
            renderer(storyboard, stock_images, job_dir / "full_motion.mp4",
                     W, H, FPS, duration, log)
        else:
            panels = render_motion_panels(
                storyboard, job_dir / "motion_panels",
                panel_height=H, stock_images=stock_images, log=log)
            compose_full_motion(job_dir, panels, duration, log)
        video_source = "full_motion.mp4"

    # ---- 7. Build captions ----
    caption_words = words
    ass_path = captions_mod.build_ass(
        caption_words, job_dir / "captions.ass",
        style=job.caption_style, position=job.caption_position,
        title="", watermark=job.watermark,
        watermark_position=job.watermark_position,
        watermark_size=job.watermark_size,
        watermark_opacity=job.watermark_opacity)
    captions_mod.build_srt(caption_words, job_dir / "captions.srt")

    # ---- 8. Build timeline and render ----
    stage("render")
    edl = build_timeline(
        job_dir, duration, words,
        video_source=video_source,
        audio_source="tutorial_audio.mp3",
        caption_style=job.caption_style,
        caption_position=job.caption_position,
        watermark=job.watermark)
    timeline_mod.save(job_dir, edl)

    final = job_dir / "short.mp4"
    timeline_render.render_timeline(job_dir, edl, final, log=lambda m: log(m))
    render.make_thumbnail(final, job_dir / "thumb.jpg",
                          at=min(1.0, edl.duration / 4))

    # ---- 9. QA ----
    stage("qa")
    report = qa_mod.audit(final, ass_path, expected_duration=duration)
    log(f"QA: score {report.score}/100 — "
        f"{'PASSED' if report.passed else 'FAILED'}")

    shutil.copy(final, settings.outputs_dir / f"{job_id}.mp4")

    result = {
        "mode": MODE,
        "tutorial_mode": mode,
        "title": title,
        "description": storyboard.get("title", ""),
        "hashtags": [],
        "duration": round(edl.duration, 2),
        "video": f"/api/jobs/{job_id}/file/short.mp4",
        "thumbnail": f"/api/jobs/{job_id}/file/thumb.jpg",
        "captions_srt": f"/api/jobs/{job_id}/file/captions.srt",
        "words": words,
        "transcript_language": language,
        "source_kind": mode,
        "edit_mode": job.edit_mode,
        "storyboard": storyboard,
    }

    db.update_job(job_id, status="done", stage="qa", progress=1.0, title=title,
                  result_json=json.dumps(result, ensure_ascii=False),
                  qa_json=report.model_dump_json())
    notify.job_done(job_id, title, edl.duration, report.score, report.passed)
    return result
