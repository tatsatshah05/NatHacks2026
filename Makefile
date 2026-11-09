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

.PHONY: setup dev web test lint format build types e2e graph lrt bus record worker bench demo-check

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

e2e:  ## Playwright end-to-end tests: API in replay mode + production web build, then stop both (OP-406)
	$(VENV_PY) scripts/e2e.py $(E2E_FLAGS)
else
build types e2e:
	@echo make $@ needs web/, which OP-113 adds && exit 1
endif

graph:  ## Rebuild cloud/data/network.npz with OSMnx (laptop only; commit the result)
	$(VENV_PY) -m pip install -r scripts/requirements-graph.txt
	$(VENV_PY) scripts/build_graph.py

lrt:  ## Rebuild cloud/data/lrt.json from the ETS GTFS schedule (one official download; commit the result)
	$(VENV_PY) scripts/build_lrt_graph.py

bus:  ## Rebuild cloud/data/bus.json, the ETS bus lines for walk and bus trips (OP-417; one official download; commit the result)
	$(VENV_PY) scripts/build_bus_graph.py

record:  ## Record a replay snapshot of every source into snapshots/<UTC time>/ (OP-305, no images)
	$(VENV_PY) scripts/record_snapshot.py $(RECORD_FLAGS)

worker:  ## Run the vision worker locally (OP-204)
	$(VENV_PY) -m vision.worker

bench:  ## Measure /api/route latency: 20 trips x 4 profiles against the API on :8000 (OP-404; BENCH_FLAGS="--base-url https://..." for another)
	$(VENV_PY) scripts/route_bench.py $(BENCH_FLAGS)

demo-check:  ## Check every demo dependency, ok or FAIL per line (OP-402); DEMO_CHECK_FLAGS="--mode replay --api ... --web ..."
	$(VENV_PY) scripts/demo_check.py $(DEMO_CHECK_FLAGS)
