#!/usr/bin/env bash
set -euo pipefail

API_BASE="${TAALI_API_BASE_URL:-https://resourceful-adaptation-production.up.railway.app/api/v1}"
TEST_EMAIL="${TAALI_TEST_EMAIL:-sampatel@deeplight.ae}"
TEST_PASSWORD="${TAALI_TEST_PASSWORD:-}"
EXPECTED_MODEL="${EXPECTED_CLAUDE_MODEL:-claude-3-5-haiku-latest}"

if [[ -z "$TEST_PASSWORD" ]]; then
  echo "error: TAALI_TEST_PASSWORD is required" >&2
  exit 20
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

AUTH_JSON="$TMP_DIR/auth.json"
AUTH_CODE="$(curl -sS -o "$AUTH_JSON" -w "%{http_code}" -X POST "${API_BASE}/auth/jwt/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=${TEST_EMAIL}" \
  --data-urlencode "password=${TEST_PASSWORD}")"

if [[ "$AUTH_CODE" != "200" ]]; then
  echo "error: auth failed for ${TEST_EMAIL} (HTTP ${AUTH_CODE})" >&2
  cat "$AUTH_JSON" >&2
  exit 21
fi

TOKEN="$(python3 - "$AUTH_JSON" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
print(payload.get("access_token") or "")
PY
)"

if [[ -z "$TOKEN" ]]; then
  echo "error: auth response missing access_token" >&2
  exit 21
fi

ORG_JSON="$TMP_DIR/org.json"
ORG_CODE="$(curl -sS -o "$ORG_JSON" -w "%{http_code}" "${API_BASE}/organizations/me" -H "Authorization: Bearer ${TOKEN}")"
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
