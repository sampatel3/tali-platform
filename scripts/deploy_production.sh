#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/scripts/release/assert_canonical_release.sh"
RELEASE_SHA="$(git rev-parse HEAD)"

echo "Deploying backend and workers from origin/main@$RELEASE_SHA ..."
"$ROOT_DIR/scripts/railway/deploy_production.sh"

if [[ "$(git rev-parse HEAD)" != "$RELEASE_SHA" ]]; then
  echo "error: source commit changed during the backend rollout." >&2
  exit 1
fi

echo "Deploying frontend from origin/main@$RELEASE_SHA ..."
(
  cd "$ROOT_DIR/frontend"
  vercel --prod --yes
)

if [[ "$(git rev-parse HEAD)" != "$RELEASE_SHA" ]]; then
  echo "error: source commit changed during the frontend rollout." >&2
  exit 1
fi

echo "Production rollout complete: $RELEASE_SHA"
