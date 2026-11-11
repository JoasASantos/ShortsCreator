"""Motion graphics via HyperFrames — HTML + GSAP rendered to MP4.

HyperFrames (github.com/heygen-com/hyperframes, Apache 2.0) is HeyGen's
HTML-to-video engine: a composition is one HTML file driven by a *paused* GSAP
timeline registered on `window.__timelines`, and the CLI seeks that timeline
frame by frame in headless Chrome, encoding with FFmpeg. Output is SDR /
yuv420p 4:2:0 by default — mobile-safe without extra flags.

This module builds a HyperFrames composition from a tutorial storyboard: each
step becomes a scene (dark panel, stock image with Ken Burns, label, icons,
big words, lists, connections), and a GSAP timeline drives the entrances
(fade+scale ease-out, staggered pop-in, slow zoom). The CLI renders it.

It is the professional replacement for the hand-rolled `motion_html` seek()
loop: same idea, but GSAP + HyperFrames do the timing and the deterministic
capture, and the ~450-block registry + the `reference-to-motion` / `reel-export`
skills become reusable on top of the same composition format.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def available() -> bool:
    """True when the HyperFrames CLI is installed and answers."""
    if not shutil.which("npx"):
        return False
    try:
        out = subprocess.run(
            ["npx", "--no-install", "hyperframes", "--version"],
            capture_output=True, text=True, timeout=30)
        return out.returncode == 0
    except Exception:
        return False


def render_video(storyboard: dict, stock_images: dict[int, Path],
                 out_path: Path, width: int, height: int, fps: int,
                 duration: float, log=lambda m, level="info": None) -> Path:
    """Render the storyboard as an animated MP4 through HyperFrames."""
    project = out_path.parent / "hyperframes_project"
    assets = project / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    # hyperframes.json — minimal project config the CLI expects
    (project / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "paths": {"blocks": "compositions", "assets": "assets"},
        "media": {"autoProxy": True},
    }), encoding="utf-8")

    # Copy stock images into the project so the composition can reach them.
    local_images: dict[int, str] = {}
    for index, src in stock_images.items():
        if src and src.exists():
            dest = assets / f"stock_{index:03d}{src.suffix or '.jpg'}"
            shutil.copy(src, dest)
            local_images[index] = f"assets/{dest.name}"

    html = _build_composition(storyboard, local_images, width, height, fps, duration)
    (project / "index.html").write_text(html, encoding="utf-8")

    log(f"HyperFrames: rendering {duration:.1f}s @ {fps}fps ({width}x{height})")
    result = subprocess.run(
        ["npx", "--no-install", "hyperframes", "render", ".",
         "-o", str(out_path.resolve()),
         "-f", str(fps), "--sdr", "--quiet"],
        cwd=project, capture_output=True, text=True, timeout=1800)

    if result.returncode != 0 or not out_path.exists():
        tail = (result.stderr or result.stdout or "")[-600:]
        raise RuntimeError(f"HyperFrames render failed: {tail}")

    log(f"HyperFrames rendered: {out_path.name}")
    return out_path


# ---------------------------------------------------------------------------
#  Composition builder
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _split_label(label: str) -> str:
    """'Passo 2. Ação' -> italic prefix + bold action."""
    label = str(label or "")
    parts = label.split(".", 1)
    if len(parts) == 2 and any(c.isdigit() for c in parts[0]):
        return (f'<span class="num">{_esc(parts[0].strip())}.</span> '
                f'{_esc(parts[1].strip())}')
    return _esc(label)


def _scene_markup(index: int, step: dict, image: str) -> str:
    layout = str(step.get("layout") or "icons_row")
    icons = step.get("icons") or []
    big_words = step.get("big_words") or []
    items = step.get("items") or []
    label = str(step.get("label") or "")
    subtitle = str(step.get("subtitle") or "")

    body = ""
    if image:
        body += f'<div class="bg" data-pop style="background-image:url(\'{_esc(image)}\')"></div>'
    if label:
        body += f'<div class="label" data-label>{_split_label(label)}</div>'

    if layout == "big_text" and big_words:
        inner = "".join(
            f'<div class="word {"accent" if j == len(big_words)-1 else ""}" data-pop>{_esc(w)}</div>'
            for j, w in enumerate(big_words[:3]))
        body += f'<div class="big">{inner}</div>'
    elif layout == "highlight" and icons:
        ic = icons[0]
        body += (f'<div class="highlight" data-pop>'
                 f'<div class="emoji">{_esc(ic.get("emoji",""))}</div>'
                 f'<div class="hname">{_esc(ic.get("name",""))}</div></div>')
    elif layout == "list" and items:
        rows = "".join(
            f'<div class="row" data-pop><span class="dot"></span><span>{_esc(it)}</span></div>'
            for it in items[:6])
        body += f'<div class="list">{rows}</div>'
    elif layout == "list" and icons:
        rows = "".join(
            f'<div class="row" data-pop><span class="dot"></span>'
            f'<span>{_esc(ic.get("emoji",""))} {_esc(ic.get("name",""))}</span></div>'
            for ic in icons[:6])
        body += f'<div class="list">{rows}</div>'
    elif layout == "connection" and icons:
        nodes = []
        for j, ic in enumerate(icons[:4]):
            nodes.append(
                f'<div class="node" data-pop><div class="emoji">{_esc(ic.get("emoji",""))}</div>'
                f'<div class="nname">{_esc(ic.get("name",""))}</div></div>')
            if j < min(len(icons), 4) - 1:
                nodes.append('<div class="link" data-pop></div>')
        body += f'<div class="connect">{"".join(nodes)}</div>'
    else:
        cells = "".join(
            f'<div class="icon" data-pop><div class="emoji">{_esc(ic.get("emoji",""))}</div>'
            f'<div class="iname">{_esc(ic.get("name",""))}</div></div>'
            for ic in icons[:5])
        body += f'<div class="icons">{cells}</div>'

    if subtitle:
        body += f'<div class="subtitle" data-sub>{_esc(subtitle)}</div>'

    return f'<div class="scene" id="scene{index}" data-clip>{body}</div>'


def _timeline_js(scenes: list[dict]) -> str:
    """GSAP timeline that drives every scene's entrances deterministically."""
    lines = ['const tl = gsap.timeline({ paused: true });']
    for i, step in enumerate(scenes):
        start = float(step.get("start", 0.0))
        end = float(step.get("end", 0.0))
        dur = max(end - start, 0.1)
        sid = f"#scene{i}"
        # scene visible window (snap on, fade off at the tail)
        lines.append(f'tl.set("{sid}", {{ autoAlpha: 1 }}, {start:.3f});')
        lines.append(f'tl.set("{sid}", {{ autoAlpha: 0 }}, {end:.3f});')
        # staggered pop-in for marked elements
        lines.append(
            f'tl.fromTo("{sid} [data-pop]", '
            f'{{ opacity: 0, scale: 0.72, y: 22 }}, '
            f'{{ opacity: 1, scale: 1, y: 0, duration: 0.42, '
            f'stagger: 0.12, ease: "power3.out" }}, {start + 0.1:.3f});')
        # label slide-down
        lines.append(
            f'tl.fromTo("{sid} [data-label]", '
            f'{{ opacity: 0, y: -30 }}, '
            f'{{ opacity: 1, y: 0, duration: 0.4, ease: "power3.out" }}, {start:.3f});')
        # subtitle delayed fade
        lines.append(
            f'tl.fromTo("{sid} [data-sub]", {{ opacity: 0 }}, '
            f'{{ opacity: 1, duration: 0.4, ease: "power2.out" }}, {start + 0.25:.3f});')
        # Ken Burns on the background image
        lines.append(
            f'tl.fromTo("{sid} .bg", {{ scale: 1.06 }}, '
            f'{{ scale: 1.18, duration: {dur:.3f}, ease: "none" }}, {start:.3f});')
    lines.append('window.__timelines = window.__timelines || {};')
    lines.append('window.__timelines["main"] = tl;')
    lines.append('tl.seek(0);')
    return "\n      ".join(lines)


def _build_composition(storyboard: dict, images: dict[int, str],
                       width: int, height: int, fps: int, duration: float) -> str:
    steps = storyboard.get("steps") or []
    scenes_html = "\n      ".join(
        _scene_markup(i, s, images.get(i, "")) for i, s in enumerate(steps))
    timeline = _timeline_js(steps)

    return _TEMPLATE \
        .replace("__WIDTH__", str(width)) \
        .replace("__HEIGHT__", str(height)) \
        .replace("__FPS__", str(fps)) \
        .replace("__DURATION__", f"{duration:.3f}") \
        .replace("__SCENES__", scenes_html) \
        .replace("__TIMELINE__", timeline)


_TEMPLATE = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=__WIDTH__, height=__HEIGHT__" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"></script>
<style>
  :root { --ground:#0A0A0A; --ink:#ffffff; --muted:#CFCFCF; --accent:#F5A623; }
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body {
    width:__WIDTH__px; height:__HEIGHT__px; overflow:hidden;
    background:var(--ground); color:var(--ink);
    font-family:-apple-system,"Segoe UI","Helvetica Neue",Arial,sans-serif;
  }
  #root { position:relative; width:100%; height:100%; }
  .scene {
    position:absolute; inset:0; visibility:hidden; opacity:0;
    display:flex; flex-direction:column; align-items:center; justify-content:center;
  }
  .bg { position:absolute; inset:0; background-size:cover; background-position:center; }
  .bg::after { content:""; position:absolute; inset:0; background:rgba(10,10,10,0.62); }
  .label {
    position:absolute; top:90px; left:0; right:0; text-align:center;
    font-size:54px; font-weight:800; padding:0 60px; line-height:1.15; z-index:3;
  }
  .label .num { color:var(--muted); font-style:italic; font-weight:600; }
  .subtitle {
    position:absolute; bottom:120px; left:0; right:0; text-align:center;
    font-size:34px; color:var(--muted); padding:0 70px; z-index:3; font-weight:500;
  }
  .icons { display:flex; gap:60px; align-items:center; justify-content:center;
           flex-wrap:wrap; z-index:2; padding:0 40px; }
  .icon { text-align:center; }
  .icon .emoji { font-size:120px; line-height:1; filter:drop-shadow(0 6px 18px rgba(0,0,0,.5)); }
  .iname { font-size:30px; color:var(--muted); margin-top:16px; font-weight:600; }
  .big { text-align:center; z-index:2; }
  .big .word { font-size:150px; font-weight:900; line-height:1.02;
               text-shadow:0 8px 30px rgba(0,0,0,.6); letter-spacing:-2px; }
  .big .word.accent { color:var(--accent); }
  .list { z-index:2; width:78%; }
  .row { display:flex; align-items:center; gap:28px; font-size:40px; font-weight:700;
         margin:22px 0; background:rgba(255,255,255,.05); border-radius:18px; padding:22px 30px; }
  .row .dot { width:16px; height:16px; border-radius:50%; background:var(--accent); flex:none; }
  .highlight { text-align:center; z-index:2; }
  .highlight .emoji { font-size:200px; filter:drop-shadow(0 10px 30px rgba(0,0,0,.6)); }
  .hname { font-size:64px; font-weight:900; margin-top:10px; }
  .connect { display:flex; align-items:center; justify-content:center; z-index:2; padding:0 40px; }
  .node { text-align:center; }
  .node .emoji { font-size:96px; }
  .nname { font-size:26px; color:var(--muted); margin-top:10px; font-weight:600; }
  .link { width:90px; height:4px; margin:0 -4px 40px;
          background:repeating-linear-gradient(90deg,#888 0 10px,transparent 10px 20px); }
</style>
</head>
<body>
  <div id="root"
       data-composition-id="main"
       data-start="0"
       data-duration="__DURATION__"
       data-fps="__FPS__"
       data-width="__WIDTH__"
       data-height="__HEIGHT__">
      __SCENES__
  </div>
  <script>
      window.__timelines = window.__timelines || {};
      __TIMELINE__
  </script>
</body>
</html>
"""
