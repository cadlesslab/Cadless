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

.PHONY: help venv install lock test test-all lint fmt clean up down smoke logs

help:
	@echo "make install   - create venv and install package + dev deps"
	@echo "make lock      - regenerate constraints.txt (needs docker)"
	@echo "make test      - run unit tests (skips live-model calls, not geometry)"
	@echo "make test-all  - run the full suite incl. the live-model tests"
	@echo "make lint      - ruff check + format check (what CI runs)"
	@echo "make fmt       - ruff format + autofix"

venv:
	python3 -m venv $(VENV)

install: venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"

# Regenerate constraints.txt, the single resolution the images and CI install.
#
# It runs inside the images' own base rather than in your venv: a resolution
# taken on macOS can name a wheel that has no linux build, and the file would
# then break the very builds it exists to fix.
#
# --platform is load-bearing rather than tidy. build123d's requirements split on
# the machine — linux/aarch64 takes py-lib3mf and everything else takes lib3mf —
# and pip-compile drops the marker, emitting only the branch it resolved in. A
# lock made on an Apple Silicon machine therefore leaves lib3mf unconstrained in
# every image CI and the deploy host actually build. What the platform must
# match is where the file is installed, never where it is generated.
#
# The consequence on any other machine: the branch that does not apply is inert
# and its counterpart floats unpinned, so an image built on Apple Silicon is
# locked except for that one package. Both resolve, and the architecture that
# ships is the one held exactly.
#
# The workdir is mounted read-only and the output comes back over stdout, so the
# file stays owned by you. It lands in a temp file and is moved into place only
# on success: truncating the committed lock and then failing on a missing daemon
# or an unsatisfiable resolve would leave the tree worse than not running at all.
#
# pip-compile's own header is suppressed because it names the container's
# temporary output path, so a reader following it would write the file somewhere
# else. tools/lock_header.py writes one that names this target instead, and
# records the declarations the resolution came from.
#
# pip-tools is pinned here rather than declared in [dev]: nothing imports it,
# and installing it for every contributor and every CI run to serve one target
# is the cost this file exists to avoid. Pinned because an unpinned generator
# would reintroduce the problem one level up.
PIP_TOOLS ?= pip-tools==7.6.1
LOCK_PLATFORM ?= linux/amd64

lock:
	docker run --rm --platform $(LOCK_PLATFORM) -v "$(CURDIR)":/w:ro -w /w \
	  python:3.12-slim sh -c \
	  'pip install -q $(PIP_TOOLS) && pip-compile --quiet --no-header --strip-extras \
	   --extra web --extra dev --output-file /tmp/pins.txt pyproject.toml && \
	   python tools/lock_header.py $(LOCK_PLATFORM) && cat /tmp/pins.txt' \
	  > constraints.txt.tmp
	mv constraints.txt.tmp constraints.txt

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
