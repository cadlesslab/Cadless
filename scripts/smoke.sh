#!/usr/bin/env bash
# End-to-end smoke test: bring the stack up, drive a generation,
# and verify STEP + GLB come back. Exits non-zero on any failure.
set -euo pipefail

# Drive the stack through the bundled proxy at /apps/cadless/api (same path the
# browser/edge uses), so the smoke exercises the full routing chain.
API="${API:-http://localhost:${CADLESS_PROXY_PORT:-8800}/apps/cadless/api}"
export PROMPT="${PROMPT:-A rectangular plate 40x20x10 mm with a 6 mm hole through the centre.}"

echo "==> waiting for api health at ${API}/health"
for i in $(seq 1 60); do
  if curl -sf "${API}/health" >/dev/null; then break; fi
  sleep 2
  [ "$i" = 60 ] && { echo "api did not become healthy"; exit 1; }
done

echo "==> creating project"
PID=$(curl -sf -X POST "${API}/projects" -H 'Content-Type: application/json' \
  -d '{"name":"smoke"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "    project id=${PID}"

echo "==> generating (this calls Bedrock + the worker; may take ~30s)"
RESP=$(curl -sf -X POST "${API}/projects/${PID}/generate" -H 'Content-Type: application/json' \
  -d "{\"prompt\":$(python3 -c 'import json,os;print(json.dumps(os.environ["PROMPT"]))')}")
OK=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["ok"])')
VID=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["version"]["id"])')
echo "    ok=${OK} version=${VID}"
[ "$OK" = "True" ] || { echo "generation failed: $RESP"; exit 1; }

echo "==> fetching STEP + GLB"
curl -sf -o /tmp/smoke.step "${API}/versions/${VID}/artifacts/step"
curl -sf -o /tmp/smoke.glb  "${API}/versions/${VID}/artifacts/glb"
[ -s /tmp/smoke.step ] && [ -s /tmp/smoke.glb ] || { echo "artifacts missing/empty"; exit 1; }

echo "==> SMOKE PASSED: STEP $(wc -c </tmp/smoke.step) bytes, GLB $(wc -c </tmp/smoke.glb) bytes"
