# Cadless — dev tasks
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
# Overridable so CI can run these targets against its own ruff: make lint RUFF=ruff
RUFF ?= $(VENV)/bin/ruff

# The Python source directories, and the single source of truth for what ruff
# covers -- CI runs these targets rather than repeating the list.
# catalog/ is excluded on purpose: it is hand-authored build123d content whose
# step scripts open with `from build123d import *`. Adding a new top-level
# Python directory means adding it here.
# Note the targets also reach README files inside these directories, because
# ruff formats Python code blocks in Markdown.
PY_DIRS := cadless tests backend worker tools scripts

.PHONY: help venv install test test-all lint fmt clean up down smoke logs

help:
	@echo "make install   - create venv and install package + dev deps"
	@echo "make test      - run unit tests (skips live-model calls, not geometry)"
	@echo "make test-all  - run the full suite incl. the live-model tests"
	@echo "make lint      - ruff check + format check (what CI runs)"
	@echo "make fmt       - ruff format + autofix"

venv:
	python3 -m venv $(VENV)

install: venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"

# Default test run excludes live-API calls so it works in CI without creds.
test:
	$(PY) -m pytest -m "not bedrock and not anthropic and not openai"

test-all:
	$(PY) -m pytest

lint:
	$(RUFF) check $(PY_DIRS)
	$(RUFF) format --check $(PY_DIRS)

fmt:
	$(RUFF) format $(PY_DIRS)
	$(RUFF) check --fix $(PY_DIRS)

clean:
	rm -rf $(VENV) .pytest_cache **/__pycache__ build dist *.egg-info

# ---- Docker PoC stack ----
up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f

# One-command bring-up + end-to-end smoke.
smoke: up
	bash scripts/smoke.sh
