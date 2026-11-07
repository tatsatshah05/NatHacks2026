# OpenPath developer commands. Works on Windows (GNU make + cmd), macOS and Linux.
# Override the bootstrap interpreter with, for example, `make setup PYTHON=python3`.

ifeq ($(OS),Windows_NT)
PYTHON ?= py -3.12
VENV_PY := .venv\Scripts\python.exe
else
PYTHON ?= python3.12
VENV_PY := .venv/bin/python
endif

.PHONY: setup dev test lint format build types graph record worker

setup:  ## Create .venv (cloud + vision) and install web dependencies
	$(PYTHON) -m venv .venv
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -r cloud/requirements-dev.txt -r vision/requirements.txt
	cd web && npm ci

dev:  ## API on :8000 (SQLite, recorded fixtures, no keys) + web on :3000
	$(VENV_PY) scripts/dev.py

test:  ## pytest for cloud and vision, plus web lint and typecheck
	$(VENV_PY) -m pytest
	cd web && npm run lint
	cd web && npm run typecheck

lint:  ## ruff check and format check, plus web lint
	$(VENV_PY) -m ruff check .
	$(VENV_PY) -m ruff format --check .
	cd web && npm run lint

format:  ## Auto-format Python
	$(VENV_PY) -m ruff format .
	$(VENV_PY) -m ruff check --fix .

build:  ## Production build of the web app
	cd web && npm run build

types:  ## Regenerate web/src/lib/api-types.ts from the API's OpenAPI schema
	$(VENV_PY) scripts/export_openapi.py web/openapi.json
	cd web && npx openapi-typescript openapi.json -o src/lib/api-types.ts

graph:  ## Rebuild the walking network with OSMnx (OP-104)
	@echo make graph is not built yet: OP-104 adds scripts/build_graph.py && exit 1

record:  ## Record a replay snapshot of every source (OP-305)
	@echo make record is not built yet: OP-305 adds scripts/record_snapshot.py && exit 1

worker:  ## Run the vision worker locally (OP-204)
	@echo make worker is not built yet: OP-204 adds vision/worker.py && exit 1
