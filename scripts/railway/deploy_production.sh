#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Starting coordinated Railway production rollout:"
echo "1/3 pin metering and migrate the production database"
"$ROOT_DIR/scripts/railway/prepare_production.sh"

echo "2/3 deploy and validate general + scoring workers"
"$ROOT_DIR/scripts/railway/deploy_worker.sh"

echo "3/3 deploy web and wait for end-to-end /ready"
"$ROOT_DIR/scripts/railway/deploy_backend.sh"

echo "Coordinated Railway production rollout passed."
