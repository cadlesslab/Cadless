# Cadless — PoC Runbook

How to build, run, and smoke-test the containerized stack.

## Prerequisites
- Docker + Docker Compose v2
- Credentials for your chosen LLM provider — the default is **Anthropic**
  (`ANTHROPIC_API_KEY`); OpenAI or AWS Bedrock work too (Bedrock needs AWS
  credentials with access in `us-east-1`). The **api** calls the configured
  provider; the **worker** never does.

## Configure
```bash
cp .env.example .env
# edit .env: pick CADLESS_LLM_PROVIDER (anthropic | openai | bedrock) and set
# that provider's credentials — ANTHROPIC_API_KEY / OPENAI_API_KEY, or for
# bedrock AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (or the ~/.aws mount
# option documented in docker-compose.yml)
```

## Run
```bash
./start.sh start   # build (if needed) + up + wait for health   (or: make up)
./start.sh stop    # stop, keep data volume
./start.sh restart
```

The stack is served under **`/apps/cadless`** by a bundled Caddy. Only that proxy
is published, on `CADLESS_PROXY_PORT` (default **8800**). Override per-run if that
port is already in use:
```bash
CADLESS_PROXY_PORT=9000 ./start.sh start
```

## Access

**Public (the goal):** `https://<your-domain>/apps/cadless/` — once the platform
Caddy that owns `<your-domain>` is told to forward `/apps/cadless*` to this stack.
Add the one-time route to `/etc/caddy/Caddyfile` (single box → `localhost:8800`)
and reload Caddy; full block + safe apply/rollback in
[`infra/proxy/README.md`](infra/proxy/README.md).

**Local on the host:** `http://localhost:8800/apps/cadless/`

**From your laptop without the public route:**
- Tailscale (or any private-network address for the host): `http://<host-ip>:8800/apps/cadless/`
- SSH tunnel: `ssh -L 8800:localhost:8800 <user>@<host>` → `http://localhost:8800/apps/cadless/`

The app has **no authentication** (single-user PoC) — keep `:8800` off the public
internet; only the platform Caddy (TLS) should be public. The browser loads the
SPA from `/apps/cadless/` and calls `/apps/cadless/api/*`; the bundled Caddy strips
the prefix and routes to the api/frontend containers (none are published
directly).

```bash
make logs      # tail logs
make down      # stop + remove (incl. volumes)
```

## Smoke test (end to end)
```bash
make smoke     # brings the stack up, then runs scripts/smoke.sh
```
The smoke test creates a project, generates a part from a natural-language
prompt (configured provider → worker → STEP/GLB), and verifies both artifacts download.
Exits non-zero on any failure.

## Architecture / isolation
- `frontend` (nginx) serves the SPA; the bundled Caddy routes `/api/*` to `api`.
- `api` (FastAPI) orchestrates: calls the configured LLM provider for code-gen, delegates **execution**
  to the worker via `CADLESS_WORKER_URL`, persists projects/versions/artifacts.
- `worker` runs the LLM-generated build123d code. It is on an **internal-only**
  network (`backnet`, no egress), runs **non-root** with a **read-only** root FS
  (only `/data` + `/tmp`), and is capped by cgroup limits (`mem_limit` 1 GB,
  `cpus`, `pids_limit`) — this is the real memory cap.
- `api` + `worker` share the `data` volume so artifacts written by the worker are
  served by the api.

## PoC security posture
For a PoC, isolation is "good enough", not hardened: shared-kernel
containers, no gVisor/Firecracker. Do not expose this to untrusted users as-is.

## Cutting a release
The backend and the frontend are released separately, each by merging its own
release pull request ([ADR-0010](docs/adr/0010-release-pull-request.md)). A
change under `frontend/` releases the frontend; any other change releases the
backend; a change touching both releases both.

1. Once a change that releases has landed on `main` (see the table in
   [CONTRIBUTING.md](CONTRIBUTING.md#pull-request-titles)), the release workflow
   opens a pull request for each component it touched — titled
   `chore(main): release backend X.Y.Z` or `chore(main): release frontend X.Y.Z`
   — carrying that component's next version and its changelog since its last
   release, and rebuilds it from `main` on every run — so merging one
   component's release pull request never leaves the other's in conflict on the
   manifest.
2. A maintainer reads the changelog — it is built from the merged pull
   requests, whose text their authors can still edit — and squash-merges the
   pull request when that release should ship.
3. The run that merge starts tags `backend-vX.Y.Z` or `frontend-vX.Y.Z`,
   creates its GitHub Release as a draft with that changelog, attaches what the
   release carries (below), and only then publishes it.

### What a release carries

Every release is built from the commit its tag names: its files by the workflow
that builds both components as a dry run on every pull request, and the
backend's images while the release is still a draft. Nothing is attached or
pushed until the tag is a release tag on a commit of `main` and names the
version its tree carries, and nothing is attached until the leak guard's
patterns find nothing in it — every file read byte by byte, binary ones
included.

| Release | Attached to the GitHub Release | Pushed to the registry |
|---|---|---|
| `backend-vX.Y.Z` | `cadless-X.Y.Z-py3-none-any.whl`; `cadless-X.Y.Z.tar.gz`, the sdist the wheel is built from, carrying `tools/leak_guard.py`; `cadless-X.Y.Z-constraints.txt`, the exact resolution the images install; `cadless-catalog-X.Y.Z.tar.gz`, the bundled catalog; `cadless-X.Y.Z-images.txt`, each image by its digest | `ghcr.io/cadlesslab/cadless-api:X.Y.Z` and `ghcr.io/cadlesslab/cadless-worker:X.Y.Z`, labelled with their version, source and commit — pin them by the digests the release lists |
| `frontend-vX.Y.Z` | `cadless-frontend-X.Y.Z.tar.gz`, the `frontend/` source — everything its image is built from, its nginx config included — to be built with whatever panels its builder adds: unpack it and run `docker build -f frontend/Dockerfile --build-arg BASE_PATH=<prefix>/ .` where it was unpacked | — |

A release stays a draft, seen by maintainers alone, until it holds exactly what
it carries — every file, with the bytes that were built, and nothing else; only
then is it published, and with immutable releases on, neither it nor its assets
change after that. An image version is never pushed twice.

If a release run stopped partway, the release is still a draft: dispatch the
`release-backfill` workflow from `main` with its tag. It builds from that tag's
commit, adds only what is missing, and publishes the release once it holds
exactly what was built. It stops and leaves the draft as it is when no release
or more than one carries the tag, or when the draft holds an asset that differs
from what it built or that it did not build: find out where that came from
before removing it from the draft by hand and dispatching the backfill again. A
release whose tagged commit predates these workflows — `v1.0.0` among them —
carries nothing and cannot be backfilled.

`v1.0.0` is the one release cut before the components were split. Both count on
from it: its commit also carries `backend-v1.0.0` and `frontend-v1.0.0`, so that
each component's first changelog compares against a tag that exists, and
`bootstrap-sha` in `release-please-config.json` points at the same commit so
that the first release pull requests do not reach back past it.

Those two are the only tags made by hand, once, before the tag ruleset below.
Never create or push any other: a tag the workflow did not create is a release
nobody merged.

The workflow depends on repository settings that live outside this tree. Set
them up once, before the release workflow first runs — that is, before the
change that adds it is merged:

- **A GitHub App** for the release automation, installed on this repository
  only, with Contents and Pull requests set to read and write, and not listed
  as able to bypass the protection on `main`. Its bot signs the release commit
  off; `signoff` in `release-please-config.json` names it.
- **An environment named `release`** whose deployment branches are limited to
  `main`, holding the App's client ID as the variable `RELEASE_APP_CLIENT_ID`
  and its private key as the secret `RELEASE_APP_PRIVATE_KEY`. Kept out of the
  repository's own secrets, the key cannot be read by a workflow pushed on any
  other branch. Create it before the workflow runs: GitHub would otherwise
  create it on the first run, with no branch limit.
- **Merging**: squash merging only, with the pull request title as the squash
  commit title — otherwise a one-commit pull request lands under its commit's
  subject, which the title check never sees.
- **Required checks on `main`** include `pr-title`, so a title that fails the
  convention cannot be merged.
- **The two baseline tags**, before the tag ruleset exists:
  `git tag backend-v1.0.0 v1.0.0 && git tag frontend-v1.0.0 v1.0.0 &&
  git push origin backend-v1.0.0 frontend-v1.0.0`.
- **A tag ruleset** over every tag, `v1.0.0` included, that lets only the release
  App create, move or delete one — with no bypass for administrators — so the
  rule above is enforced rather than observed.
- **A branch ruleset** over `release*` and `release*/**/*` that lets only the
  release App create or update such a branch, so ordinary branch names must not
  start with `release`. The workflow tags a merged pull request from a branch
  named that way once it carries the release label, which anyone who can push
  may add. The ruleset keeps such branches out of this repository; one pushed to
  a fork is caught only by a reviewer reading the pull request's head branch
  before merging.
- **Immutable releases on.** A release is attached to while it is a draft and
  published after, so locking it at publication costs the run nothing and keeps
  a published release, its tag and its assets as they were published.
- **The write role kept to maintainers**, with contributors working from forks.
  The `release` environment keeps these workflows' publishing jobs to `main`,
  but GitHub lets any workflow on any branch ask for a token that changes
  releases and packages, so the write role decides who can change what a
  release carries.
- **Actions settings**: the default workflow token read-only, and approval
  required before a workflow from an outside contributor runs.

Two more settings can only be made once the first backend release has pushed
its images:

- **The two image packages made public**: GitHub creates `cadless-api` and
  `cadless-worker` private, and only their package settings can change that.
- **Their access**: Actions write access for this repository alone, and
  deleting a version kept to administrators.

## Troubleshooting
- **Generation fails with credential errors** → check your provider's credentials
  in `.env` (default `ANTHROPIC_API_KEY`; for Bedrock, AWS keys / region); the api
  container needs access to the configured provider.
- **Worker unreachable** → `docker compose logs worker`; ensure it is healthy.
- **First build is very slow** → the worker image installs OCCT/build123d
  (hundreds of MB); subsequent builds use the cached dependency layer.
