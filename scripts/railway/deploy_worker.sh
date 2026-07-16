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

railway_assert_release_source "$ROOT_DIR" "$ENV_NAME"
railway_assert_canonical_backend_dir "$ROOT_DIR" "$BACKEND_DIR" "$ENV_NAME"

for command in railway python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "error: required command is not installed: $command" >&2
    exit 1
  fi
done

for path in \
  "$BACKEND_DIR" \
  "$BACKEND_DIR/railway.json" \
  "$BACKEND_DIR/app/scripts/railway_worker_start.py"; do
  if [[ ! -e "$path" ]]; then
    echo "error: required backend deployment path is missing: $path" >&2
    exit 1
  fi
done

railway_assert_distinct_services \
  "$WEB_SERVICE" "$GENERAL_WORKER_SERVICE" "$SCORING_WORKER_SERVICE"

railway environment "$ENV_NAME" >/dev/null
STATUS_FILE="$(mktemp)"
trap 'rm -f "$STATUS_FILE"' EXIT
railway status --json > "$STATUS_FILE"
railway_service_snapshot \
  "$STATUS_FILE" "$ENV_NAME" "$GENERAL_WORKER_SERVICE" >/dev/null
railway_service_snapshot \
  "$STATUS_FILE" "$ENV_NAME" "$SCORING_WORKER_SERVICE" >/dev/null
railway_assert_production_database_provenance \
  "$ENV_NAME" "$WEB_SERVICE" "$BACKEND_DIR"
railway_assert_release_source "$ROOT_DIR" "$ENV_NAME"

echo "Pinning the production worker topology (environment: $ENV_NAME)..."
railway variable set \
  --service "$GENERAL_WORKER_SERVICE" \
  --environment "$ENV_NAME" \
  --skip-deploys \
  TALI_SERVICE_MODE=worker \
  TALI_WORKER_QUEUES=celery \
  TALI_WORKER_BEAT=true >/dev/null
railway variable set \
  --service "$SCORING_WORKER_SERVICE" \
  --environment "$ENV_NAME" \
  --skip-deploys \
  TALI_SERVICE_MODE=worker \
  TALI_WORKER_QUEUES=scoring \
  TALI_WORKER_BEAT=false >/dev/null

railway_validate_worker_variables \
  "$ENV_NAME" "$GENERAL_WORKER_SERVICE" "celery" "true"
railway_validate_worker_variables \
  "$ENV_NAME" "$SCORING_WORKER_SERVICE" "scoring" "false"

deploy_worker_service() {
  local service="$1"
  local queues="$2"
  local beat="$3"
  local fresh_status previous_id

  fresh_status="$(mktemp)"
  railway status --json > "$fresh_status"
  previous_id="$(railway_service_deployment_id "$fresh_status" "$ENV_NAME" "$service")"
  rm -f "$fresh_status"

  railway_assert_release_source "$ROOT_DIR" "$ENV_NAME"
  echo "Deploying '$service' (queues=$queues, beat=$beat) from $BACKEND_DIR ..."
  (
    cd "$ROOT_DIR"
    railway up \
      --service "$service" \
      --environment "$ENV_NAME" \
      --detach
  )
  railway_wait_for_new_successful_deployment \
    "$ENV_NAME" "$service" "$previous_id"
}

# General first so the one Beat scheduler remains available throughout the
# coordinated rollout; scoring is a separate process and never owns Beat.
deploy_worker_service "$GENERAL_WORKER_SERVICE" "celery" "true"
deploy_worker_service "$SCORING_WORKER_SERVICE" "scoring" "false"

RAILWAY_ENVIRONMENT="$ENV_NAME" \
RAILWAY_BACKEND_SERVICE="$WEB_SERVICE" \
RAILWAY_WORKER_SERVICE="$GENERAL_WORKER_SERVICE" \
RAILWAY_SCORING_WORKER_SERVICE="$SCORING_WORKER_SERVICE" \
RAILWAY_STATUS_SCOPE=workers \
  "$ROOT_DIR/scripts/railway/check_status.sh"

echo "Both worker deployments succeeded and the split topology is validated."
