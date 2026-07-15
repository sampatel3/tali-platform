#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

for command in git python3 node; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "error: required release command is missing: $command" >&2
    exit 1
  fi
done

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: production releases require a clean worktree." >&2
  exit 1
fi

git fetch --quiet origin main
HEAD_SHA="$(git rev-parse HEAD)"
MAIN_SHA="$(git rev-parse origin/main)"
if [[ "$HEAD_SHA" != "$MAIN_SHA" ]]; then
  echo "error: refusing to deploy a branch or stale commit." >&2
  echo "       HEAD=$HEAD_SHA" >&2
  echo "origin/main=$MAIN_SHA" >&2
  echo "Merge through main, fetch it, and deploy that exact commit." >&2
  exit 1
fi

echo "Release source verified: origin/main@$HEAD_SHA"
(
  cd backend
  python3 scripts/check_alembic_single_head.py
)
(
  cd frontend
  node scripts/check-chat-system.mjs
)

echo "Canonical release checks passed."
