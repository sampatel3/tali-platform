#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="${BACKEND_DIR:-$ROOT_DIR/backend}"
ENV_NAME="${RAILWAY_ENVIRONMENT:-production}"
WORKER_SERVICE="${RAILWAY_WORKER_SERVICE:-}"

if ! command -v railway >/dev/null 2>&1; then
  echo "error: railway CLI is not installed. Install with: npm i -g @railway/cli" >&2
  exit 1
fi

if [[ ! -d "$BACKEND_DIR" ]]; then
  echo "error: backend directory not found: $BACKEND_DIR" >&2
  exit 1
fi

if [[ ! -f "$BACKEND_DIR/railway.worker.json" ]]; then
  echo "error: missing backend/railway.worker.json" >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "error: rsync is required to prepare the worker deployment bundle" >&2
  exit 1
fi

cd "$ROOT_DIR"
railway environment "$ENV_NAME" >/dev/null
STATUS_FILE="$(mktemp)"
WORKER_BUNDLE_DIR="$(mktemp -d)"
trap 'rm -f "$STATUS_FILE"; rm -rf "$WORKER_BUNDLE_DIR"' EXIT
railway status --json > "$STATUS_FILE"

if [[ -z "$WORKER_SERVICE" ]]; then
  WORKER_SERVICE="$(python3 - "$STATUS_FILE" <<'PY'
import json
import re
import sys

payload = json.load(open(sys.argv[1]))
services = [
    edge.get("node", {}).get("name", "")
    for edge in payload.get("services", {}).get("edges", [])
]
for name in services:
    if re.search(r"worker", name, re.IGNORECASE):
        print(name)
        break
PY
)"
fi

if [[ -z "$WORKER_SERVICE" ]]; then
  echo "No worker service found. Skipping worker deploy."
  echo "Set RAILWAY_WORKER_SERVICE if your worker service name does not include 'worker'."
  exit 0
fi

SERVICE_EXISTS="$(python3 - "$STATUS_FILE" "$WORKER_SERVICE" <<'PY'
import json
import sys

status_file = sys.argv[1]
service_name = sys.argv[2]
payload = json.load(open(status_file))
services = [
    edge.get("node", {}).get("name")
    for edge in payload.get("services", {}).get("edges", [])
]
print("1" if service_name in services else "0")
PY
)"

if [[ "$SERVICE_EXISTS" != "1" ]]; then
  echo "error: Railway worker service '$WORKER_SERVICE' was not found." >&2
  exit 1
fi

cd "$BACKEND_DIR"
echo "Deploying worker service '$WORKER_SERVICE' from $BACKEND_DIR (environment: $ENV_NAME)..."
rsync -a \
  --delete \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude ".mypy_cache" \
  --exclude "htmlcov" \
  --exclude "dist" \
  --exclude "build" \
  "$BACKEND_DIR"/ "$WORKER_BUNDLE_DIR"/
cp "$BACKEND_DIR/railway.worker.json" "$WORKER_BUNDLE_DIR/railway.json"
railway up "$WORKER_BUNDLE_DIR" --path-as-root --service "$WORKER_SERVICE" --environment "$ENV_NAME" --detach

echo "Deployment submitted with worker-specific Railway config. Check status with: scripts/railway/check_status.sh"
