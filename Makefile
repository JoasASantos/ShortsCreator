.PHONY: help setup api web dev doctor test test-fast typecheck i18n check clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:
	@echo "make setup     — cria venv, instala backend e frontend"
	@echo "make api       — sobe a API em :8000"
	@echo "make web       — sobe a interface em :3000"
	@echo "make dev       — sobe os dois juntos"
	@echo "make doctor    — checa dependências do sistema"
	@echo "make test      — roda os testes do backend"
	@echo "make test-fast — só os testes que não usam FFmpeg"
	@echo "make typecheck — checa os tipos do frontend"
	@echo "make i18n      — audita os dicionários dos 5 idiomas"
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
