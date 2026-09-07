"""Requirements doctor — what is installed, what is missing, what it costs.

`make doctor`, `python -m app.pipeline.doctor` and
`GET /api/system/requirements` all read this module, so there is a single
answer to "is this machine ready?" instead of three that drift apart.

Two rules shape the output:

* REQUIRED and OPTIONAL are a real distinction, not decoration. Only a missing
  required item means the pipeline cannot run; a missing optional one costs one
  named feature and says which, because "install faster-whisper" is useless
  advice next to "without it you cannot caption your own recordings".
* a credential is reported as present or absent and nothing else. Not the
  value, not a masked prefix — a prefix still leaks entropy, and the only place
  a secret belongs is the gitignored `.env`.

The libass check is separate from the ffmpeg check on purpose. FFmpeg being on
PATH says nothing about whether the `ass` filter exists in that build, and
Homebrew's ships without it. The pipeline survives it (`overlays.py` renders
the captions to PNG and composites them instead), so it is optional — but it is
a slower render with less exact karaoke, and the user deserves to know which
of the two paths their machine is on before they wonder why their captions
look different from the screenshots.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field

from ..config import settings

# The renders are tens of MB each and the b-roll cache holds stock clips of the
# same order. This project has filled a disk before; warn while there is still
# room to react.
MIN_FREE_GB = 5.0

MIN_PYTHON = (3, 11)
MIN_NODE = 20

# Probing a binary must never hang the health route.
PROBE_TIMEOUT = 8.0

# The health route polls every few seconds and every check here shells out.
CACHE_TTL = 45.0


@dataclass
class Requirement:
    id: str
    label: str
    required: bool
    found: bool
    version: str = ""
    unlocks: str = ""
    install: str = ""
    # What is lost by not having it, or how the pipeline copes without it.
    note: str = ""

    def as_dict(self) -> dict:
        data = asdict(self)
        data["level"] = "required" if self.required else "optional"
        return data


@dataclass
class _Env:
    """The host, resolved once per report."""

    os: str = ""              # macos | linux | windows | unknown
    manager: str = ""         # brew | apt | dnf | pacman | winget | choco | ""
    machine: str = field(default_factory=platform.machine)


# ------------------------------------------------------------------ platform

def platform_id() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    if os.name == "nt" or sys.platform.startswith("win"):
        return "windows"
    return "unknown"


def package_manager(host: str = "") -> str:
    """The manager the install hints should be written for.

    Detected rather than assumed: an Arch box and a Debian box get different
    commands, and printing the wrong one is worse than printing none.
    """
    host = host or platform_id()
    if host == "macos":
        return "brew" if shutil.which("brew") else ""
    if host == "linux":
        for manager in ("apt", "dnf", "pacman", "zypper"):
            if shutil.which(manager):
                return manager
        return ""
    if host == "windows":
        for manager in ("winget", "choco"):
            if shutil.which(manager):
                return manager
        return ""
    return ""


# Install commands per manager. The "*" entry is the fallback when no manager
# was detected — a link beats a command for a package manager the user lacks.
_INSTALL: dict[str, dict[str, str]] = {
    "python": {
        "brew": "brew install python@3.12",
        "apt": "sudo apt install -y python3 python3-venv",
        "dnf": "sudo dnf install -y python3",
        "pacman": "sudo pacman -S python",
        "zypper": "sudo zypper install python3",
        "winget": "winget install Python.Python.3.12",
        "choco": "choco install python",
        "*": "https://www.python.org/downloads/",
    },
    "ffmpeg": {
        "brew": "brew install ffmpeg",
        "apt": "sudo apt install -y ffmpeg",
        "dnf": "sudo dnf install -y ffmpeg",
        "pacman": "sudo pacman -S ffmpeg",
        "zypper": "sudo zypper install ffmpeg",
        "winget": "winget install Gyan.FFmpeg.Full",
        "choco": "choco install ffmpeg-full",
        "*": "https://ffmpeg.org/download.html",
    },
    # A build with libass, which is not the same package as plain ffmpeg
    # everywhere: Homebrew's bottle drops it, so macOS needs the tap that still
    # accepts build options. Distro builds and the "full" Windows builds carry
    # it already, so there the answer is the ordinary reinstall.
    "ffmpeg_libass": {
        "brew": "brew tap homebrew-ffmpeg/ffmpeg && "
                "brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass",
        "apt": "sudo apt install -y ffmpeg libass9",
        "dnf": "sudo dnf install -y ffmpeg libass",
        "pacman": "sudo pacman -S ffmpeg libass",
        "zypper": "sudo zypper install ffmpeg libass",
        "winget": "winget install Gyan.FFmpeg.Full",
        "choco": "choco install ffmpeg-full",
        "*": "https://ffmpeg.org/download.html (pick a build with --enable-libass)",
    },
    "node": {
        "brew": "brew install node",
        "apt": "sudo apt install -y nodejs npm",
        "dnf": "sudo dnf install -y nodejs",
        "pacman": "sudo pacman -S nodejs npm",
        "zypper": "sudo zypper install nodejs npm",
        "winget": "winget install OpenJS.NodeJS.LTS",
        "choco": "choco install nodejs-lts",
        "*": "https://nodejs.org/en/download",
    },
    "claude": {
        "*": "npm install -g @anthropic-ai/claude-code  (then: claude login)",
    },
    "codex": {
        "*": "npm install -g @openai/codex  (then: codex login)",
    },
}

_PIP = ".venv/bin/pip install -r backend/requirements.txt"
_PIP_WIN = ".venv\\Scripts\\pip install -r backend\\requirements.txt"


def _install_hint(key: str, env: _Env) -> str:
    table = _INSTALL.get(key, {})
    return table.get(env.manager) or table.get("*", "")


def _pip_hint(env: _Env) -> str:
    return _PIP_WIN if env.os == "windows" else _PIP


# -------------------------------------------------------------------- probes

def _clean_version(binary: str, line: str) -> str:
    """"ffmpeg version 8.1.1 Copyright (c) 2000-2026 ..." -> "8.1.1".

    The banner is longer than the terminal is wide and only the number is a
    fact anyone acts on.
    """
    name = os.path.basename(binary)
    for cut in (" Copyright", " (c) "):
        head, sep, _ = line.partition(cut)
        if sep:
            line = head
    prefix = f"{name} version "
    if line.lower().startswith(prefix.lower()):
        line = line[len(prefix):]
    return line.strip()


def _probe(binary: str, *args: str) -> tuple[str, str]:
    """(resolved path, its version, trimmed). Both empty when absent.

    Anything the binary does wrong — missing, not executable, hanging, writing
    to stderr — degrades to "found but version unknown" rather than raising:
    the doctor is what people run *because* the machine is broken.
    """
    path = shutil.which(binary)
    if not path:
        return "", ""
    try:
        proc = subprocess.run([path, *(args or ("--version",))],
                              capture_output=True, text=True,
                              timeout=PROBE_TIMEOUT)
        out = (proc.stdout or proc.stderr).strip().splitlines()
        return path, _clean_version(binary, out[0]) if out else ""
    except (OSError, subprocess.SubprocessError):
        return path, ""


def _module_version(name: str) -> str:
    """Installed distribution version without importing the package.

    Importing faster-whisper pulls in ctranslate2 and a few hundred MB of
    tokenizer machinery; the doctor only needs to know it is installed.
    """
    from importlib import metadata

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return ""


def _node_major(version: str) -> int:
    digits = version.lstrip("v").split(".")[0]
    return int(digits) if digits.isdigit() else 0


def free_gb(path=None) -> float:
    """Free space where the renders land, not where the repo lives."""
    target = path or settings.data_dir
    try:
        return shutil.disk_usage(target).free / 1_000_000_000
    except OSError:
        return -1.0


# ------------------------------------------------------------ LLM readiness

def provider_ready(provider: str) -> bool:
    """CLI providers depend on the logged-in binary, not on an API key."""
    if provider == "claude_cli":
        return bool(shutil.which(settings.claude_cli_bin))
    if provider == "codex_cli":
        return bool(shutil.which(settings.codex_cli_bin))
    if provider == "ollama":
        return True
    return bool(settings.anthropic_api_key or settings.openai_api_key)


def llm_ready() -> bool:
    if settings.llm_provider == "chain":
        # A single usable link is enough — that is what the chain is for. It
        # reads the chain in force rather than the one .env described at boot,
        # since the model can be changed from the interface.
        from . import llm as llm_mod

        return any(provider_ready(p) for p, _ in llm_mod.active_chain())
    return provider_ready(settings.llm_provider)


def _configured(connector_id: str, *fallbacks: str) -> bool:
    """Is a credential registered — from the Accounts screen or from .env?

    Goes through the connector catalog so the doctor agrees with what the
    Accounts screen shows (the database wins over .env there). The fallbacks
    cover keys read straight from `settings` with no connector behind them.
    """
    try:
        from . import connectors

        if connectors.is_configured(connector_id):
            return True
    except Exception:  # noqa: BLE001 — no database yet, or a broken catalog
        pass
    return any(bool(value) for value in fallbacks)


# -------------------------------------------------------------------- checks

def _python(env: _Env) -> Requirement:
    ok = sys.version_info >= MIN_PYTHON
    return Requirement(
        id="python", label="Python 3.11+", required=True, found=ok,
        version=platform.python_version(),
        unlocks="the whole backend — FastAPI, the pipeline, the tests",
        install=_install_hint("python", env),
        note="" if ok else f"found {platform.python_version()}, "
                           f"needs {'.'.join(map(str, MIN_PYTHON))} or newer",
    )


def _ffmpeg(env: _Env) -> Requirement:
    path, version = _probe("ffmpeg")
    return Requirement(
        id="ffmpeg", label="FFmpeg", required=True, found=bool(path),
        version=version,
        unlocks="every render, the QA audit and the timeline recompile",
        install=_install_hint("ffmpeg", env),
        note="" if path else "nothing renders without it",
    )


def _ffprobe(env: _Env) -> Requirement:
    path, version = _probe("ffprobe")
    return Requirement(
        id="ffprobe", label="ffprobe", required=True, found=bool(path),
        version=version,
        unlocks="reading duration, resolution and codecs — the QA audit reads "
                "the finished file through it",
        install=_install_hint("ffmpeg", env),
        note="" if path else "ships with FFmpeg; a build without it is unusual",
    )


def _libass(env: _Env, ffmpeg_found: bool) -> Requirement:
    """Whether *this* FFmpeg build has the `ass` filter, not whether it exists.

    Kept apart from the ffmpeg check because the two answers differ often
    enough to matter, and because the consequence is a different code path
    rather than a failure.
    """
    found = False
    if ffmpeg_found:
        try:
            from . import render

            found = render.has_filter("ass")
        except Exception:  # noqa: BLE001 — odd build, unreadable filter list
            found = False
    return Requirement(
        id="ffmpeg_libass", label="FFmpeg with libass (the `ass` filter)",
        required=False, found=found,
        version="filter present" if found else "",
        unlocks="captions burned in a single pass, with per-word karaoke "
                "straight from the .ass file",
        install=_install_hint("ffmpeg_libass", env),
        note="" if found else
             "captions still work: the pipeline falls back to the PNG overlays "
             "in overlays.py. One extra compositing pass per caption, so a "
             "slower render — nothing is lost from the finished video",
    )


def _ytdlp(env: _Env) -> Requirement:
    path, version = _probe("yt-dlp")
    if not path:
        # requirements.txt installs it as a library; the console script is only
        # on PATH while the venv is active, and the pipeline imports it either
        # way.
        version = _module_version("yt-dlp")
        found = bool(version)
        if found:
            version = f"{version} (python package)"
    else:
        found = True
    return Requirement(
        id="ytdlp", label="yt-dlp", required=True, found=found, version=version,
        unlocks="ingesting a video from a link — YouTube, TikTok, Instagram, "
                "and the Batch screen's long source video",
        install=_pip_hint(env),
        note="" if found else
             "only link ingestion breaks; a topic, an article or an uploaded "
             "file still work",
    )


def _node(env: _Env) -> Requirement:
    path, version = _probe("node")
    major = _node_major(version)
    ok = bool(path) and major >= MIN_NODE
    return Requirement(
        id="node", label=f"Node {MIN_NODE}+", required=True, found=ok,
        version=version,
        unlocks="the web interface (Next.js)",
        install=_install_hint("node", env),
        note="" if ok else (
            f"found {version}, needs {MIN_NODE} or newer" if path
            else "the API works headless, but there is no interface"),
    )


def _npm(env: _Env) -> Requirement:
    path, version = _probe("npm")
    return Requirement(
        id="npm", label="npm", required=True, found=bool(path), version=version,
        unlocks="installing the frontend dependencies (`make setup`)",
        install=_install_hint("node", env),
    )


def _edge_tts(env: _Env) -> Requirement:
    version = _module_version("edge-tts")
    return Requirement(
        id="edge_tts", label="edge-tts", required=True, found=bool(version),
        version=version,
        unlocks="the default narration voice, free, plus the per-word timing "
                "the karaoke captions are built from",
        install=_pip_hint(env),
        note="" if version else
             "with no TTS at all there is no narration; ElevenLabs, fish.audio "
             "or a local XTTS replace it if you have one configured",
    )


def _faster_whisper(env: _Env) -> Requirement:
    version = _module_version("faster-whisper")
    return Requirement(
        id="faster_whisper", label="faster-whisper", required=False,
        found=bool(version), version=version,
        unlocks="transcription: captioning your own recording, the Live Cuts "
                "screen, and condensing a long video into 60s",
        install=_pip_hint(env),
        note="" if version else
             "without faster-whisper you cannot caption your own recordings "
             "and Live Cuts has nothing to cut on. Generating a short from a "
             "topic, an article or a repository is unaffected",
    )


def _claude_cli(env: _Env) -> Requirement:
    path, version = _probe(settings.claude_cli_bin)
    return Requirement(
        id="claude_cli", label=f"Claude Code CLI (`{settings.claude_cli_bin}`)",
        required=False, found=bool(path), version=version,
        unlocks="writing scripts on a Claude Pro/Max subscription instead of a "
                "per-token key — the first two links of the default chain",
        install=_install_hint("claude", env),
        note="" if path else
             "the chain falls through to the next link. With no link at all "
             "and no API key, nothing gets written",
    )


def _codex_cli(env: _Env) -> Requirement:
    # Not on PATH is the normal case for this one: it is the last resort of the
    # chain and plenty of installs never add it. Probing `settings.codex_cli_bin`
    # rather than the literal "codex" respects CODEX_CLI_BIN pointing somewhere
    # else entirely.
    path, version = _probe(settings.codex_cli_bin)
    return Requirement(
        id="codex_cli", label=f"Codex CLI (`{settings.codex_cli_bin}`)",
        required=False, found=bool(path), version=version,
        unlocks="the last link of the chain, on a ChatGPT Plus/Pro "
                "subscription — what answers when the Claude links hit their "
                "limit",
        install=_install_hint("codex", env),
        note="" if path else
             "not on PATH. The chain simply stops one link earlier, which "
             "only costs you anything on the day the Claude links are "
             "exhausted",
    )


def _llm(env: _Env) -> Requirement:
    ready = llm_ready()
    provider = settings.llm_provider
    if provider == "chain":
        links = [f"{p}:{m}" if m else p for p, m in settings.llm_chain]
        detail = f"chain: {', '.join(links)}" if links else "chain: empty"
    else:
        detail = provider
    return Requirement(
        id="llm", label="A usable language model", required=True, found=ready,
        version=detail,
        unlocks="writing the script, the alternative hooks, the post copy and "
                "picking the moments in a long video",
        install="a CLI above (claude / codex) or an API key in .env "
                "(ANTHROPIC_API_KEY, OPENAI_API_KEY)",
        note="" if ready else
             f"LLM_PROVIDER={provider} has no usable path: no CLI on PATH and "
             f"no API key set. The 'roteiro' source type still works — it "
             f"narrates text you wrote yourself, with no model involved",
    )


def _disk(env: _Env) -> Requirement:
    gb = free_gb()
    ok = gb < 0 or gb >= MIN_FREE_GB
    return Requirement(
        id="disk", label=f"Free disk space ({MIN_FREE_GB:.0f} GB+)",
        required=True, found=ok,
        version="unknown" if gb < 0 else f"{gb:.1f} GB free in {settings.data_dir}",
        unlocks="room for the renders, the b-roll cache and the Whisper models",
        install="make clean  (drops data/jobs, data/cache and data/outputs)",
        note="" if ok else
             "a render that runs out of space dies mid-encode and leaves a "
             "truncated MP4 that QA then rejects for the wrong reason",
    )


def _tts_cloning(env: _Env) -> Requirement:
    found = _configured("elevenlabs", settings.elevenlabs_api_key) or \
        _configured("fishaudio", settings.fishaudio_api_key)
    return Requirement(
        id="tts_keys", label="Character voice key (ElevenLabs / fish.audio)",
        required=False, found=found,
        version="configured" if found else "",
        unlocks="cloned and character voices, and ElevenLabs' per-character "
                "caption timestamps",
        install="Accounts screen, or ELEVENLABS_API_KEY / FISHAUDIO_API_KEY in .env",
        note="" if found else
             "edge-tts already narrates for free in several languages — this "
             "only buys you a specific voice",
    )


def _stock_video(env: _Env) -> Requirement:
    found = (_configured("pexels", settings.pexels_api_key)
             or _configured("pixabay", settings.pixabay_api_key)
             or _configured("coverr", settings.coverr_api_key))
    return Requirement(
        id="broll_keys", label="Stock video key (Pexels / Pixabay / Coverr)",
        required=False, found=found,
        version="configured" if found else "",
        unlocks="the automatic b-roll background, footage matched per segment",
        install="Accounts screen, or PEXELS_API_KEY / PIXABAY_API_KEY / "
                "COVERR_API_KEY in .env",
        note="" if found else
             "the background falls back to the animated gradient, or to media "
             "the job brings itself (video, photos, generated clip)",
    )


def _assets(env: _Env) -> list[Requirement]:
    """Fonts and music tracks: gitignored folders, so a fresh clone has neither."""
    fonts = sorted(p for p in (settings.assets_dir / "fonts").glob("*")
                   if p.suffix.lower() in (".ttf", ".otf", ".ttc"))
    music = sorted(p for p in (settings.assets_dir / "music").glob("*")
                   if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg"))
    return [
        Requirement(
            id="fonts", label="Caption fonts (assets/fonts)", required=False,
            found=bool(fonts), version=f"{len(fonts)} file(s)" if fonts else "",
            unlocks="the project's caption typeface instead of the system default",
            install="drop a .ttf/.otf into assets/fonts/",
            note="" if fonts else
                 "captions still render — FFmpeg falls back to a system font, "
                 "so the look drifts from the screenshots",
        ),
        Requirement(
            id="music", label="Music tracks (assets/music)", required=False,
            found=bool(music), version=f"{len(music)} track(s)" if music else "",
            unlocks="the background track mixed under the narration",
            install="drop an .mp3 into assets/music/",
            note="" if music else
                 "shorts come out with narration only; the music toggle has "
                 "nothing to pick from",
        ),
    ]


# -------------------------------------------------------------------- report

def check() -> dict:
    """The full report, probed fresh.

    Ordered the way a person reads it: what must work, then what each optional
    piece would add.
    """
    host = platform_id()
    env = _Env(os=host, manager=package_manager(host))

    ffmpeg = _ffmpeg(env)
    items: list[Requirement] = [
        _python(env),
        ffmpeg,
        _ffprobe(env),
        _libass(env, ffmpeg.found),
        _ytdlp(env),
        _node(env),
        _npm(env),
        _edge_tts(env),
        _llm(env),
        _disk(env),
        _faster_whisper(env),
        _claude_cli(env),
        _codex_cli(env),
        _tts_cloning(env),
        _stock_video(env),
        *_assets(env),
    ]

    missing_required = [r.id for r in items if r.required and not r.found]
    missing_optional = [r.id for r in items if not r.required and not r.found]
    return {
        "ok": not missing_required,
        "platform": {"os": env.os, "machine": env.machine,
                     "package_manager": env.manager},
        "checks": [r.as_dict() for r in items],
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "min_free_gb": MIN_FREE_GB,
    }


_CACHE: tuple[float, dict] | None = None


def cached_check(ttl: float = CACHE_TTL) -> dict:
    """For callers on a timer. /api/health is polled every few seconds and
    every check here spawns a process; re-probing on each poll would cost more
    than the route it decorates."""
    global _CACHE

    now = time.monotonic()
    if _CACHE is not None and now - _CACHE[0] < ttl:
        return _CACHE[1]
    report = check()
    _CACHE = (now, report)
    return report


def summary() -> dict:
    """The compact form the sidebar health readout can carry for free."""
    report = cached_check()
    return {
        "ok": report["ok"],
        "missing_required": report["missing_required"],
        "missing_optional": report["missing_optional"],
    }


# ----------------------------------------------------------------------- CLI

_MARK = {True: "ok     ", False: "MISSING"}


def render_text(report: dict) -> str:
    """`make doctor` output. Plain ASCII: this runs on the terminal that is
    already having a bad day."""
    lines: list[str] = []
    host = report["platform"]
    manager = host["package_manager"] or "none detected"
    lines.append(f"ShortsCreator doctor — {host['os']}/{host['machine']} "
                 f"(package manager: {manager})")

    for level, title in (("required", "REQUIRED"), ("optional", "OPTIONAL")):
        rows = [c for c in report["checks"] if c["level"] == level]
        lines.append("")
        lines.append(title)
        for c in rows:
            mark = _MARK[c["found"]] if level == "required" else \
                ("ok     " if c["found"] else "absent ")
            version = f"  {c['version']}" if c["version"] else ""
            lines.append(f"  [{mark}] {c['label']}{version}")
            if c["found"]:
                continue
            if c["note"]:
                lines.append(f"            {c['note']}")
            lines.append(f"            unlocks: {c['unlocks']}")
            if c["install"]:
                lines.append(f"            install: {c['install']}")

    lines.append("")
    if report["ok"]:
        lines.append("All required dependencies are in place.")
    else:
        lines.append("Missing and needed: " + ", ".join(report["missing_required"]))
    if report["missing_optional"]:
        lines.append("Optional, not installed (nothing is broken): "
                     + ", ".join(report["missing_optional"]))
    return "\n".join(lines)


def main() -> int:
    report = check()
    print(render_text(report))
    # Non-zero only for a missing requirement, so `make doctor` stays usable in
    # CI without an absent optional key failing the build.
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
