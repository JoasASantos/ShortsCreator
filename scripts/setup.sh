#!/bin/sh
# ShortsCreator — one-shot setup for Linux and macOS.
#
#   sh scripts/setup.sh
#
# POSIX sh, no bashisms: the script has to run under dash on a Debian box and
# under whatever /bin/sh happens to be on macOS. It is idempotent — re-running
# it after fixing one missing dependency is the intended workflow, so every
# step checks before it acts.
#
# It never fails the whole run over one optional piece. Anything it could not
# do lands in $SKIPPED and gets printed at the end, next to the command you
# would type by hand, and then the doctor has the final word.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

VENV="$ROOT/.venv"
PY="$VENV/bin/python"
SKIPPED=""

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
info() { printf '    %s\n' "$1"; }
warn() { printf '    \033[33m! %s\033[0m\n' "$1"; }
skip() { SKIPPED="$SKIPPED
  $1"; warn "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------- platform

case "$(uname -s)" in
  Darwin) OS=macos ;;
  Linux)  OS=linux ;;
  *)      OS=unknown ;;
esac

MANAGER=""
if [ "$OS" = macos ]; then
  have brew && MANAGER=brew
else
  for m in apt-get dnf pacman zypper; do
    have "$m" && { MANAGER=$m; break; }
  done
fi

# Homebrew refuses to run as root and there is no flag that changes its mind,
# so on macOS-as-root we stop trying to install and print the commands instead.
# Doing otherwise means failing halfway with a half-built venv.
SUDO=""
AS_ROOT=no
[ "$(id -u)" = 0 ] && AS_ROOT=yes
if [ "$AS_ROOT" = no ] && [ "$OS" = linux ] && have sudo; then
  SUDO=sudo
fi

BREW_BLOCKED=no
if [ "$OS" = macos ] && [ "$AS_ROOT" = yes ]; then
  BREW_BLOCKED=yes
fi

say "ShortsCreator setup — $OS (package manager: ${MANAGER:-none detected})"
if [ "$BREW_BLOCKED" = yes ]; then
  warn "running as root on macOS: Homebrew will not run as root, so this"
  warn "script will NOT install system packages. Everything else still runs;"
  warn "the manual commands are printed at the end."
fi

# Installs one system package, or records why it could not. The package names
# are deliberately unquoted below: some distros split what Homebrew ships as a
# single formula (python3 + python3-venv), so an argument may be two names.
install_pkg() {
  formula=$1; apt=$2; dnf=$3; pac=$4; zyp=$5
  if [ "$BREW_BLOCKED" = yes ]; then
    skip "brew install $formula   (run it as your own user, not root)"
    return 0
  fi
  case "$MANAGER" in
    brew)    brew install "$formula" ;;
    apt-get) $SUDO apt-get update -qq && $SUDO apt-get install -y $apt ;;
    dnf)     $SUDO dnf install -y $dnf ;;
    pacman)  $SUDO pacman -S --needed --noconfirm $pac ;;
    zypper)  $SUDO zypper install -y $zyp ;;
    *)       skip "install $formula by hand — no supported package manager found" ;;
  esac
}

# ------------------------------------------------------------------- python

say "Python virtualenv"
PY_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
  have "$candidate" || continue
  # 3.11 is the floor: the backend uses X | Y unions and tomllib.
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY_BIN=$candidate
    break
  fi
done

if [ -z "$PY_BIN" ]; then
  warn "no Python 3.11+ found"
  install_pkg python@3.12 "python3 python3-venv" python3 python python3
  for candidate in python3.13 python3.12 python3.11 python3; do
    have "$candidate" && { PY_BIN=$candidate; break; }
  done
fi

if [ -z "$PY_BIN" ]; then
  skip "install Python 3.11+ and re-run this script"
elif [ -x "$PY" ]; then
  info "$VENV already exists — reusing it"
else
  info "creating $VENV with $PY_BIN"
  "$PY_BIN" -m venv "$VENV"
fi

if [ -x "$PY" ]; then
  say "Backend dependencies"
  "$PY" -m pip install --upgrade pip --quiet
  "$PY" -m pip install -r backend/requirements.txt
else
  skip "python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt"
fi

# --------------------------------------------------------------------- node

say "Node and the web dependencies"
if have node && have npm; then
  info "node $(node --version), npm $(npm --version)"
else
  warn "node/npm missing"
  install_pkg node "nodejs npm" nodejs "nodejs npm" "nodejs npm"
fi

if have npm; then
  ( cd web && npm install )
else
  skip "install Node 20+ and run: cd web && npm install"
fi

# ------------------------------------------------------------------- ffmpeg

say "FFmpeg"
if have ffmpeg && have ffprobe; then
  info "$(ffmpeg -version 2>/dev/null | head -1)"
else
  install_pkg ffmpeg ffmpeg ffmpeg ffmpeg ffmpeg
fi

# libass is checked apart from ffmpeg because the binary being present says
# nothing about the build carrying the `ass` filter — Homebrew's does not. The
# pipeline has a PNG-overlay fallback, so this is a note, not an error.
if have ffmpeg; then
  if ffmpeg -hide_banner -filters 2>/dev/null | awk '{print $2}' | grep -qx ass; then
    info "libass present — captions burn in one pass"
  else
    warn "this FFmpeg build has no libass: captions fall back to PNG overlays"
    warn "(slower render, same finished video). To get libass, see: make doctor"
  fi
fi

# -------------------------------------------------------------------- yt-dlp

say "yt-dlp"
if have yt-dlp; then
  info "yt-dlp $(yt-dlp --version 2>/dev/null)"
elif [ -x "$PY" ] && "$PY" -c 'import yt_dlp' 2>/dev/null; then
  # requirements.txt already installed it into the venv; the console script is
  # only on PATH while the venv is active, and the pipeline imports it anyway.
  info "yt-dlp available inside the venv"
else
  if [ -x "$PY" ]; then
    "$PY" -m pip install --upgrade yt-dlp
  else
    skip "install yt-dlp (pip install yt-dlp, or your package manager)"
  fi
fi

# ----------------------------------------------------------------------- env

say "Configuration file"
if [ -f .env ]; then
  info ".env already exists — left untouched"
elif [ -f .env.example ]; then
  cp .env.example .env
  info "created .env from .env.example — fill in the keys you need"
else
  skip "no .env.example to copy from"
fi

# -------------------------------------------------------------------- doctor

say "Checking what is still missing"
if [ -x "$PY" ]; then
  ( cd backend && "$PY" -m app.pipeline.doctor ) || true
else
  warn "no venv yet, so the doctor cannot run. Fix the items above and re-run."
fi

if [ -n "$SKIPPED" ]; then
  printf '\n\033[1m==> Not done automatically — run these yourself:\033[0m%s\n' "$SKIPPED"
  if [ "$BREW_BLOCKED" = yes ]; then
    printf '\n    You are root. Open a shell as your normal user first:\n'
    printf '      su - <your-user>\n'
    printf '      cd %s && sh scripts/setup.sh\n' "$ROOT"
  fi
fi

printf '\nDone. Fill in .env, then: make dev\n'
