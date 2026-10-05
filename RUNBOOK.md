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
- `frontend` (nginx) serves the SPA and proxies `/api/*` → `api`.
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
A release is cut by merging the release pull request
([ADR-0010](docs/adr/0010-release-pull-request.md)):

1. Once a change that releases has landed on `main` (see the table in
   [CONTRIBUTING.md](CONTRIBUTING.md#pull-request-titles)), the release workflow
   opens one pull request titled `chore(main): release X.Y.Z`, carrying the next
   version and the changelog since the last release, and refreshes it whenever
   that version or changelog would change.
2. A maintainer reads the changelog — it is built from the merged pull
   requests, whose text their authors can still edit — and squash-merges the
   pull request when the release should ship.
3. The run that merge starts tags `vX.Y.Z` and publishes the GitHub Release with
   that changelog.

Never create or push a tag by hand: a tag the workflow did not create is a
release nobody merged.

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
- **A tag ruleset** that lets only the release App create `v*` tags, so the
  rule above is enforced rather than observed.

## Troubleshooting
- **Generation fails with credential errors** → check your provider's credentials
  in `.env` (default `ANTHROPIC_API_KEY`; for Bedrock, AWS keys / region); the api
  container needs access to the configured provider.
- **Worker unreachable** → `docker compose logs worker`; ensure it is healthy.
- **First build is very slow** → the worker image installs OCCT/build123d
  (hundreds of MB); subsequent builds use the cached dependency layer.
