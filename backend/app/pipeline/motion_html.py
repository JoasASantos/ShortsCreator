"""HTML motion graphics rendered to MP4 — inspired by claude-motion.

Instead of static Pillow PNGs, each storyboard step becomes an animated HTML
scene: icons fade in and scale up, labels slide, stock images do a slow Ken
Burns zoom, big words pop. A single HTML page exposes a deterministic
`window.seek(t)` function that positions every element for the time `t`;
headless Chromium (Playwright) steps through the whole timeline frame by frame,
and FFmpeg encodes the frames into a video.

This keeps rendering deterministic — no CSS-animation/clock drift — so a frame
at `t` is identical every run. The motion vocabulary (fade+scale entrances,
ease-out curves, 300-500ms, Ken Burns on photos) follows the tutorial style
sheet.

Falls back cleanly: if Playwright or Chromium are missing, the caller renders
the static Pillow panels instead.
"""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

from . import render


def available() -> bool:
    """True when Playwright + a Chromium build are usable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def _data_uri(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except Exception:
        return ""
    ext = path.suffix.lower().lstrip(".") or "jpeg"
    if ext == "jpg":
        ext = "jpeg"
    return f"data:image/{ext};base64," + base64.b64encode(raw).decode("ascii")


def build_html(storyboard: dict, stock_images: dict[int, Path],
               width: int, height: int, duration: float) -> str:
    """One self-contained HTML page: all scenes + a deterministic seek()."""
    scenes = []
    for index, step in enumerate(storyboard.get("steps") or []):
        img = stock_images.get(index)
        scenes.append({
            "start": float(step.get("start", 0.0)),
            "end": float(step.get("end", 0.0)),
            "label": str(step.get("label") or ""),
            "subtitle": str(step.get("subtitle") or ""),
            "layout": str(step.get("layout") or "icons_row"),
            "icons": step.get("icons") or [],
            "big_words": step.get("big_words") or [],
            "items": step.get("items") or [],
            "image": _data_uri(img) if img else "",
        })

    scenes_json = json.dumps(scenes, ensure_ascii=False)

    return _HTML_TEMPLATE.replace("__WIDTH__", str(width)) \
        .replace("__HEIGHT__", str(height)) \
        .replace("__DURATION__", f"{duration:.3f}") \
        .replace("__SCENES__", scenes_json)


def render_video(storyboard: dict, stock_images: dict[int, Path],
                 out_path: Path, width: int, height: int, fps: int,
                 duration: float, log=lambda m, level="info": None) -> Path:
    """Render the storyboard as an animated MP4 via headless Chromium."""
    from playwright.sync_api import sync_playwright

    work = out_path.parent / "motion_html_frames"
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("*.jpg"):
        old.unlink()

    html = build_html(storyboard, stock_images, width, height, duration)
    html_file = out_path.parent / "motion_scene.html"
    html_file.write_text(html, encoding="utf-8")

    total_frames = max(int(round(duration * fps)), 1)
    log(f"HTML motion: capturing {total_frames} frames ({duration:.1f}s @ {fps}fps)")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=["--force-color-profile=srgb",
                                 "--disable-blink-features=AutomationControlled"])
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=1)
        page.goto(html_file.as_uri(), timeout=20000)
        page.wait_for_function("typeof window.seek === 'function'", timeout=10000)

        for frame in range(total_frames):
            t = frame / fps
            page.evaluate(f"window.seek({t})")
            page.screenshot(path=str(work / f"f_{frame:05d}.jpg"),
                            type="jpeg", quality=90)

        browser.close()

    render._run([
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", str(work / "f_%05d.jpg"),
        "-vf", (f"scale={width}:{height}:in_range=full:out_range=tv,"
                f"format=yuv420p"),
        "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-pix_fmt", "yuv420p", "-color_range", "tv",
        str(out_path),
    ])

    for f in work.glob("*.jpg"):
        f.unlink()
    log(f"HTML motion rendered: {out_path.name}")
    return out_path


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body {
    width: __WIDTH__px; height: __HEIGHT__px;
    background: #0A0A0A; overflow: hidden;
    font-family: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    color: #fff;
  }
  #stage { position: relative; width: 100%; height: 100%; }
  .scene {
    position: absolute; inset: 0;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    opacity: 0; will-change: opacity, transform;
  }
  .scene .bg {
    position: absolute; inset: 0;
    background-size: cover; background-position: center;
    will-change: transform;
  }
  .scene .bg::after {
    content: ""; position: absolute; inset: 0;
    background: rgba(10,10,10,0.62);
  }
  .label {
    position: absolute; top: 90px; left: 0; right: 0;
    text-align: center; font-size: 52px; font-weight: 800;
    padding: 0 60px; line-height: 1.15; z-index: 3;
  }
  .label .num { color: #CCCCCC; font-style: italic; font-weight: 600; }
  .subtitle {
    position: absolute; bottom: 120px; left: 0; right: 0;
    text-align: center; font-size: 34px; color: #D8D8D8;
    padding: 0 70px; z-index: 3; font-weight: 500;
  }
  .icons {
    display: flex; gap: 60px; align-items: center; justify-content: center;
    flex-wrap: wrap; z-index: 2; padding: 0 40px;
  }
  .icon { text-align: center; }
  .icon .emoji { font-size: 110px; line-height: 1; filter: drop-shadow(0 6px 18px rgba(0,0,0,0.5)); }
  .icon .name { font-size: 30px; color: #CFCFCF; margin-top: 16px; font-weight: 600; }
  .big { text-align: center; z-index: 2; }
  .big .word {
    font-size: 150px; font-weight: 900; line-height: 1.02;
    text-shadow: 0 8px 30px rgba(0,0,0,0.6); letter-spacing: -2px;
  }
  .big .word.accent { color: #F5A623; }
  .list { z-index: 2; width: 78%; }
  .list .row {
    display: flex; align-items: center; gap: 28px;
    font-size: 40px; font-weight: 700; margin: 22px 0;
    background: rgba(255,255,255,0.05); border-radius: 18px;
    padding: 22px 30px;
  }
  .list .row .dot {
    width: 16px; height: 16px; border-radius: 50%;
    background: #F5A623; flex: none;
  }
  .highlight { text-align: center; z-index: 2; }
  .highlight .emoji { font-size: 190px; filter: drop-shadow(0 10px 30px rgba(0,0,0,0.6)); }
  .highlight .name { font-size: 64px; font-weight: 900; margin-top: 10px; }
  .connect {
    display: flex; align-items: center; justify-content: center;
    gap: 0; z-index: 2; padding: 0 40px;
  }
  .connect .node { text-align: center; }
  .connect .node .emoji { font-size: 92px; }
  .connect .node .name { font-size: 26px; color: #CFCFCF; margin-top: 10px; font-weight: 600; }
  .connect .link {
    width: 90px; height: 4px; margin: 0 -4px 40px;
    background: repeating-linear-gradient(90deg,#888 0 10px,transparent 10px 20px);
  }
</style>
</head>
<body>
<div id="stage"></div>
<script>
const WIDTH = __WIDTH__, HEIGHT = __HEIGHT__, DURATION = __DURATION__;
const SCENES = __SCENES__;
const stage = document.getElementById('stage');

function esc(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

// Build each scene's DOM once; seek() only toggles opacity/transform.
const sceneEls = SCENES.map((sc, i) => {
  const el = document.createElement('div');
  el.className = 'scene';
  let inner = '';
  if (sc.image) inner += `<div class="bg" style="background-image:url('${sc.image}')"></div>`;

  // label with "Passo N." prefix split
  if (sc.label) {
    const m = sc.label.split('.');
    if (m.length >= 2 && /\d/.test(m[0])) {
      inner += `<div class="label"><span class="num">${esc(m[0])}.</span> ${esc(m.slice(1).join('.').trim())}</div>`;
    } else {
      inner += `<div class="label">${esc(sc.label)}</div>`;
    }
  }

  const layout = sc.layout;
  if (layout === 'big_text' && sc.big_words && sc.big_words.length) {
    inner += `<div class="big">` + sc.big_words.map((w,j)=>
      `<div class="word ${j===sc.big_words.length-1?'accent':''}" data-pop="${j}">${esc(w)}</div>`).join('') + `</div>`;
  } else if (layout === 'highlight' && sc.icons && sc.icons.length) {
    const ic = sc.icons[0];
    inner += `<div class="highlight"><div class="emoji">${esc(ic.emoji||'')}</div><div class="name">${esc(ic.name||'')}</div></div>`;
  } else if (layout === 'list' && (sc.items && sc.items.length)) {
    inner += `<div class="list">` + sc.items.map((it,j)=>
      `<div class="row" data-pop="${j}"><span class="dot"></span><span>${esc(it)}</span></div>`).join('') + `</div>`;
  } else if (layout === 'list' && sc.icons && sc.icons.length) {
    inner += `<div class="list">` + sc.icons.map((ic,j)=>
      `<div class="row" data-pop="${j}"><span class="dot"></span><span>${esc(ic.emoji||'')} ${esc(ic.name||'')}</span></div>`).join('') + `</div>`;
  } else if (layout === 'connection' && sc.icons && sc.icons.length) {
    let parts = [];
    sc.icons.forEach((ic,j)=>{
      parts.push(`<div class="node" data-pop="${j}"><div class="emoji">${esc(ic.emoji||'')}</div><div class="name">${esc(ic.name||'')}</div></div>`);
      if (j < sc.icons.length-1) parts.push(`<div class="link"></div>`);
    });
    inner += `<div class="connect">${parts.join('')}</div>`;
  } else {
    // icons_row / tree / comparison fallback → row of icons
    inner += `<div class="icons">` + (sc.icons||[]).map((ic,j)=>
      `<div class="icon" data-pop="${j}"><div class="emoji">${esc(ic.emoji||'')}</div><div class="name">${esc(ic.name||'')}</div></div>`).join('') + `</div>`;
  }

  if (sc.subtitle) inner += `<div class="subtitle" data-sub="1">${esc(sc.subtitle)}</div>`;
  el.innerHTML = inner;
  stage.appendChild(el);
  return el;
});

function easeOut(x){ return 1 - Math.pow(1 - x, 3); }

window.seek = function(t){
  for (let i = 0; i < SCENES.length; i++) {
    const sc = SCENES[i], el = sceneEls[i];
    const inScene = t >= sc.start && t < sc.end;
    if (!inScene) { el.style.opacity = '0'; el.style.display = 'none'; continue; }
    el.style.display = 'flex';

    const local = t - sc.start;
    const dur = Math.max(sc.end - sc.start, 0.01);
    // scene fade in (0.35s) and fade out (last 0.3s)
    const fin = Math.min(local / 0.35, 1);
    const fout = Math.min((sc.end - t) / 0.3, 1);
    el.style.opacity = String(Math.min(fin, fout, 1));

    // Ken Burns on bg image
    const bg = el.querySelector('.bg');
    if (bg) {
      const p = Math.min(local / dur, 1);
      bg.style.transform = `scale(${1.06 + 0.10 * p})`;
    }

    // staggered pop-in for icons/words/rows/nodes
    el.querySelectorAll('[data-pop]').forEach(node => {
      const j = parseInt(node.getAttribute('data-pop')) || 0;
      const delay = 0.12 + j * 0.13;
      const prog = easeOut(Math.max(0, Math.min((local - delay) / 0.4, 1)));
      node.style.opacity = String(prog);
      node.style.transform = `scale(${0.7 + 0.3 * prog}) translateY(${(1-prog)*20}px)`;
    });

    // label slide-down
    const label = el.querySelector('.label');
    if (label) {
      const prog = easeOut(Math.min(local / 0.4, 1));
      label.style.opacity = String(prog);
      label.style.transform = `translateY(${(1-prog)*-30}px)`;
    }
    const sub = el.querySelector('[data-sub]');
    if (sub) {
      const prog = easeOut(Math.max(0, Math.min((local - 0.2) / 0.4, 1)));
      sub.style.opacity = String(prog);
    }
  }
};
window.seek(0);
</script>
</body>
</html>
"""
