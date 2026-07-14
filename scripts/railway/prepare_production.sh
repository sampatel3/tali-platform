#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/railway/lib.sh
source "$ROOT_DIR/scripts/railway/lib.sh"

BACKEND_DIR="${BACKEND_DIR:-$ROOT_DIR/backend}"
ENV_NAME="${RAILWAY_ENVIRONMENT:-production}"
WEB_SERVICE="${RAILWAY_BACKEND_SERVICE:-resourceful-adaptation}"
GENERAL_WORKER_SERVICE="${RAILWAY_WORKER_SERVICE:-taali-worker}"
SCORING_WORKER_SERVICE="${RAILWAY_SCORING_WORKER_SERVICE:-taali-worker-scoring}"

if [[ "$ENV_NAME" != "production" ]]; then
  echo "error: prepare_production.sh only accepts RAILWAY_ENVIRONMENT=production." >&2
  exit 1
fi

for command in railway python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "error: required command is not installed: $command" >&2
    exit 1
  fi
done

if [[ ! -f "$BACKEND_DIR/alembic.ini" ]]; then
  echo "error: missing backend/alembic.ini: $BACKEND_DIR/alembic.ini" >&2
  exit 1
fi

railway_assert_distinct_services \
  "$WEB_SERVICE" "$GENERAL_WORKER_SERVICE" "$SCORING_WORKER_SERVICE"
railway environment "$ENV_NAME" >/dev/null

STATUS_FILE="$(mktemp)"
WEB_VARIABLES_FILE="$(mktemp)"
trap 'rm -f "$STATUS_FILE" "$WEB_VARIABLES_FILE"' EXIT
railway status --json > "$STATUS_FILE"
for service in \
  "$WEB_SERVICE" \
  "$GENERAL_WORKER_SERVICE" \
  "$SCORING_WORKER_SERVICE"; do
  railway_service_snapshot "$STATUS_FILE" "$ENV_NAME" "$service" >/dev/null
done

echo "Pinning the production agent contract on web and both workers without deploying..."
for service in \
  "$WEB_SERVICE" \
  "$GENERAL_WORKER_SERVICE" \
  "$SCORING_WORKER_SERVICE"; do
  railway variable set \
    --service "$service" \
    --environment "$ENV_NAME" \
    --skip-deploys \
    USAGE_METER_LIVE=true \
    ATS_PUBLIC_APPLY_ENABLED=true >/dev/null
done
for service in \
  "$WEB_SERVICE" \
  "$GENERAL_WORKER_SERVICE" \
  "$SCORING_WORKER_SERVICE"; do
  railway_validate_service_variable \
    "$ENV_NAME" "$service" "USAGE_METER_LIVE" "true"
  railway_validate_service_variable \
    "$ENV_NAME" "$service" "ATS_PUBLIC_APPLY_ENABLED" "true"
done

# Fetch the resolved public database URL without printing any Railway variables.
# Migrations run as a separate predeploy operation, before either worker starts
# executing code that may depend on the new schema.
railway variable list \
  --service "$WEB_SERVICE" \
  --environment "$ENV_NAME" \
  --json > "$WEB_VARIABLES_FILE"

python3 - "$WEB_VARIABLES_FILE" "$BACKEND_DIR" <<'PY'
import json
import os
import subprocess
import sys
from urllib.parse import urlparse

variables_file, backend_dir = sys.argv[1:]
payload = json.load(open(variables_file))
if not isinstance(payload, dict):
    print("error: unexpected Railway variable payload for the web service.", file=sys.stderr)
    raise SystemExit(1)
database_url = str(payload.get("DATABASE_PUBLIC_URL") or "").strip()
if not database_url:
    print(
        "error: production web service has no DATABASE_PUBLIC_URL; "
        "attach the public Postgres URL before rollout.",
        file=sys.stderr,
    )
    raise SystemExit(1)
parsed = urlparse(database_url)
hostname = (parsed.hostname or "").lower()
if parsed.scheme not in {"postgres", "postgresql"} or not hostname:
    print("error: DATABASE_PUBLIC_URL is not a valid PostgreSQL URL.", file=sys.stderr)
    raise SystemExit(1)
if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(
    ".railway.internal"
):
    print(
        "error: DATABASE_PUBLIC_URL is not publicly reachable from the deploy host.",
        file=sys.stderr,
    )
    raise SystemExit(1)

env = os.environ.copy()
env["DATABASE_PUBLIC_URL"] = database_url
env["DATABASE_URL"] = database_url
print(f"Running production migrations against {hostname} ...", flush=True)
subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"],
    cwd=backend_dir,
    env=env,
    check=True,
)
print("Verifying production Alembic revision ...", flush=True)
subprocess.run(
    [sys.executable, "-m", "alembic", "current"],
    cwd=backend_dir,
    env=env,
    check=True,
)
PY

echo "Production variables and database schema preparation passed."
