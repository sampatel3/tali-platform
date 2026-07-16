#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/railway/lib.sh
source "$ROOT_DIR/scripts/railway/lib.sh"

for command in git python3 node railway vercel curl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "error: required production command is missing: $command" >&2
    exit 1
  fi
done
railway whoami >/dev/null
railway status --json >/dev/null
vercel whoami >/dev/null
if [[ ! -f "$ROOT_DIR/frontend/.vercel/project.json" \
  && ( -z "${VERCEL_ORG_ID:-}" || -z "${VERCEL_PROJECT_ID:-}" ) ]]; then
  echo "error: Vercel production target is not explicit." >&2
  echo "Link frontend/ or set both VERCEL_ORG_ID and VERCEL_PROJECT_ID." >&2
  exit 1
fi

"$ROOT_DIR/scripts/release/assert_canonical_release.sh"
RELEASE_SHA="$(git rev-parse HEAD)"
railway_begin_coordinated_release "$ROOT_DIR" "$RELEASE_SHA"
trap 'railway_end_coordinated_release' EXIT

echo "Deploying backend and workers from origin/main@$RELEASE_SHA ..."
"$ROOT_DIR/scripts/railway/deploy_production.sh"

"$ROOT_DIR/scripts/release/assert_canonical_source.sh" --expected-sha "$RELEASE_SHA"

echo "Deploying frontend from origin/main@$RELEASE_SHA ..."
(
  cd "$ROOT_DIR/frontend"
  vercel --prod --yes
)

"$ROOT_DIR/scripts/release/assert_canonical_source.sh" --expected-sha "$RELEASE_SHA"

echo "Production rollout complete: $RELEASE_SHA"
