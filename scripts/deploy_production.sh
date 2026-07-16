#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/scripts/release/assert_canonical_release.sh"
RELEASE_SHA="$(git rev-parse HEAD)"
"$ROOT_DIR/scripts/release/assert_provider_preflight.sh"

assert_release_source_unchanged() {
  if [[ "$(git rev-parse HEAD)" != "$RELEASE_SHA" || -n "$(git status --porcelain)" ]]; then
    echo "error: release source changed after canonical validation; no further provider changes are allowed." >&2
    exit 1
  fi
}

assert_release_source_unchanged

echo "Deploying backend and workers from origin/main@$RELEASE_SHA ..."
"$ROOT_DIR/scripts/railway/deploy_production.sh"

assert_release_source_unchanged

echo "Deploying frontend from origin/main@$RELEASE_SHA ..."
(
  cd "$ROOT_DIR/frontend"
  vercel --prod --yes
)

assert_release_source_unchanged

echo "Production rollout complete: $RELEASE_SHA"
