# OpenPath developer commands. Works on Windows (GNU make + cmd), macOS and Linux.
# Override the bootstrap interpreter with, for example, `make setup PYTHON=python3`.

ifeq ($(OS),Windows_NT)
PYTHON ?= py -3.12
VENV_PY := .venv\Scripts\python.exe
else
PYTHON ?= python3.12
VENV_PY := .venv/bin/python
endif

# Every requirements file CI installs: the shared ones and any a story adds.
REQUIREMENTS := $(sort $(wildcard cloud/requirements*.txt cloud/tests/requirements*.txt vision/requirements*.txt vision/tests/requirements*.txt))

# The web app (web/) comes from OP-113. Web steps are skipped until it is here.
HAS_WEB := $(wildcard web/package.json)

.PHONY: setup dev web test lint format build types graph lrt record worker

setup:  ## Create .venv (cloud + vision) and install the web app's dependencies
	$(PYTHON) -m venv .venv
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install $(addprefix -r ,$(REQUIREMENTS))
ifneq ($(HAS_WEB),)
	cd web && npm ci
endif

dev:  ## API on :8000 and web app on :3000, from recorded fixtures with no keys
	$(VENV_PY) scripts/dev.py

web:  ## Only the web app on :3000, pointed at the API on :8000
	$(VENV_PY) scripts/dev.py web

test:  ## pytest for cloud and vision, plus web lint and typecheck
	$(VENV_PY) -m pytest
ifneq ($(HAS_WEB),)
	cd web && npm run lint
	cd web && npm run typecheck
endif

lint:  ## ruff check and format check, plus web lint (what CI runs)
	$(VENV_PY) -m ruff check .
	$(VENV_PY) -m ruff format --check .
ifneq ($(HAS_WEB),)
	cd web && npm run lint
endif

format:  ## Fix lint problems ruff can fix, then format Python
	$(VENV_PY) -m ruff check --fix .
	$(VENV_PY) -m ruff format .

ifneq ($(HAS_WEB),)
build:  ## Production build of the web app
	cd web && npm run build

types:  ## Regenerate web/src/lib/api-types.ts from the API's OpenAPI schema
	$(VENV_PY) scripts/export_openapi.py web/openapi.json
	cd web && npx --yes openapi-typescript@7.13.0 openapi.json -o src/lib/api-types.ts
else
build types:
	@echo make $@ needs web/, which OP-113 adds && exit 1
endif

graph:  ## Rebuild cloud/data/network.npz with OSMnx (laptop only; commit the result)
	$(VENV_PY) -m pip install -r scripts/requirements-graph.txt
	$(VENV_PY) scripts/build_graph.py

lrt:  ## Rebuild cloud/data/lrt.json from the ETS GTFS schedule (one official download; commit the result)
	$(VENV_PY) scripts/build_lrt_graph.py

record:  ## Record a replay snapshot of every source (OP-305)
	@echo make record is not built yet: OP-305 adds scripts/record_snapshot.py && exit 1

worker:  ## Run the vision worker locally (OP-204)
	$(VENV_PY) -m vision.worker
