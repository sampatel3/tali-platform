#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/railway/lib.sh
source "$ROOT_DIR/scripts/railway/lib.sh"

ENV_NAME="${RAILWAY_ENVIRONMENT:-production}"
WEB_SERVICE="${RAILWAY_BACKEND_SERVICE:-resourceful-adaptation}"
GENERAL_WORKER_SERVICE="${RAILWAY_WORKER_SERVICE:-taali-worker}"
SCORING_WORKER_SERVICE="${RAILWAY_SCORING_WORKER_SERVICE:-taali-worker-scoring}"
STATUS_SCOPE="${RAILWAY_STATUS_SCOPE:-all}"

if [[ "$STATUS_SCOPE" != "all" && "$STATUS_SCOPE" != "workers" ]]; then
  echo "error: RAILWAY_STATUS_SCOPE must be 'all' or 'workers'." >&2
  exit 1
fi

for command in railway python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "error: required command is not installed: $command" >&2
    exit 1
  fi
done

railway_assert_distinct_services \
  "$WEB_SERVICE" "$GENERAL_WORKER_SERVICE" "$SCORING_WORKER_SERVICE"

railway environment "$ENV_NAME" >/dev/null
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
STATUS_FILE="$TMP_DIR/status.json"
railway status --json > "$STATUS_FILE"

python3 - \
  "$STATUS_FILE" \
  "$ENV_NAME" \
  "$STATUS_SCOPE" \
  "$WEB_SERVICE" \
  "$GENERAL_WORKER_SERVICE" \
  "$SCORING_WORKER_SERVICE" <<'PY'
import json
import sys

(
    status_file,
    requested_environment,
    scope,
    web_service,
    general_worker,
    scoring_worker,
) = sys.argv[1:]
payload = json.load(open(status_file))
print(f"Project: {payload.get('name') or '(unknown)'}")

env = next(
    (
        edge.get("node", {})
        for edge in payload.get("environments", {}).get("edges", [])
        if edge.get("node", {}).get("name") == requested_environment
    ),
    None,
)
if env is None:
    available = ", ".join(
        sorted(
            str(edge.get("node", {}).get("name") or "(unknown)")
            for edge in payload.get("environments", {}).get("edges", [])
        )
    )
    print(
        f"error: environment {requested_environment!r} was not found "
        f"(available: {available or 'none'}).",
        file=sys.stderr,
    )
    raise SystemExit(1)

print(f"Environment: {requested_environment}")
instances = {
    edge.get("node", {}).get("serviceName"): edge.get("node", {})
    for edge in env.get("serviceInstances", {}).get("edges", [])
}
required = [general_worker, scoring_worker]
if scope == "all":
    required.insert(0, web_service)

errors = []
print("Required services:")
for service in required:
    instance = instances.get(service)
    if instance is None:
        errors.append(f"required service {service!r} is missing")
        print(f"- {service}: MISSING")
        continue
    latest = instance.get("latestDeployment") or {}
    deployment_id = latest.get("id") or "-"
    status = str(latest.get("status") or "MISSING").upper()
    created = latest.get("createdAt") or "-"
    print(f"- {service}: {status} (deployment: {deployment_id}, created: {created})")
    if status != "SUCCESS":
        errors.append(f"{service!r} latest deployment is {status}, not SUCCESS")

    if service in {general_worker, scoring_worker}:
        meta = latest.get("meta") or {}
        manifest = meta.get("serviceManifest") or meta.get("fileServiceManifest") or {}
        healthcheck = (manifest.get("deploy") or {}).get("healthcheckPath")
        if healthcheck:
            errors.append(
                f"{service!r} still has HTTP healthcheckPath={healthcheck!r}; "
                "Celery workers do not serve HTTP"
            )

if errors:
    for error in errors:
        print(f"error: {error}.", file=sys.stderr)
    raise SystemExit(1)
PY

echo "Worker topology variables:"
railway_validate_worker_variables \
  "$ENV_NAME" "$GENERAL_WORKER_SERVICE" "celery" "true"
railway_validate_worker_variables \
  "$ENV_NAME" "$SCORING_WORKER_SERVICE" "scoring" "false"

echo "Production agent contract variables:"
for service in \
  "$WEB_SERVICE" \
  "$GENERAL_WORKER_SERVICE" \
  "$SCORING_WORKER_SERVICE"; do
  railway_validate_service_variable \
    "$ENV_NAME" "$service" "USAGE_METER_LIVE" "true"
  railway_validate_service_variable \
    "$ENV_NAME" "$service" "ATS_PUBLIC_APPLY_ENABLED" "true"
  railway_validate_service_variable \
    "$ENV_NAME" "$service" "BULLHORN_ENABLED" "true"
  railway_validate_service_variable \
    "$ENV_NAME" "$service" "MVP_DISABLE_WORKABLE" "false"
done

echo "Railway ${STATUS_SCOPE} status validation passed."
