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
EXPECTED_MODEL="${EXPECTED_CLAUDE_MODEL:-claude-haiku-4-5-20251001}"
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
  exit 20
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
  exit 21
fi

AUTH_HEADER_FILE="$TMP_DIR/auth.headers"
if ! qa_write_auth_header "$AUTH_JSON" "$AUTH_HEADER_FILE"; then
  exit 21
fi

ORG_JSON="$TMP_DIR/org.json"
ORG_CODE="$(curl --disable "${curl_timeout_args[@]}" -sS -o "$ORG_JSON" -w "%{http_code}" "${API_BASE}/organizations/me" --header "@${AUTH_HEADER_FILE}")"
if [[ "$ORG_CODE" != "200" ]]; then
  echo "error: failed to fetch /organizations/me (HTTP ${ORG_CODE})" >&2
  cat "$ORG_JSON" >&2
  exit 22
fi

ACTIVE_MODEL="$(python3 - "$ORG_JSON" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
print(payload.get("active_claude_model") or "")
PY
)"

if [[ -z "$ACTIVE_MODEL" ]]; then
  echo "error: organizations payload missing active_claude_model" >&2
  cat "$ORG_JSON" >&2
  exit 22
fi

echo "Active model: ${ACTIVE_MODEL}"
if [[ "$ACTIVE_MODEL" != "$EXPECTED_MODEL" ]]; then
  echo "error: active model mismatch. expected='${EXPECTED_MODEL}' actual='${ACTIVE_MODEL}'" >&2
  exit 23
fi

echo "PASS: model smoke check succeeded for ${TEST_EMAIL}."
