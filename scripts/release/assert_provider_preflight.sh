#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"

ENV_NAME="${RAILWAY_ENVIRONMENT:-production}"
WEB_SERVICE="${RAILWAY_BACKEND_SERVICE:-resourceful-adaptation}"
GENERAL_WORKER_SERVICE="${RAILWAY_WORKER_SERVICE:-taali-worker}"
SCORING_WORKER_SERVICE="${RAILWAY_SCORING_WORKER_SERVICE:-taali-worker-scoring}"

# These identifiers bind the production wrapper to the intended provider
# projects. Override them only when the production projects are deliberately
# migrated; a same-named project in another account must not pass preflight.
EXPECTED_RAILWAY_PROJECT_ID="${TALI_RAILWAY_PROJECT_ID:-54330ea2-57c1-4ca3-8539-8e4184b820be}"
EXPECTED_RAILWAY_PROJECT_NAME="${TALI_RAILWAY_PROJECT_NAME:-tali-platform}"
EXPECTED_VERCEL_PROJECT_ID="${TALI_VERCEL_PROJECT_ID:-prj_k54r0dKrcwQs8PXNgyIPlt3rT9KY}"
EXPECTED_VERCEL_ORG_ID="${TALI_VERCEL_ORG_ID:-team_l8V4u5axLxqzbUWGv9akM4eh}"
EXPECTED_VERCEL_PROJECT_NAME="${TALI_VERCEL_PROJECT_NAME:-frontend}"
VERCEL_LINK_FILE="${TALI_VERCEL_LINK_FILE:-$FRONTEND_DIR/.vercel/project.json}"

for command in railway vercel python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "error: required production provider command is missing: $command" >&2
    exit 1
  fi
done

if [[ ! -d "$FRONTEND_DIR" ]]; then
  echo "error: frontend directory not found: $FRONTEND_DIR" >&2
  exit 1
fi
if [[ ! -f "$VERCEL_LINK_FILE" ]]; then
  echo "error: Vercel project link is missing: $VERCEL_LINK_FILE" >&2
  echo "Link the existing production project before running a release; auto-linking is not allowed." >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
RAILWAY_USER_FILE="$TMP_DIR/railway-user.json"
RAILWAY_STATUS_FILE="$TMP_DIR/railway-status.json"
VERCEL_USER_FILE="$TMP_DIR/vercel-user.json"

# Every provider call in this script is read-only. Authentication and link
# failures must happen before prepare_production mutates variables or deploys.
if ! railway whoami --json > "$RAILWAY_USER_FILE" 2>/dev/null; then
  echo "error: Railway authentication failed; log in before releasing." >&2
  exit 1
fi
if ! railway status --json > "$RAILWAY_STATUS_FILE" 2>/dev/null; then
  echo "error: Railway project status is unavailable; link this checkout to the production project." >&2
  exit 1
fi

python3 - \
  "$RAILWAY_USER_FILE" \
  "$RAILWAY_STATUS_FILE" \
  "$EXPECTED_RAILWAY_PROJECT_ID" \
  "$EXPECTED_RAILWAY_PROJECT_NAME" \
  "$ENV_NAME" \
  "$WEB_SERVICE" \
  "$GENERAL_WORKER_SERVICE" \
  "$SCORING_WORKER_SERVICE" <<'PY'
import json
import sys

(
    user_file,
    status_file,
    expected_project_id,
    expected_project_name,
    environment,
    web_service,
    general_worker,
    scoring_worker,
) = sys.argv[1:]

user = json.load(open(user_file))
if not isinstance(user, dict) or not (user.get("email") or user.get("name")):
    print("error: Railway did not return an authenticated identity.", file=sys.stderr)
    raise SystemExit(1)

status = json.load(open(status_file))
if not isinstance(status, dict):
    print("error: Railway returned an invalid project payload.", file=sys.stderr)
    raise SystemExit(1)
actual_id = str(status.get("id") or "").strip()
actual_name = str(status.get("name") or "").strip()
if actual_id != expected_project_id or actual_name != expected_project_name:
    print(
        "error: this checkout is linked to the wrong Railway project "
        f"({actual_name or 'unknown'} / {actual_id or 'unknown'}); expected "
        f"{expected_project_name} / {expected_project_id}.",
        file=sys.stderr,
    )
    raise SystemExit(1)

environment_row = next(
    (
        edge.get("node", {})
        for edge in status.get("environments", {}).get("edges", [])
        if edge.get("node", {}).get("name") == environment
    ),
    None,
)
if environment_row is None:
    print(
        f"error: Railway environment {environment!r} is not configured in the expected project.",
        file=sys.stderr,
    )
    raise SystemExit(1)

configured_services = {
    str(edge.get("node", {}).get("serviceName") or "").strip()
    for edge in environment_row.get("serviceInstances", {}).get("edges", [])
}
required_services = (web_service, general_worker, scoring_worker)
if len(set(required_services)) != len(required_services):
    print(
        "error: Railway web, general-worker, and scoring-worker service names must be distinct.",
        file=sys.stderr,
    )
    raise SystemExit(1)
missing = [service for service in required_services if service not in configured_services]
if missing:
    print(
        "error: expected Railway production services are missing: " + ", ".join(missing),
        file=sys.stderr,
    )
    raise SystemExit(1)
PY

if ! vercel whoami --format=json --non-interactive > "$VERCEL_USER_FILE" 2>/dev/null; then
  echo "error: Vercel authentication failed; log in before releasing." >&2
  exit 1
fi
python3 - \
  "$VERCEL_USER_FILE" \
  "$VERCEL_LINK_FILE" \
  "$EXPECTED_VERCEL_PROJECT_ID" \
  "$EXPECTED_VERCEL_ORG_ID" \
  "$EXPECTED_VERCEL_PROJECT_NAME" <<'PY'
import json
import sys

user_file, link_file, expected_project_id, expected_org_id, expected_name = sys.argv[1:]
user = json.load(open(user_file))
if not isinstance(user, dict) or not (user.get("email") or user.get("username")):
    print("error: Vercel did not return an authenticated identity.", file=sys.stderr)
    raise SystemExit(1)

link = json.load(open(link_file))
if not isinstance(link, dict):
    print("error: Vercel project link is invalid.", file=sys.stderr)
    raise SystemExit(1)
actual = (
    str(link.get("projectId") or "").strip(),
    str(link.get("orgId") or "").strip(),
    str(link.get("projectName") or "").strip(),
)
expected = (expected_project_id, expected_org_id, expected_name)
if actual != expected:
    print(
        "error: frontend is linked to the wrong Vercel project; "
        f"expected {expected_name} / {expected_project_id} in {expected_org_id}.",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY

# `project inspect` proves the authenticated account can resolve the exact local
# link. It is read-only and, unlike `vercel --prod`, cannot create/link a project.
if ! vercel project inspect \
  --cwd "$FRONTEND_DIR" \
  --non-interactive \
  --no-color > /dev/null 2>&1; then
  echo "error: the linked Vercel production project is not accessible to the authenticated account." >&2
  exit 1
fi

echo "Provider preflight passed: Railway ${EXPECTED_RAILWAY_PROJECT_NAME}/${ENV_NAME} and Vercel ${EXPECTED_VERCEL_PROJECT_NAME}."
