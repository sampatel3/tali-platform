#!/usr/bin/env bash
# platform-qa environment bootstrap.
#
# Fresh containers and git worktrees have NO .venv and NO node_modules — so the
# harness must build its own environment, never assume one. This script is
# idempotent: run it on a clean checkout or a worktree.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"

echo "==> Creating venv (.venv)"
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing pinned QA deps"
pip install --upgrade pip -q
pip install -q -e . 2>/dev/null || pip install -q pytest httpx pydantic psycopg[binary]

echo "==> (optional) Starting throwaway Postgres on :55432"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker compose -f docker-compose.qa.yml up -d
  export QA_DATABASE_URL="postgresql://qa:qa@localhost:55432/qa"
  echo "    QA_DATABASE_URL=$QA_DATABASE_URL"
else
  echo "    docker unavailable — contract tests still run; E2E will skip."
fi

# REPLACE-WITH-REAL: clone/link the repos under test so their interfaces are
# importable, e.g.:
#   for repo in mainspring taali-brand cadence; do
#     [ -d ".repos/$repo" ] || git clone "git@github.com:sampatel3/$repo" ".repos/$repo"
#   done

echo "==> Running contract tests"
pytest tests/contract -q
echo "==> Done. (E2E: set QA_DATABASE_URL and run 'pytest tests/e2e')"
