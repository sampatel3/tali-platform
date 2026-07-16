#!/usr/bin/env bash

# Shared, side-effect-free helpers for the Railway deployment wrappers.
# Callers own `set -euo pipefail` and select the Railway environment first.

readonly TALI_NIXPACKS_INSTALL_CMD="python -m venv --copies /opt/venv && . /opt/venv/bin/activate && python scripts/check_requirements_lock.py --runtime-only && python -m pip install --require-hashes --no-deps -r requirements-runtime-lock.txt && python -m pip check"

railway_begin_coordinated_release() {
  local root_dir="$1"
  local release_sha="$2"
  local attestation_file attestation_token canonical_root

  if [[ -n "${TALI_COORDINATED_RELEASE_SHA:-}" \
    || -n "${TALI_COORDINATED_RELEASE_ATTESTATION:-}" \
    || -n "${TALI_COORDINATED_RELEASE_TOKEN:-}" ]]; then
    echo "error: coordinated release state already exists." >&2
    return 1
  fi

  release_sha="$(git -C "$root_dir" rev-parse "${release_sha}^{commit}")"
  canonical_root="$(cd "$root_dir" && pwd -P)"
  attestation_file="$(mktemp)"
  chmod 600 "$attestation_file"
  attestation_token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  printf '%s\n%s\n%s\n' \
    "$attestation_token" "$release_sha" "$canonical_root" > "$attestation_file"

  export TALI_COORDINATED_RELEASE_SHA="$release_sha"
  export TALI_COORDINATED_RELEASE_ATTESTATION="$attestation_file"
  export TALI_COORDINATED_RELEASE_TOKEN="$attestation_token"
  TALI_COORDINATED_RELEASE_ATTESTATION_CREATED="$attestation_file"
}

railway_end_coordinated_release() {
  local attestation_file="${TALI_COORDINATED_RELEASE_ATTESTATION_CREATED:-}"
  if [[ -n "$attestation_file" ]]; then
    rm -f "$attestation_file"
  fi
  unset TALI_COORDINATED_RELEASE_SHA
  unset TALI_COORDINATED_RELEASE_ATTESTATION
  unset TALI_COORDINATED_RELEASE_TOKEN
  unset TALI_COORDINATED_RELEASE_ATTESTATION_CREATED
}

railway_assert_release_source() {
  local root_dir="$1"
  local environment="$2"

  if [[ "$environment" != "production" ]]; then
    return 0
  fi

  local guard="$root_dir/scripts/release/assert_canonical_source.sh"
  if [[ -n "${TALI_COORDINATED_RELEASE_SHA:-}" ]]; then
    "$guard" --expected-sha "$TALI_COORDINATED_RELEASE_SHA"
  else
    "$guard"
  fi
}

railway_assert_canonical_backend_dir() {
  local root_dir="$1"
  local backend_dir="$2"
  local environment="$3"

  if [[ "$environment" != "production" ]]; then
    return 0
  fi
  if [[ ! -d "$backend_dir" || ! -d "$root_dir/backend" ]]; then
    echo "error: canonical backend directory is missing." >&2
    return 1
  fi

  local actual_backend canonical_backend
  actual_backend="$(cd "$backend_dir" && pwd -P)"
  canonical_backend="$(cd "$root_dir/backend" && pwd -P)"
  if [[ "$actual_backend" != "$canonical_backend" ]]; then
    echo "error: production must deploy backend/ from the verified release worktree." >&2
    return 1
  fi
}

railway_assert_database_provenance_from_variables_file() {
  local variables_file="$1"
  local backend_dir="$2"

  python3 - "$variables_file" "$backend_dir" <<'PY'
import json
import os
import subprocess
import sys
from urllib.parse import urlsplit

variables_file, backend_dir = sys.argv[1:]
with open(variables_file, encoding="utf-8") as handle:
    payload = json.load(handle)
if not isinstance(payload, dict):
    print("error: unexpected Railway variable payload for the web service.", file=sys.stderr)
    raise SystemExit(1)

database_url = str(payload.get("DATABASE_PUBLIC_URL") or "").strip()
if not database_url:
    print(
        "error: production web service has no DATABASE_PUBLIC_URL; "
        "attach the public Postgres URL before rollout.",
        file=sys.stderr,
    )
    raise SystemExit(1)
parsed = urlsplit(database_url)
hostname = (parsed.hostname or "").lower()
if parsed.scheme not in {"postgres", "postgresql"} or not hostname:
    print("error: DATABASE_PUBLIC_URL is not a valid PostgreSQL URL.", file=sys.stderr)
    raise SystemExit(1)
if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(
    ".railway.internal"
):
    print(
        "error: DATABASE_PUBLIC_URL is not publicly reachable from the deploy host.",
        file=sys.stderr,
    )
    raise SystemExit(1)

env = os.environ.copy()
env["DATABASE_PUBLIC_URL"] = database_url
env["DATABASE_URL"] = database_url
subprocess.run(
    [sys.executable, "scripts/check_alembic_provenance.py"],
    cwd=backend_dir,
    env=env,
    check=True,
)
PY
}

railway_assert_production_database_provenance() {
  local environment="$1"
  local web_service="$2"
  local backend_dir="$3"

  if [[ "$environment" != "production" ]]; then
    return 0
  fi

  local variables_file result=0
  variables_file="$(mktemp)"
  chmod 600 "$variables_file"
  if ! railway variable list \
    --service "$web_service" \
    --environment "$environment" \
    --json > "$variables_file"; then
    echo "error: could not read production web variables for migration provenance." >&2
    rm -f "$variables_file"
    return 1
  fi
  railway_assert_database_provenance_from_variables_file \
    "$variables_file" "$backend_dir" || result=$?
  rm -f "$variables_file"
  return "$result"
}

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
  local comparison="${5:-case-insensitive}"
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

  python3 - "$variables_file" "$service" "$key" "$expected" "$comparison" <<'PY' || result=$?
import json
import sys

variables_file, service, key, expected, comparison = sys.argv[1:]
payload = json.load(open(variables_file))
if not isinstance(payload, dict):
    print(f"error: unexpected Railway variable payload for {service!r}.", file=sys.stderr)
    raise SystemExit(1)
raw_actual = str(payload.get(key) or "")
if comparison == "exact":
    actual = raw_actual
    matches = actual == expected
elif comparison == "case-insensitive":
    actual = raw_actual.strip()
    matches = actual.lower() == expected.lower()
else:
    print(
        f"error: unsupported Railway variable comparison: {comparison!r}.",
        file=sys.stderr,
    )
    raise SystemExit(1)
if not matches:
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

railway_validate_service_variable_exact() {
  railway_validate_service_variable "$1" "$2" "$3" "$4" exact
}

railway_scoring_policy_from_file() {
  local variables_file="$1"

  python3 - "$variables_file" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1]))
if not isinstance(payload, dict):
    print("error: unexpected Railway variable payload for the web service.", file=sys.stderr)
    raise SystemExit(1)

raw_threshold = str(payload.get("PRE_SCREEN_THRESHOLD") or "30").strip()
raw_gate = str(payload.get("ENABLE_PRE_SCREEN_GATE") or "false").strip().lower()
try:
    threshold = int(raw_threshold)
except ValueError:
    print("error: PRE_SCREEN_THRESHOLD must be an integer.", file=sys.stderr)
    raise SystemExit(1)
if not 0 <= threshold <= 100:
    print("error: PRE_SCREEN_THRESHOLD must be between 0 and 100.", file=sys.stderr)
    raise SystemExit(1)
if raw_gate not in {"true", "false"}:
    print("error: ENABLE_PRE_SCREEN_GATE must be true or false.", file=sys.stderr)
    raise SystemExit(1)

print(f"{threshold}\t{raw_gate}")
PY
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

railway_validate_default_agent_capabilities() {
  local base_url="${1%/}"
  local admin_secret="${RAILWAY_ADMIN_SECRET:-}"
  if [[ ${#admin_secret} -lt 32 \
    || "$admin_secret" == *$'\n'* \
    || "$admin_secret" == *$'\r'* ]]; then
    echo "error: RAILWAY_ADMIN_SECRET must contain the production ADMIN_SECRET (at least 32 characters, no newlines)." >&2
    return 1
  fi

  local health_file auth_header_file
  health_file="$(mktemp)"
  auth_header_file="$(mktemp)"
  chmod 600 "$auth_header_file"
  printf 'X-Admin-Secret: %s\n' "$admin_secret" > "$auth_header_file"
  if ! curl --fail --silent --show-error --max-time 15 \
    --header "@$auth_header_file" \
    "$base_url/admin/health" > "$health_file"; then
    rm -f "$health_file" "$auth_header_file"
    echo "error: could not read authenticated agent capability status from ${base_url}/admin/health." >&2
    return 1
  fi
  rm -f "$auth_header_file"

  local result=0
  python3 - "$health_file" <<'PY' || result=$?
import json
import sys

payload = json.load(open(sys.argv[1]))
capabilities = (
    payload.get("agent_worker", {})
    .get("queues", {})
    .get("celery", {})
    .get("capabilities", {})
)
required_true = (
    "anthropic_configured",
    "anthropic_probe_ok",
    "usage_meter_live",
    "e2b_configured",
    "resend_configured",
    "resend_probe_ok",
    "github_configured",
    "github_probe_ok",
)
errors = [key for key in required_true if capabilities.get(key) is not True]
if capabilities.get("github_mock_mode") is not False:
    errors.append("github_mock_mode=false")
if errors:
    print(
        "error: default agent assessment path is not production-ready: "
        + ", ".join(errors),
        file=sys.stderr,
    )
    raise SystemExit(1)
print(
    "Default agent capability validation passed "
    "(Anthropic, E2B, Resend delivery, and GitHub)."
)
PY
  rm -f "$health_file"
  return "$result"
}
