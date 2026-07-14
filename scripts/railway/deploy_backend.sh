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

for command in railway python3 curl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "error: required command is not installed: $command" >&2
    exit 1
  fi
done

if [[ ! -d "$BACKEND_DIR" ]]; then
  echo "error: backend directory not found: $BACKEND_DIR" >&2
  exit 1
fi
if [[ ! -f "$BACKEND_DIR/railway.json" ]]; then
  echo "error: missing backend/railway.json; refusing to deploy from repo root layout" >&2
  exit 1
fi

# The same repository config is consumed by web and both Celery services.
# An HTTP healthcheck here would make the workers fail every deployment because
# they intentionally do not bind a port. Web readiness is polled below instead.
python3 - "$BACKEND_DIR/railway.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1]))
deploy = payload.get("deploy") or {}
if "healthcheckPath" in deploy or "healthcheckTimeout" in deploy:
    print(
        "error: shared backend/railway.json must not configure an HTTP healthcheck; "
        "the same config is used by Celery workers.",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY

railway_assert_distinct_services \
  "$WEB_SERVICE" "$GENERAL_WORKER_SERVICE" "$SCORING_WORKER_SERVICE"
railway environment "$ENV_NAME" >/dev/null

# A web-only deployment must never report success while either required worker
# is missing, failed, or configured for the wrong queue/Beat ownership.
RAILWAY_ENVIRONMENT="$ENV_NAME" \
RAILWAY_BACKEND_SERVICE="$WEB_SERVICE" \
RAILWAY_WORKER_SERVICE="$GENERAL_WORKER_SERVICE" \
RAILWAY_SCORING_WORKER_SERVICE="$SCORING_WORKER_SERVICE" \
RAILWAY_STATUS_SCOPE=workers \
  "$ROOT_DIR/scripts/railway/check_status.sh"

STATUS_FILE="$(mktemp)"
trap 'rm -f "$STATUS_FILE"' EXIT
railway status --json > "$STATUS_FILE"
previous_id="$(
  railway_service_deployment_id "$STATUS_FILE" "$ENV_NAME" "$WEB_SERVICE"
)"

echo "Deploying web service '$WEB_SERVICE' from $BACKEND_DIR (environment: $ENV_NAME)..."
(
  cd "$BACKEND_DIR"
  railway up \
    --service "$WEB_SERVICE" \
    --environment "$ENV_NAME" \
    --detach
)
railway_wait_for_new_successful_deployment \
  "$ENV_NAME" "$WEB_SERVICE" "$previous_id"

railway status --json > "$STATUS_FILE"
BACKEND_BASE_URL="${RAILWAY_BACKEND_URL:-${BACKEND_URL:-}}"
if [[ -z "$BACKEND_BASE_URL" ]]; then
  if ! BACKEND_BASE_URL="$(
    railway_service_public_url "$STATUS_FILE" "$ENV_NAME" "$WEB_SERVICE"
  )"; then
    echo "error: could not derive a public domain for '$WEB_SERVICE'." >&2
    echo "Set RAILWAY_BACKEND_URL=https://<backend-domain> and retry validation." >&2
    exit 1
  fi
fi
railway_wait_for_readiness "$BACKEND_BASE_URL"
railway_validate_default_agent_capabilities "$BACKEND_BASE_URL"

RAILWAY_ENVIRONMENT="$ENV_NAME" \
RAILWAY_BACKEND_SERVICE="$WEB_SERVICE" \
RAILWAY_WORKER_SERVICE="$GENERAL_WORKER_SERVICE" \
RAILWAY_SCORING_WORKER_SERVICE="$SCORING_WORKER_SERVICE" \
RAILWAY_STATUS_SCOPE=all \
  "$ROOT_DIR/scripts/railway/check_status.sh"

echo "Web deployment succeeded; /ready and the default agent assessment path are production-ready."
