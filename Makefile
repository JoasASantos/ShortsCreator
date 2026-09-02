.PHONY: help setup api web dev doctor clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:
	@echo "make setup   — cria venv, instala backend e frontend"
	@echo "make api     — sobe a API em :8000"
	@echo "make web     — sobe a interface em :3000"
	@echo "make dev     — sobe os dois juntos"
	@echo "make doctor  — checa dependências do sistema"

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

clean:
	rm -rf data/jobs/* data/cache/* data/outputs/*
