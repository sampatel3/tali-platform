#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/qa/lib.sh
source "$SCRIPT_DIR/lib.sh"

API_BASE="${TAALI_API_BASE_URL:-https://resourceful-adaptation-production.up.railway.app/api/v1}"
TEST_EMAIL="${TAALI_TEST_EMAIL:-sampatel@deeplight.ae}"
TEST_PASSWORD="${TAALI_TEST_PASSWORD:-}"
unset TAALI_TEST_PASSWORD
export -n TEST_PASSWORD
ROLE_ID="${TAALI_ROLE_ID:-}"
MAX_TIME_SEC="${MAX_TIME_SEC:-10}"
HTTP_CONNECT_TIMEOUT_SEC="${HTTP_CONNECT_TIMEOUT_SEC:-5}"
HTTP_MAX_TIME_SEC="${HTTP_MAX_TIME_SEC:-30}"

qa_validate_curl_timeouts "$HTTP_CONNECT_TIMEOUT_SEC" "$HTTP_MAX_TIME_SEC"
curl_timeout_args=(
  --connect-timeout "$HTTP_CONNECT_TIMEOUT_SEC"
  --max-time "$HTTP_MAX_TIME_SEC"
)
readonly -a curl_timeout_args

if [[ -z "$TEST_PASSWORD" ]]; then
  echo "error: TAALI_TEST_PASSWORD is required" >&2
  exit 10
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

AUTH_USERNAME_FILE="$TMP_DIR/auth_username.form-value"
AUTH_PASSWORD_FILE="$TMP_DIR/auth_password.form-value"
printf '%s' "$TEST_EMAIL" > "$AUTH_USERNAME_FILE"
printf '%s' "$TEST_PASSWORD" > "$AUTH_PASSWORD_FILE"
unset TEST_PASSWORD
chmod 600 "$AUTH_USERNAME_FILE" "$AUTH_PASSWORD_FILE"
AUTH_JSON="$TMP_DIR/auth.json"
AUTH_CODE="$(curl --disable "${curl_timeout_args[@]}" -sS -o "$AUTH_JSON" -w "%{http_code}" -X POST "${API_BASE}/auth/jwt/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username@${AUTH_USERNAME_FILE}" \
  --data-urlencode "password@${AUTH_PASSWORD_FILE}")"
rm -f "$AUTH_USERNAME_FILE" "$AUTH_PASSWORD_FILE"

if [[ "$AUTH_CODE" != "200" ]]; then
  echo "error: auth failed for ${TEST_EMAIL} (HTTP ${AUTH_CODE})" >&2
  exit 11
fi

AUTH_HEADER_FILE="$TMP_DIR/auth.headers"
if ! qa_write_auth_header "$AUTH_JSON" "$AUTH_HEADER_FILE"; then
  exit 11
fi
auth_header=(--header "@${AUTH_HEADER_FILE}")

probe() {
  local endpoint="$1"
  local out_file="$TMP_DIR/resp.json"
  local metrics_file="$TMP_DIR/metrics.txt"
  curl --disable "${curl_timeout_args[@]}" -sS -o "$out_file" -w "code=%{http_code}\ntime_total=%{time_total}\n" \
    "$API_BASE$endpoint" "${auth_header[@]}" >"$metrics_file"
  local code
  local time_total
  code="$(awk -F= '/^code=/{print $2}' "$metrics_file")"
  time_total="$(awk -F= '/^time_total=/{print $2}' "$metrics_file")"
  echo "endpoint=${endpoint} code=${code} time_total=${time_total}s"
  if [[ "$code" != "200" ]]; then
    cat "$out_file" >&2
    exit 20
  fi
  python3 - "$time_total" "$MAX_TIME_SEC" <<'PY'
import sys
actual = float(sys.argv[1])
limit = float(sys.argv[2])
if actual > limit:
    print(f"error: endpoint exceeded max time ({actual:.3f}s > {limit:.3f}s)", file=sys.stderr)
    raise SystemExit(21)
PY
}

if [[ -z "$ROLE_ID" ]]; then
  ROLES_JSON="$TMP_DIR/roles.json"
  ROLES_CODE="$(curl --disable "${curl_timeout_args[@]}" -sS -o "$ROLES_JSON" -w "%{http_code}" "${API_BASE}/roles?include_pipeline_stats=true" "${auth_header[@]}")"
  if [[ "$ROLES_CODE" != "200" ]]; then
    echo "error: roles lookup failed (HTTP ${ROLES_CODE})" >&2
    cat "$ROLES_JSON" >&2
    exit 12
  fi
  ROLE_ID="$(python3 - "$ROLES_JSON" <<'PY'
import json
import sys
rows = json.load(open(sys.argv[1]))
if isinstance(rows, list) and rows:
    print(rows[0].get("id") or "")
else:
    print("")
PY
)"
fi

probe "/applications?limit=50&offset=0&application_outcome=open"
if [[ -n "$ROLE_ID" ]]; then
  probe "/roles/${ROLE_ID}/pipeline?limit=50&offset=0"
fi

echo "PASS: candidates directory smoke succeeded for ${TEST_EMAIL}"
