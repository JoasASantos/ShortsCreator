.PHONY: help setup api web dev doctor test test-fast typecheck i18n check clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:
	@echo "make setup     — create the venv, install backend and frontend"
	@echo "make api       — start the API on :8000"
	@echo "make web       — start the web UI on :3000"
	@echo "make dev       — start both together"
	@echo "make doctor    — check the system dependencies"
	@echo "make test      — run the backend test suite"
	@echo "make test-fast — only the tests that do not need FFmpeg"
	@echo "make typecheck — check the frontend types"
	@echo "make i18n      — audit the dictionaries of all 5 languages"
	@echo "make check     — test + typecheck + i18n"

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements.txt
	cd web && npm install
	@test -f .env || cp .env.example .env
	@echo "Pronto. Preencha o .env e rode: make dev"

api:
	$(PY) -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --reload

web:
	cd web && npm run dev

dev:
	@$(MAKE) api & $(MAKE) web

doctor:
	@printf "ffmpeg   : "; command -v ffmpeg >/dev/null && ffmpeg -version | head -1 || echo "AUSENTE"
	@printf "ffprobe  : "; command -v ffprobe >/dev/null && echo ok || echo "AUSENTE"
	@printf "libass   : "; ffmpeg -hide_banner -filters 2>/dev/null | grep -q " ass " && echo "presente" || echo "ausente (usa fallback PNG)"
	@printf "python   : "; $(PY) --version 2>/dev/null || echo "venv ausente — rode make setup"
	@printf "node     : "; node --version 2>/dev/null || echo "AUSENTE"
	@printf "fontes   : "; ls assets/fonts/*.tt* 2>/dev/null | wc -l | tr -d ' '
	@printf "trilhas  : "; ls assets/music/*.mp3 2>/dev/null | wc -l | tr -d ' '
	@printf "claude   : "; command -v claude >/dev/null && claude --version 2>/dev/null || echo "ausente (elo Fable/Opus da cadeia)"
	@printf "codex    : "; command -v codex >/dev/null && echo ok || echo "ausente (elo GPT-5.6 da cadeia)"
	@printf "espaco   : "; df -h . | tail -1 | awk '{print $$4" livres"}'

test:
	cd backend && ../$(PY) -m pytest

test-fast:
	cd backend && ../$(PY) -m pytest -k "not ffmpeg" -q

typecheck:
	cd web && npx tsc --noEmit -p .

i18n:
	cd web && node lib/i18n/check.mjs

check: test typecheck i18n

clean:
	rm -rf data/jobs/* data/cache/* data/outputs/*
	rm -rf backend/.pytest_cache
