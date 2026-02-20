#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="${BACKEND_DIR:-$ROOT_DIR/backend}"
SERVICE_NAME="${RAILWAY_BACKEND_SERVICE:-resourceful-adaptation}"
ENV_NAME="${RAILWAY_ENVIRONMENT:-production}"

if ! command -v railway >/dev/null 2>&1; then
  echo "error: railway CLI is not installed. Install with: npm i -g @railway/cli" >&2
  exit 1
fi

if [[ ! -d "$BACKEND_DIR" ]]; then
  echo "error: backend directory not found: $BACKEND_DIR" >&2
  exit 1
fi

if [[ ! -f "$BACKEND_DIR/railway.json" ]]; then
  echo "error: missing backend/railway.json; refusing to deploy from repo root layout" >&2
  exit 1
fi

cd "$ROOT_DIR"
railway environment "$ENV_NAME" >/dev/null

STATUS_FILE="$(mktemp)"
trap 'rm -f "$STATUS_FILE"' EXIT
railway status --json > "$STATUS_FILE"

SERVICE_EXISTS="$(python3 - "$STATUS_FILE" "$SERVICE_NAME" <<'PY'
import json
import sys

status_file = sys.argv[1]
service_name = sys.argv[2]
try:
    payload = json.load(open(status_file))
except Exception:
    print("0")
    raise SystemExit(0)

services = [
    edge.get("node", {}).get("name")
    for edge in payload.get("services", {}).get("edges", [])
]
print("1" if service_name in services else "0")
PY
)"

if [[ "$SERVICE_EXISTS" != "1" ]]; then
  echo "error: Railway service '$SERVICE_NAME' was not found in linked project." >&2
  echo "tip: run 'railway status --json' to list services and set RAILWAY_BACKEND_SERVICE." >&2
  exit 1
fi

cd "$BACKEND_DIR"
echo "Deploying backend service '$SERVICE_NAME' from $BACKEND_DIR (environment: $ENV_NAME)..."
railway up --service "$SERVICE_NAME" --environment "$ENV_NAME" --detach

echo "Deployment submitted. Check status with: scripts/railway/check_status.sh"
