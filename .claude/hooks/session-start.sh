#!/usr/bin/env bash
# SessionStart hook: make the repo ready to run tests and linters in a fresh
# session (e.g. Claude Code on the web, where the container is cloned clean).
#
# Best-effort and idempotent: it only installs what's missing and never fails the
# session — any error is reported as context, not a hard stop. Reads the hook JSON
# from stdin (unused here) and emits additionalContext describing readiness.
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cat >/dev/null 2>&1 || true   # drain hook stdin if present

notes=()

# --- Backend: ensure a venv with deps so `pytest`/`alembic` work ---
if [ -f "$repo_root/backend/requirements.txt" ]; then
  if [ ! -d "$repo_root/backend/.venv" ]; then
    python3 -m venv "$repo_root/backend/.venv" >/dev/null 2>&1 \
      && "$repo_root/backend/.venv/bin/pip" install -q -r "$repo_root/backend/requirements.txt" >/dev/null 2>&1 \
      && notes+=("backend: created .venv and installed requirements") \
      || notes+=("backend: dependency install incomplete — run pip install -r backend/requirements.txt")
  else
    notes+=("backend: .venv present")
  fi
fi

# --- Frontend: ensure node_modules so `npm test`/`npm run build` work ---
if [ -f "$repo_root/frontend/package.json" ]; then
  if [ ! -d "$repo_root/frontend/node_modules" ]; then
    ( cd "$repo_root/frontend" && npm ci --silent >/dev/null 2>&1 ) \
      && notes+=("frontend: ran npm ci") \
      || notes+=("frontend: npm ci incomplete — run npm ci in frontend/")
  else
    notes+=("frontend: node_modules present")
  fi
fi

summary="Tali dev environment: ${notes[*]:-no setup needed}"

# Emit as additionalContext (valid JSON). Falls back silently if jq is absent.
if command -v jq >/dev/null 2>&1; then
  jq -n --arg ctx "$summary" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
fi

exit 0
