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

## Troubleshooting
- **Generation fails with credential errors** → check your provider's credentials
  in `.env` (default `ANTHROPIC_API_KEY`; for Bedrock, AWS keys / region); the api
  container needs access to the configured provider.
- **Worker unreachable** → `docker compose logs worker`; ensure it is healthy.
- **First build is very slow** → the worker image installs OCCT/build123d
  (hundreds of MB); subsequent builds use the cached dependency layer.
