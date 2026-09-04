.PHONY: help setup api web dev doctor test test-fast typecheck i18n check clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:
	@echo "make setup     — install everything: venv, backend, frontend, ffmpeg, .env"
	@echo "make api       — start the API on :8000"
	@echo "make web       — start the web UI on :3000"
	@echo "make dev       — start both together"
	@echo "make doctor    — report what is installed, what is missing and why"
	@echo "make test      — run the backend test suite"
	@echo "make test-fast — only the tests that do not need FFmpeg"
	@echo "make typecheck — check the frontend types"
	@echo "make i18n      — audit the dictionaries of all 5 languages"
	@echo "make check     — test + typecheck + i18n"

# Delegates to the script so Linux, macOS and Windows follow the same steps in
# the same order — the script also installs ffmpeg/yt-dlp, which make cannot do
# portably, and finishes by running the doctor.
setup:
	sh scripts/setup.sh

api:
	$(PY) -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --reload

web:
	cd web && npm run dev

dev:
	@$(MAKE) api & $(MAKE) web

# One implementation, three front doors: this target, the setup scripts and
# GET /api/system/requirements all print the same report.
doctor:
	@test -x $(PY) || { echo "no venv yet — run: make setup"; exit 1; }
	@cd backend && ../$(PY) -m app.pipeline.doctor

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
