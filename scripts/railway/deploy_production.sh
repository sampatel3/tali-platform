#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/railway/lib.sh
source "$ROOT_DIR/scripts/railway/lib.sh"

ENV_NAME="${RAILWAY_ENVIRONMENT:-production}"
if [[ "$ENV_NAME" != "production" ]]; then
  echo "error: deploy_production.sh only accepts RAILWAY_ENVIRONMENT=production." >&2
  exit 1
fi
railway_assert_release_source "$ROOT_DIR" "$ENV_NAME"
RELEASE_SHA="$(git -C "$ROOT_DIR" rev-parse HEAD)"
if [[ -z "${TALI_COORDINATED_RELEASE_ATTESTATION:-}" ]]; then
  railway_begin_coordinated_release "$ROOT_DIR" "$RELEASE_SHA"
  trap 'railway_end_coordinated_release' EXIT
fi

run_release_step() {
  "$@"
  "$ROOT_DIR/scripts/release/assert_canonical_source.sh" \
    --expected-sha "$RELEASE_SHA"
}

echo "Starting coordinated Railway production rollout:"
echo "1/3 pin metering and migrate the production database"
run_release_step "$ROOT_DIR/scripts/railway/prepare_production.sh"

echo "2/3 deploy and validate general + scoring workers"
run_release_step "$ROOT_DIR/scripts/railway/deploy_worker.sh"

echo "3/3 deploy web and wait for end-to-end /ready"
run_release_step "$ROOT_DIR/scripts/railway/deploy_backend.sh"

echo "Coordinated Railway production rollout passed."
