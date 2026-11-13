"""Motion graphics via HyperFrames — HTML + GSAP rendered to MP4.

HyperFrames (github.com/heygen-com/hyperframes, Apache 2.0) is HeyGen's
HTML-to-video engine: a composition is one HTML file driven by a *paused* GSAP
timeline on `window.__timelines`, and the CLI seeks that timeline frame by
frame in headless Chrome, encoding with FFmpeg. Output is SDR / yuv420p 4:2:0
by default — mobile-safe without extra flags.

The storyboard's `style` field picks one of four authored looks:

- **keynote** (default): dark tech stage #0a0a0a with a faint hairline grid,
  white #f5f5f5 type, grey #8b8b8b secondary, one accent #ff5a1f. Long-tail
  easing (expo.out ≈ cubic-bezier(.16,1,.3,1)), elements revealed by a light
  sweep (clip wipe), not fades. The `counter` layout counts one giant number
  up to its exact value. No glow, no particles.

- **hud**: technical HUD. Dark #050b0f field, cyan #38e8ff primary, amber
  #ffb000 for warnings/accent, dim #1d3540 grid. Monospace throughout. The
  `counter` scrambles through glyphs then locks to the true value; target
  brackets snap in. Thin lines, every label readable.

- **line**: single continuous line drawing. Warm paper #faf7f0, ink #1a1a1a,
  one accent #c1121f. Hand-drawn feel: underlines draw themselves, a closed
  element floods with the accent on a beat. Round caps, constant weight.

- **screencast**: a mock interface rebuilt in HTML/CSS (real copy, no
  screenshots) with a cursor that moves on eased curves, pauses and clicks with
  a ripple, while the camera zooms to 1.6x on the detail that matters.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

EASE = "expo.out"   # long-tail, ≈ cubic-bezier(.16,1,.3,1)
MOVE = 1.2          # seconds per reveal (~36 frames @ 30fps)

# Per-style palette + theme. `reveal` is the default entrance animation and
# `font` the type family; `grid` controls the hairline overlay.
STYLES: dict[str, dict] = {
    "keynote": {
        "ground": "#0a0a0a", "ink": "#f5f5f5", "muted": "#8b8b8b",
        "accent": "#ff5a1f", "grid": "rgba(255,255,255,0.035)", "grid_size": 120,
        "font": '-apple-system,"Segoe UI","Helvetica Neue",Arial,sans-serif',
        "reveal": "sweep", "counter": "count",
    },
    "hud": {
        "ground": "#050b0f", "ink": "#cdeef5", "muted": "#5f818c",
        "accent": "#ffb000", "primary": "#38e8ff", "grid": "#1d3540", "grid_size": 80,
        "font": '"SF Mono","JetBrains Mono","Fira Code",Consolas,monospace',
        "reveal": "scramble", "counter": "scramble",
    },
    "line": {
        "ground": "#faf7f0", "ink": "#1a1a1a", "muted": "#6b6b6b",
        "accent": "#c1121f", "grid": "rgba(0,0,0,0)", "grid_size": 0,
        "font": '"Georgia","Iowan Old Style",serif',
        "reveal": "draw", "counter": "count",
    },
    "screencast": {
        "ground": "#0a0a0a", "ink": "#f5f5f5", "muted": "#8b8b8b",
        "accent": "#ff5a1f", "grid": "rgba(255,255,255,0.035)", "grid_size": 120,
        "font": '-apple-system,"Segoe UI","Helvetica Neue",Arial,sans-serif',
        "reveal": "sweep", "counter": "count",
    },
}
DEFAULT_STYLE = "keynote"


def _style(storyboard: dict) -> dict:
    name = str(storyboard.get("style") or DEFAULT_STYLE).lower()
    return {**STYLES.get(name, STYLES[DEFAULT_STYLE]), "_name": name}


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

    (project / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "paths": {"blocks": "compositions", "assets": "assets"},
        "media": {"autoProxy": True},
    }), encoding="utf-8")

    local_images: dict[int, str] = {}
    for index, src in stock_images.items():
        if src and src.exists():
            dest = assets / f"stock_{index:03d}{src.suffix or '.jpg'}"
            shutil.copy(src, dest)
            local_images[index] = f"assets/{dest.name}"

    style = _style(storyboard)
    log(f"HyperFrames style: {style['_name']}")
    html = _build_composition(storyboard, local_images, style,
                              width, height, fps, duration)
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
#  Markup
# ---------------------------------------------------------------------------

def _esc(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _split_label(label: str) -> str:
    label = str(label or "")
    parts = label.split(".", 1)
    if len(parts) == 2 and any(c.isdigit() for c in parts[0]):
        return (f'<span class="num">{_esc(parts[0].strip())}.</span> '
                f'{_esc(parts[1].strip())}')
    return _esc(label)


def _num_target(step: dict) -> float:
    for key in ("number", "value", "stat"):
        v = step.get(key)
        if v is None:
            continue
        try:
            if isinstance(v, str):
                clean = v.replace(",", "").replace(".", "").strip()
                if clean.isdigit():
                    return float(clean)
            return float(v)
        except Exception:
            continue
    return 0.0


def _scene_markup(index: int, step: dict, image: str, style: dict) -> str:
    layout = str(step.get("layout") or "icons_row")
    icons = step.get("icons") or []
    big_words = step.get("big_words") or []
    items = step.get("items") or []
    label = str(step.get("label") or "")
    subtitle = str(step.get("subtitle") or "")
    accent_on = str(step.get("accent") or "")

    body = ""
    if image and layout not in ("counter", "screencast"):
        body += f'<div class="bg" data-reveal style="background-image:url(\'{_esc(image)}\')"></div>'
    if label:
        body += f'<div class="label" data-reveal>{_split_label(label)}</div>'

    if layout == "counter":
        target = _num_target(step)
        prefix = _esc(step.get("number_prefix") or "")
        suffix = _esc(step.get("number_suffix") or "")
        decimals = 1 if (target != int(target)) else 0
        bracket = ("hud" == style["_name"])
        body += (
            f'<div class="counter">'
            f'{"<span class=bk>[</span>" if bracket else ""}'
            f'<span class="cpre">{prefix}</span>'
            f'<span class="cnum accent" data-count="{target}" data-dec="{decimals}">0</span>'
            f'<span class="csuf">{suffix}</span>'
            f'{"<span class=bk>]</span>" if bracket else ""}</div>')
        caption = step.get("number_label") or (icons[0].get("name") if icons else "")
        if caption:
            body += f'<div class="ccap" data-reveal>{_esc(caption)}</div>'

    elif layout == "screencast":
        ui = step.get("ui") or {}
        ui_title = _esc(ui.get("title") or (icons[0].get("name") if icons else "App"))
        rows = ui.get("rows") or [ic.get("name", "") for ic in icons]
        click = int(ui.get("click_row", 0) or 0)
        row_html = ""
        for j, r in enumerate(rows[:5]):
            cls = "uirow active" if j == click else "uirow"
            row_html += f'<div class="{cls}" data-row="{j}"><span class="uidot"></span><span>{_esc(r)}</span></div>'
        body += (
            f'<div class="screencast" data-zoom>'
            f'<div class="window" data-reveal>'
            f'<div class="titlebar"><i></i><i></i><i></i>'
            f'<span class="wt">{ui_title}</span></div>'
            f'<div class="uibody">{row_html}</div></div>'
            f'<div class="cursor" data-cursor>➤</div>'
            f'<div class="ripple" data-ripple></div></div>')

    elif layout == "big_text" and big_words:
        inner = "".join(
            f'<div class="word {"accent" if (accent_on == w or (not accent_on and j == len(big_words)-1)) else ""}" data-reveal>'
            f'{_esc(w)}<span class="uline" data-draw></span></div>'
            for j, w in enumerate(big_words[:3]))
        body += f'<div class="big">{inner}</div>'

    elif layout == "highlight" and icons:
        ic = icons[0]
        body += (f'<div class="highlight" data-reveal>'
                 f'<div class="emoji">{_esc(ic.get("emoji",""))}</div>'
                 f'<div class="hname accent">{_esc(ic.get("name",""))}'
                 f'<span class="uline" data-draw></span></div></div>')

    elif layout == "list" and (items or icons):
        src = items if items else [f'{ic.get("emoji","")} {ic.get("name","")}' for ic in icons]
        rows = "".join(
            f'<div class="row" data-reveal><span class="dot"></span><span>{_esc(it)}</span></div>'
            for it in src[:6])
        body += f'<div class="list">{rows}</div>'

    elif layout == "connection" and icons:
        nodes = []
        n = min(len(icons), 4)
        for j, ic in enumerate(icons[:4]):
            nodes.append(
                f'<div class="node" data-reveal><div class="emoji">{_esc(ic.get("emoji",""))}</div>'
                f'<div class="nname">{_esc(ic.get("name",""))}</div></div>')
            if j < n - 1:
                nodes.append('<div class="link" data-draw></div>')
        body += f'<div class="connect">{"".join(nodes)}</div>'

    else:
        cells = "".join(
            f'<div class="icon" data-reveal><div class="emoji">{_esc(ic.get("emoji",""))}</div>'
            f'<div class="iname">{_esc(ic.get("name",""))}</div></div>'
            for ic in icons[:5])
        body += f'<div class="icons">{cells}</div>'

    if subtitle:
        body += f'<div class="subtitle" data-reveal>{_esc(subtitle)}</div>'

    return f'<div class="scene" id="scene{index}">{body}</div>'


# ---------------------------------------------------------------------------
#  Timeline
# ---------------------------------------------------------------------------

def _timeline_js(scenes: list[dict], style: dict) -> str:
    reveal = style["reveal"]
    counter_mode = style["counter"]
    lines = [
        'const tl = gsap.timeline({ paused: true });',
        # deterministic scramble glyphs (no Math.random — seek must be stable)
        'const GLYPHS = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&@";',
        'function scrambleText(real, p){ if(p>=1) return real; let out=""; '
        'for(let i=0;i<real.length;i++){ const c=real[i]; if(c===" "){out+=" ";continue;} '
        'const seed=Math.floor((p*97+i*13)*7)%GLYPHS.length; out+=GLYPHS[seed]; } return out; }',
    ]

    for i, step in enumerate(scenes):
        start = float(step.get("start", 0.0))
        end = float(step.get("end", 0.0))
        dur = max(end - start, 0.1)
        layout = str(step.get("layout") or "")
        sid = f"#scene{i}"

        lines.append(f'tl.set("{sid}", {{ autoAlpha: 1 }}, {start:.3f});')
        lines.append(f'tl.set("{sid}", {{ autoAlpha: 0 }}, {end:.3f});')

        # entrance reveal by style
        if reveal == "sweep":
            lines.append(
                f'tl.fromTo("{sid} [data-reveal]", '
                f'{{ clipPath: "inset(0 100% 0 0)", y: 16 }}, '
                f'{{ clipPath: "inset(0 0% 0 0)", y: 0, duration: {MOVE}, '
                f'stagger: 0.14, ease: "{EASE}" }}, {start + 0.08:.3f});')
        elif reveal == "scramble":
            # quick hard-in, then lock (brackets/labels snap); counter scrambles.
            lines.append(
                f'tl.fromTo("{sid} [data-reveal]", '
                f'{{ opacity: 0, y: 10 }}, '
                f'{{ opacity: 1, y: 0, duration: 0.5, stagger: 0.1, ease: "power2.out" }}, {start + 0.06:.3f});')
            lines.append(
                f'tl.fromTo("{sid} .bk", {{ opacity: 0, scale: 1.4 }}, '
                f'{{ opacity: 1, scale: 1, duration: 0.3, stagger: 0.12, ease: "steps(3)" }}, {start + 0.1:.3f});')
        elif reveal == "draw":
            lines.append(
                f'tl.fromTo("{sid} [data-reveal]", '
                f'{{ opacity: 0, y: 14 }}, '
                f'{{ opacity: 1, y: 0, duration: 0.7, stagger: 0.14, ease: "{EASE}" }}, {start + 0.08:.3f});')

        # underlines / links draw themselves (all styles, strongest in "line")
        lines.append(
            f'tl.fromTo("{sid} [data-draw]", {{ scaleX: 0 }}, '
            f'{{ scaleX: 1, duration: 0.6, transformOrigin: "left center", '
            f'stagger: 0.1, ease: "{EASE}" }}, {start + 0.5:.3f});')

        # background push
        lines.append(
            f'tl.fromTo("{sid} .bg", {{ scale: 1.05 }}, '
            f'{{ scale: 1.14, duration: {dur:.3f}, ease: "none" }}, {start:.3f});')

        if layout == "counter":
            lock = min(dur * 0.65, 2.0)
            if counter_mode == "scramble":
                lines.append(
                    f'{{ const el=document.querySelector("{sid} .cnum"); if(el){{ '
                    f'const tgt=parseFloat(el.dataset.count)||0; const dec=parseInt(el.dataset.dec)||0; '
                    f'const real=tgt.toLocaleString("pt-BR",{{minimumFractionDigits:dec,maximumFractionDigits:dec}}); '
                    f'const o={{p:0}}; tl.to(o,{{p:1,duration:{lock:.3f},ease:"power1.in",'
                    f'onUpdate:()=>{{ el.textContent=scrambleText(real,o.p); }} }},{start + 0.25:.3f}); }} }}')
            else:
                lines.append(
                    f'{{ const el=document.querySelector("{sid} .cnum"); if(el){{ '
                    f'const tgt=parseFloat(el.dataset.count)||0; const dec=parseInt(el.dataset.dec)||0; '
                    f'const o={{v:0}}; tl.to(o,{{v:tgt,duration:{lock:.3f},ease:"{EASE}",'
                    f'onUpdate:()=>{{ el.textContent=o.v.toLocaleString("pt-BR",'
                    f'{{minimumFractionDigits:dec,maximumFractionDigits:dec}}); }} }},{start + 0.2:.3f}); }} }}')

        if layout == "screencast":
            lines.append(
                f'tl.fromTo("{sid} [data-zoom]", {{ scale: 1 }}, '
                f'{{ scale: 1.6, duration: 1.0, ease: "{EASE}" }}, {start + 0.9:.3f});')
            lines.append(
                f'tl.to("{sid} [data-zoom]", {{ scale: 1, duration: 0.8, ease: "{EASE}" }}, {end - 0.9:.3f});')
            lines.append(
                f'tl.fromTo("{sid} [data-cursor]", '
                f'{{ left: "20%", top: "80%", opacity: 0 }}, '
                f'{{ left: "46%", top: "52%", opacity: 1, duration: 0.9, ease: "{EASE}" }}, {start + 0.3:.3f});')
            lines.append(
                f'tl.to("{sid} [data-cursor]", {{ scale: 0.82, duration: 0.12 }}, {start + 1.5:.3f});')
            lines.append(
                f'tl.to("{sid} [data-cursor]", {{ scale: 1, duration: 0.12 }}, {start + 1.62:.3f});')
            lines.append(
                f'tl.fromTo("{sid} [data-ripple]", '
                f'{{ left: "46%", top: "52%", scale: 0, opacity: 0.5 }}, '
                f'{{ scale: 3, opacity: 0, duration: 0.6, ease: "{EASE}" }}, {start + 1.5:.3f});')

    lines.append('window.__timelines = window.__timelines || {};')
    lines.append('window.__timelines["main"] = tl;')
    lines.append('tl.seek(0);')
    return "\n      ".join(lines)


def _build_composition(storyboard: dict, images: dict[int, str], style: dict,
                       width: int, height: int, fps: int, duration: float) -> str:
    steps = storyboard.get("steps") or []
    scenes_html = "\n      ".join(
        _scene_markup(i, s, images.get(i, ""), style) for i, s in enumerate(steps))
    timeline = _timeline_js(steps, style)
    primary = style.get("primary", style["accent"])
    return _TEMPLATE \
        .replace("__WIDTH__", str(width)).replace("__HEIGHT__", str(height)) \
        .replace("__FPS__", str(fps)).replace("__DURATION__", f"{duration:.3f}") \
        .replace("__GROUND__", style["ground"]).replace("__INK__", style["ink"]) \
        .replace("__MUTED__", style["muted"]).replace("__ACCENT__", style["accent"]) \
        .replace("__PRIMARY__", primary).replace("__FONT__", style["font"]) \
        .replace("__GRID__", style["grid"]).replace("__GRIDSIZE__", str(style["grid_size"])) \
        .replace("__STYLENAME__", style["_name"]) \
        .replace("__SCENES__", scenes_html).replace("__TIMELINE__", timeline)


_TEMPLATE = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=__WIDTH__, height=__HEIGHT__" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"></script>
<style>
  :root { --ground:__GROUND__; --ink:__INK__; --muted:__MUTED__;
          --accent:__ACCENT__; --primary:__PRIMARY__; }
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body {
    width:__WIDTH__px; height:__HEIGHT__px; overflow:hidden;
    background:var(--ground); color:var(--ink); font-family:__FONT__;
  }
  #grid {
    position:absolute; inset:0; pointer-events:none; z-index:0;
    background-image:
      linear-gradient(__GRID__ 1px, transparent 1px),
      linear-gradient(90deg, __GRID__ 1px, transparent 1px);
    background-size:__GRIDSIZE__px __GRIDSIZE__px;
  }
  #root { position:relative; width:100%; height:100%; z-index:1; }
  .scene { position:absolute; inset:0; visibility:hidden; opacity:0;
           display:flex; flex-direction:column; align-items:center; justify-content:center; }
  .bg { position:absolute; inset:0; background-size:cover; background-position:center; z-index:-1; }
  .bg::after { content:""; position:absolute; inset:0; background:rgba(5,8,10,0.70); }
  .label { position:absolute; top:96px; left:0; right:0; text-align:center;
           font-size:52px; font-weight:800; padding:0 60px; line-height:1.15; z-index:3;
           color:var(--ink); letter-spacing:-0.02em; }
  .label .num { color:var(--muted); font-style:italic; font-weight:600; }
  .subtitle { position:absolute; bottom:128px; left:0; right:0; text-align:center;
              font-size:33px; color:var(--muted); padding:0 70px; z-index:3; font-weight:500; }
  .icons { display:flex; gap:64px; align-items:center; justify-content:center;
           flex-wrap:wrap; z-index:2; padding:0 40px; }
  .icon .emoji { font-size:120px; line-height:1; }
  .iname { font-size:30px; color:var(--muted); margin-top:16px; font-weight:600; text-align:center; }
  .big { text-align:center; z-index:2; padding:0 50px; }
  .big .word { position:relative; font-size:150px; font-weight:900; line-height:1.0;
               letter-spacing:-3px; white-space:nowrap; color:var(--ink); }
  .big .word.accent { color:var(--accent); }
  .uline { position:absolute; left:0; bottom:-10px; width:100%; height:8px;
           background:var(--accent); border-radius:4px; display:block; }
  .counter { display:flex; align-items:baseline; justify-content:center; gap:10px; z-index:2; }
  .counter .cnum { font-size:400px; font-weight:900; line-height:0.9; letter-spacing:-12px; }
  .counter .cpre, .counter .csuf { font-size:120px; font-weight:800; color:var(--ink); }
  .counter .bk { font-size:300px; font-weight:400; color:var(--primary); }
  .counter .accent { color:var(--accent); }
  .ccap { position:absolute; bottom:300px; left:0; right:0; text-align:center;
          font-size:40px; color:var(--muted); font-weight:600; z-index:3; }
  .list { z-index:2; width:80%; }
  .row { display:flex; align-items:center; gap:28px; font-size:40px; font-weight:700;
         margin:20px 0; color:var(--ink); }
  .row .dot { width:14px; height:14px; border-radius:50%; background:var(--accent); flex:none; }
  .highlight { text-align:center; z-index:2; }
  .highlight .emoji { font-size:200px; }
  .hname { position:relative; font-size:66px; font-weight:900; margin-top:12px; display:inline-block; }
  .hname.accent { color:var(--accent); }
  .connect { display:flex; align-items:center; justify-content:center; z-index:2; padding:0 40px; }
  .node .emoji { font-size:96px; text-align:center; }
  .nname { font-size:26px; color:var(--muted); margin-top:10px; font-weight:600; text-align:center; }
  .link { width:90px; height:3px; margin:0 -4px 40px; background:var(--muted); opacity:0.6; }
  .screencast { position:relative; width:100%; height:100%;
                display:flex; align-items:center; justify-content:center; z-index:2; }
  .window { width:72%; background:#151515; border:1px solid #2a2a2a; border-radius:20px;
            overflow:hidden; box-shadow:0 30px 80px rgba(0,0,0,0.6); }
  .titlebar { display:flex; align-items:center; gap:10px; padding:20px 24px;
              background:#1c1c1c; border-bottom:1px solid #2a2a2a; }
  .titlebar i { width:16px; height:16px; border-radius:50%; background:#3a3a3a; display:block; }
  .titlebar .wt { margin-left:16px; font-size:28px; font-weight:700; color:var(--ink); }
  .uibody { padding:20px 0; }
  .uirow { display:flex; align-items:center; gap:22px; padding:26px 30px; font-size:38px;
           font-weight:600; color:var(--muted); }
  .uirow .uidot { width:34px; height:34px; border-radius:9px; background:#2a2a2a; flex:none; }
  .uirow.active { color:var(--ink); background:rgba(255,90,31,0.14); }
  .uirow.active .uidot { background:var(--accent); }
  .cursor { position:absolute; font-size:60px; color:var(--ink); z-index:5;
            transform:rotate(-20deg); filter:drop-shadow(0 3px 6px rgba(0,0,0,0.5)); }
  .ripple { position:absolute; width:80px; height:80px; border-radius:50%;
            border:4px solid var(--accent); z-index:4; margin:-40px 0 0 -40px; }

  /* ---- HUD theme tweaks ---- */
  body[data-style="hud"] .label { color:var(--primary); text-transform:uppercase; letter-spacing:2px; font-weight:700; }
  body[data-style="hud"] .label .num { color:var(--accent); }
  body[data-style="hud"] .iname, body[data-style="hud"] .nname { color:var(--primary); }
  body[data-style="hud"] .row .dot { background:var(--primary); border-radius:0; }
  body[data-style="hud"] .big .word { color:var(--primary); letter-spacing:0; font-weight:700; }
  body[data-style="hud"] .uline { background:var(--primary); height:4px; }
  body[data-style="hud"] .subtitle { color:var(--primary); opacity:0.7; }
  body[data-style="hud"] .link { background:var(--primary); height:1.5px; opacity:0.8; }

  /* ---- line (paper) theme tweaks ---- */
  body[data-style="line"] .bg::after { background:rgba(250,247,240,0.55); }
  body[data-style="line"] .label .num { color:var(--accent); font-style:italic; }
  body[data-style="line"] .row .dot { background:var(--accent); }
  body[data-style="line"] .link { background:var(--ink); height:4px; border-radius:2px; opacity:1; }
  body[data-style="line"] .uline { background:var(--accent); height:6px; }
  body[data-style="line"] .iname, body[data-style="line"] .nname, body[data-style="line"] .subtitle { color:var(--muted); }
</style>
</head>
<body data-style="__STYLENAME__">
  <div id="grid"></div>
  <div id="root"
       data-composition-id="main" data-start="0" data-duration="__DURATION__"
       data-fps="__FPS__" data-width="__WIDTH__" data-height="__HEIGHT__">
      __SCENES__
  </div>
  <script>
      window.__timelines = window.__timelines || {};
      __TIMELINE__
  </script>
</body>
</html>
"""
