#!/usr/bin/env bash
# Cadless — PoC stack control (served at /apps/cadless behind a bundled Caddy).
#
#   ./start.sh build      build the docker images
#   ./start.sh start      build (if needed) + start the stack, wait for health
#   ./start.sh stop       stop + remove containers (keeps the data volume)
#   ./start.sh restart    stop, then start
#
# Only the bundled proxy is published, on CADLESS_PROXY_PORT (default 8800).
# Override per-run:  CADLESS_PROXY_PORT=9000 ./start.sh start
set -euo pipefail

cd "$(dirname "$0")"

# Default to 8800; override CADLESS_PROXY_PORT if that port is already in use.
export CADLESS_PROXY_PORT="${CADLESS_PROXY_PORT:-8800}"
BASE="/apps/cadless"

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi
}

ensure_env() {
  if [[ ! -f .env ]]; then
    echo "==> no .env found; creating from .env.example"
    cp .env.example .env
    echo "    edit .env to add an LLM credential before generating."
  fi
  if ! grep -qE '^(ANTHROPIC_API_KEY|OPENAI_API_KEY|AWS_ACCESS_KEY_ID)=.+' .env 2>/dev/null \
      && [[ -z "${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}${AWS_ACCESS_KEY_ID:-}" ]]; then
    echo "    warning: no LLM credential in .env or environment (ANTHROPIC_API_KEY /" >&2
    echo "    OPENAI_API_KEY / AWS keys) — generation stays off until one is set;" >&2
    echo "    browsing the bundled catalog works without it." >&2
  fi
}

wait_health() {
  local url="http://localhost:${CADLESS_PROXY_PORT}${BASE}/api/health"
  echo "==> waiting for stack health at ${url}"
  for _ in $(seq 1 60); do
    if curl -sf "$url" >/dev/null 2>&1; then echo "    healthy"; return 0; fi
    sleep 2
  done
  echo "    stack did not become healthy in time — check: ./start.sh logs" >&2
  return 1
}

usage() { echo "Usage: $0 {start|stop|restart|build} [extra compose args]" >&2; exit 2; }

cmd="${1:-}"
[[ $# -gt 0 ]] && shift || true

case "$cmd" in
  build)
    compose build "$@"
    ;;
  start)
    ensure_env
    compose up -d --build "$@"
    wait_health || true
    echo "==> up"
    echo "    Local:    http://localhost:${CADLESS_PROXY_PORT}${BASE}/"
    echo "    Public:   https://<your-domain>${BASE}/  (once the platform Caddy routes ${BASE}* here;"
    echo "              see infra/proxy/README.md)"
    ;;
  stop)
    compose down "$@"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  logs)  # convenience
    compose logs -f "$@"
    ;;
  *)
    usage
    ;;
esac
