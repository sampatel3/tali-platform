#!/usr/bin/env bash

# Shared, side-effect-free helpers for the Railway deployment wrappers.
# Callers own `set -euo pipefail` and select the Railway environment first.

railway_assert_distinct_services() {
  local web_service="$1"
  local general_worker_service="$2"
  local scoring_worker_service="$3"

  if [[ "$web_service" == "$general_worker_service" \
    || "$web_service" == "$scoring_worker_service" \
    || "$general_worker_service" == "$scoring_worker_service" ]]; then
    echo "error: web, general-worker, and scoring-worker service names must be distinct." >&2
    return 1
  fi
}

railway_service_snapshot() {
  local status_file="$1"
  local environment="$2"
  local service="$3"

  python3 - "$status_file" "$environment" "$service" <<'PY'
import json
import sys

status_file, environment, service = sys.argv[1:]
payload = json.load(open(status_file))
env = next(
    (
        edge.get("node", {})
        for edge in payload.get("environments", {}).get("edges", [])
        if edge.get("node", {}).get("name") == environment
    ),
    None,
)
if env is None:
    print(f"error: Railway environment {environment!r} was not found.", file=sys.stderr)
    raise SystemExit(2)
instance = next(
    (
        edge.get("node", {})
        for edge in env.get("serviceInstances", {}).get("edges", [])
        if edge.get("node", {}).get("serviceName") == service
    ),
    None,
)
if instance is None:
    print(
        f"error: Railway service {service!r} was not found in environment {environment!r}.",
        file=sys.stderr,
    )
    raise SystemExit(3)
latest = instance.get("latestDeployment") or {}
print(f"{latest.get('id') or ''}\t{latest.get('status') or 'MISSING'}")
PY
}

railway_service_deployment_id() {
  local snapshot
  snapshot="$(railway_service_snapshot "$1" "$2" "$3")" || return
  printf '%s\n' "${snapshot%%$'\t'*}"
}

railway_wait_for_new_successful_deployment() {
  local environment="$1"
  local service="$2"
  local previous_id="$3"
  local timeout_seconds="${RAILWAY_DEPLOY_TIMEOUT_SECONDS:-900}"
  local interval_seconds="${RAILWAY_DEPLOY_POLL_INTERVAL_SECONDS:-5}"
  local deadline=$((SECONDS + timeout_seconds))
  local last_report=""

  while (( SECONDS < deadline )); do
    local status_file snapshot deployment_id deployment_status report
    status_file="$(mktemp)"
    if ! railway status --json > "$status_file"; then
      rm -f "$status_file"
      echo "warning: could not refresh Railway deployment status for '$service'; retrying." >&2
      sleep "$interval_seconds"
      continue
    fi
    if ! snapshot="$(railway_service_snapshot "$status_file" "$environment" "$service")"; then
      rm -f "$status_file"
      return 1
    fi
    rm -f "$status_file"

    deployment_id="${snapshot%%$'\t'*}"
    deployment_status="${snapshot#*$'\t'}"
    if [[ -n "$deployment_id" && "$deployment_id" != "$previous_id" ]]; then
      report="$deployment_id:$deployment_status"
      if [[ "$report" != "$last_report" ]]; then
        echo "Railway deployment '$service': $deployment_status ($deployment_id)"
        last_report="$report"
      fi
      case "$deployment_status" in
        SUCCESS)
          return 0
          ;;
        FAILED|CRASHED|CANCELLED|REMOVED|SKIPPED)
          echo "error: Railway deployment '$service' ended with $deployment_status." >&2
          return 1
          ;;
      esac
    fi
    sleep "$interval_seconds"
  done

  echo "error: timed out after ${timeout_seconds}s waiting for a new successful deployment of '$service'." >&2
  return 1
}

railway_service_public_url() {
  local status_file="$1"
  local environment="$2"
  local service="$3"

  python3 - "$status_file" "$environment" "$service" <<'PY'
import json
import sys

status_file, environment, service = sys.argv[1:]
payload = json.load(open(status_file))
env = next(
    (
        edge.get("node", {})
        for edge in payload.get("environments", {}).get("edges", [])
        if edge.get("node", {}).get("name") == environment
    ),
    None,
)
if env is None:
    raise SystemExit(2)
instance = next(
    (
        edge.get("node", {})
        for edge in env.get("serviceInstances", {}).get("edges", [])
        if edge.get("node", {}).get("serviceName") == service
    ),
    None,
)
if instance is None:
    raise SystemExit(3)
domains = instance.get("domains") or {}
entries = list(domains.get("customDomains") or []) + list(
    domains.get("serviceDomains") or []
)
domain = next(
    (str(entry.get("domain") or "").strip() for entry in entries if entry.get("domain")),
    "",
)
if not domain:
    raise SystemExit(4)
print(f"https://{domain}")
PY
}

railway_validate_worker_variables() {
  local environment="$1"
  local service="$2"
  local expected_queues="$3"
  local expected_beat="$4"
  local variables_file
  variables_file="$(mktemp)"
  if ! railway variable list \
    --service "$service" \
    --environment "$environment" \
    --json > "$variables_file"; then
    rm -f "$variables_file"
    echo "error: could not read Railway variables for '$service'." >&2
    return 1
  fi

  local result=0
  python3 - "$variables_file" "$service" "$expected_queues" "$expected_beat" <<'PY' || result=$?
import json
import sys

variables_file, service, expected_queues, expected_beat = sys.argv[1:]
payload = json.load(open(variables_file))
if not isinstance(payload, dict):
    print(f"error: unexpected Railway variable payload for {service!r}.", file=sys.stderr)
    raise SystemExit(1)

actual_mode = str(payload.get("TALI_SERVICE_MODE") or "").strip().lower()
actual_queues = ",".join(
    part.strip()
    for part in str(payload.get("TALI_WORKER_QUEUES") or "").split(",")
    if part.strip()
)
actual_beat = str(payload.get("TALI_WORKER_BEAT") or "").strip().lower()
expected_beat = expected_beat.lower()
errors = []
if actual_mode != "worker":
    errors.append("TALI_SERVICE_MODE must be worker")
if actual_queues != expected_queues:
    errors.append(
        f"TALI_WORKER_QUEUES must be {expected_queues!r} (got {actual_queues!r})"
    )
if actual_beat != expected_beat:
    errors.append(
        f"TALI_WORKER_BEAT must be {expected_beat!r} (got {actual_beat!r})"
    )
if errors:
    for error in errors:
        print(f"error: {service}: {error}.", file=sys.stderr)
    raise SystemExit(1)
print(f"- {service}: queues={actual_queues}, beat={actual_beat}")
PY
  rm -f "$variables_file"
  return "$result"
}

railway_validate_service_variable() {
  local environment="$1"
  local service="$2"
  local key="$3"
  local expected="$4"
  local variables_file result=0
  variables_file="$(mktemp)"
  if ! railway variable list \
    --service "$service" \
    --environment "$environment" \
    --json > "$variables_file"; then
    rm -f "$variables_file"
    echo "error: could not read Railway variables for '$service'." >&2
    return 1
  fi

  python3 - "$variables_file" "$service" "$key" "$expected" <<'PY' || result=$?
import json
import sys

variables_file, service, key, expected = sys.argv[1:]
payload = json.load(open(variables_file))
if not isinstance(payload, dict):
    print(f"error: unexpected Railway variable payload for {service!r}.", file=sys.stderr)
    raise SystemExit(1)
actual = str(payload.get(key) or "").strip()
if actual.lower() != expected.lower():
    print(
        f"error: {service}: {key} must be {expected!r} (got {actual!r}).",
        file=sys.stderr,
    )
    raise SystemExit(1)
print(f"- {service}: {key}={actual}")
PY
  rm -f "$variables_file"
  return "$result"
}

railway_wait_for_readiness() {
  local base_url="${1%/}"
  local timeout_seconds="${RAILWAY_READINESS_TIMEOUT_SECONDS:-420}"
  local interval_seconds="${RAILWAY_READINESS_POLL_INTERVAL_SECONDS:-5}"
  local deadline=$((SECONDS + timeout_seconds))
  local ready_url="$base_url/ready"

  echo "Waiting for web readiness at $ready_url ..."
  while (( SECONDS < deadline )); do
    if curl --fail --silent --show-error --max-time 10 "$ready_url" >/dev/null 2>&1; then
      echo "Web readiness passed: $ready_url"
      return 0
    fi
    sleep "$interval_seconds"
  done

  echo "error: web readiness did not pass within ${timeout_seconds}s: $ready_url" >&2
  echo "diagnostics: curl --fail-with-body ${base_url}/health" >&2
  return 1
}
