#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

for command in python3 node; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "error: required release command is missing: $command" >&2
    exit 1
  fi
done

"$ROOT_DIR/scripts/release/assert_canonical_source.sh" "$@"
(
  cd backend
  python3 scripts/check_alembic_single_head.py
)
(
  cd frontend
  node scripts/check-chat-system.mjs
)

echo "Canonical release checks passed."
