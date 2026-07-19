#!/usr/bin/env bash
# Test Workable API integration against production backend.
#
# Option A - Admin endpoint (no user login needed):
#   set +x
#   read -r -s -p "Admin secret: " admin_secret; printf '\n'
#   ADMIN_SECRET="$admin_secret" EMAIL=sampatel@deeplight.ae ./scripts/test_workable_api.sh
#   unset admin_secret
#
# Option B - User token:
#   set +x
#   read -r -s -p "Auth token: " auth_token; printf '\n'
#   AUTH_TOKEN="$auth_token" ./scripts/test_workable_api.sh
#   unset auth_token
#
# Option C - User email/password:
#   set +x
#   read -r -s -p "Password: " password; printf '\n'
#   EMAIL=sampatel@deeplight.ae PASSWORD="$password" ./scripts/test_workable_api.sh
#   unset password
#
# For unattended use, populate the short-lived lower-case variable with an
# approved secret-store command that does not echo values. Never put a literal
# credential in shell history or export it into the long-lived operator shell.

set -euo pipefail
# Never let an inherited shell trace print credentials while they are copied
# into private curl input files.
set +x
umask 077

SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
if [ "$SCRIPT_DIR" = "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="."
fi
# shellcheck source=scripts/qa/lib.sh
source "$SCRIPT_DIR/qa/lib.sh"

TEST_EMAIL="${EMAIL:-sampatel@deeplight.ae}"
ADMIN_SECRET_VALUE="${ADMIN_SECRET:-}"
AUTH_TOKEN_VALUE="${AUTH_TOKEN:-}"
PASSWORD_VALUE="${PASSWORD:-}"
unset ADMIN_SECRET AUTH_TOKEN PASSWORD
export -n ADMIN_SECRET_VALUE AUTH_TOKEN_VALUE PASSWORD_VALUE

BACKEND_URL="${BACKEND_URL:-https://resourceful-adaptation-production.up.railway.app}"
BACKEND_URL="${BACKEND_URL%/}"
HTTP_CONNECT_TIMEOUT_SEC="${HTTP_CONNECT_TIMEOUT_SEC:-5}"
HTTP_MAX_TIME_SEC="${HTTP_MAX_TIME_SEC:-30}"
qa_validate_curl_timeouts "$HTTP_CONNECT_TIMEOUT_SEC" "$HTTP_MAX_TIME_SEC"
case "$BACKEND_URL" in
  https://*) CURL_PROTOCOLS="=https" ;;
  http://127.0.0.1:*|http://localhost:*) CURL_PROTOCOLS="=http" ;;
  *)
    echo "BACKEND_URL must use HTTPS (or HTTP on an explicit loopback host)." >&2
    exit 1
    ;;
esac
curl_args=(
  --disable
  --no-location
  --proto "$CURL_PROTOCOLS"
  --proto-redir "=https"
  --connect-timeout "$HTTP_CONNECT_TIMEOUT_SEC"
  --max-time "$HTTP_MAX_TIME_SEC"
  --silent
  --show-error
)
API_BASE="${BACKEND_URL}/api/v1"

ADMIN_HEADER_FILE=""
AUTH_HEADER_FILE=""
ADMIN_EMAIL_FILE=""
FORM_USERNAME_FILE=""
FORM_PASSWORD_FILE=""
RESPONSE_FILE=""

_cleanup() {
  rm -f -- \
    "$ADMIN_HEADER_FILE" \
    "$AUTH_HEADER_FILE" \
    "$ADMIN_EMAIL_FILE" \
    "$FORM_USERNAME_FILE" \
    "$FORM_PASSWORD_FILE" \
    "$RESPONSE_FILE"
}
trap _cleanup EXIT

ADMIN_HEADER_FILE="$(mktemp "${TMPDIR:-/tmp}/taali-workable-admin-header.XXXXXX")"
AUTH_HEADER_FILE="$(mktemp "${TMPDIR:-/tmp}/taali-workable-auth-header.XXXXXX")"
ADMIN_EMAIL_FILE="$(mktemp "${TMPDIR:-/tmp}/taali-workable-admin-email.XXXXXX")"
FORM_USERNAME_FILE="$(mktemp "${TMPDIR:-/tmp}/taali-workable-username.XXXXXX")"
FORM_PASSWORD_FILE="$(mktemp "${TMPDIR:-/tmp}/taali-workable-password.XXXXXX")"
RESPONSE_FILE="$(mktemp "${TMPDIR:-/tmp}/taali-workable-response.XXXXXX")"
chmod 600 \
  "$ADMIN_HEADER_FILE" \
  "$AUTH_HEADER_FILE" \
  "$ADMIN_EMAIL_FILE" \
  "$FORM_USERNAME_FILE" \
  "$FORM_PASSWORD_FILE" \
  "$RESPONSE_FILE"

HAS_ADMIN_SECRET=0
HAS_AUTH_TOKEN=0
HAS_LOGIN_CREDENTIALS=0
if [ -n "$ADMIN_SECRET_VALUE" ]; then
  if [[ "$ADMIN_SECRET_VALUE" == *$'\n'* || "$ADMIN_SECRET_VALUE" == *$'\r'* ]]; then
    echo "ADMIN_SECRET must not contain newlines." >&2
    exit 1
  fi
  printf 'X-Admin-Secret: %s\n' "$ADMIN_SECRET_VALUE" > "$ADMIN_HEADER_FILE"
  printf '%s' "$TEST_EMAIL" > "$ADMIN_EMAIL_FILE"
  HAS_ADMIN_SECRET=1
fi

if [ -n "$AUTH_TOKEN_VALUE" ]; then
  if [[ "$AUTH_TOKEN_VALUE" == *$'\n'* || "$AUTH_TOKEN_VALUE" == *$'\r'* ]]; then
    echo "AUTH_TOKEN must not contain newlines." >&2
    exit 1
  fi
  printf 'Authorization: Bearer %s\n' "$AUTH_TOKEN_VALUE" > "$AUTH_HEADER_FILE"
  HAS_AUTH_TOKEN=1
elif [ -n "${EMAIL:-}" ] && [ -n "$PASSWORD_VALUE" ]; then
  printf '%s' "$EMAIL" > "$FORM_USERNAME_FILE"
  printf '%s' "$PASSWORD_VALUE" > "$FORM_PASSWORD_FILE"
  HAS_LOGIN_CREDENTIALS=1
fi

# All children receive only private file paths, never credentials inherited
# from the caller's environment.
unset ADMIN_SECRET_VALUE AUTH_TOKEN_VALUE PASSWORD_VALUE

if [ "$HAS_ADMIN_SECRET" -eq 1 ]; then
  echo "Trying admin diagnostic for $TEST_EMAIL..."
  HTTP="$(
    curl "${curl_args[@]}" \
      --output "$RESPONSE_FILE" \
      --write-out "%{http_code}" \
      --get "${API_BASE}/workable/admin/diagnostic" \
      --data-urlencode "email@${ADMIN_EMAIL_FILE}" \
      --header "@${ADMIN_HEADER_FILE}"
  )"
  : > "$ADMIN_HEADER_FILE"
  : > "$ADMIN_EMAIL_FILE"
  if [ "$HTTP" = "200" ]; then
    python3 -m json.tool < "$RESPONSE_FILE"
    echo "Done (admin)."
    exit 0
  fi
  echo "Admin diagnostic returned $HTTP (endpoint may not be deployed). Use EMAIL + PASSWORD instead."
  : > "$RESPONSE_FILE"
fi

if [ "$HAS_AUTH_TOKEN" -eq 1 ]; then
  :
else
  if [ "$HAS_LOGIN_CREDENTIALS" -ne 1 ]; then
    echo "Set ADMIN_SECRET + EMAIL (admin), or AUTH_TOKEN, or EMAIL + PASSWORD." 1>&2
    exit 1
  fi
  echo "Logging in as $EMAIL..."
  HTTP="$(
    curl "${curl_args[@]}" \
      --output "$RESPONSE_FILE" \
      --write-out "%{http_code}" \
      --request POST "${API_BASE}/auth/jwt/login" \
      --header "Content-Type: application/x-www-form-urlencoded" \
      --data-urlencode "username@${FORM_USERNAME_FILE}" \
      --data-urlencode "password@${FORM_PASSWORD_FILE}"
  )"
  : > "$FORM_USERNAME_FILE"
  : > "$FORM_PASSWORD_FILE"

  if ! python3 - "$RESPONSE_FILE" "$AUTH_HEADER_FILE" <<'PY'
import json
import os
from pathlib import Path
import sys

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
token = payload.get("access_token") if isinstance(payload, dict) else None
if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
    raise SystemExit(1)
Path(sys.argv[2]).write_text(
    f"Authorization: Bearer {token}\n",
    encoding="utf-8",
)
os.chmod(sys.argv[2], 0o600)
PY
  then
    echo "Login failed (HTTP $HTTP); response body withheld." 1>&2
    exit 1
  fi
  : > "$RESPONSE_FILE"
  echo "Login OK."
fi

echo ""
echo "Calling GET /workable/sync/status?include_diagnostic=true (works even if diagnostic route not deployed)..."
echo "========================================"
if ! curl "${curl_args[@]}" --fail --request GET \
  --output "$RESPONSE_FILE" \
  "${API_BASE}/workable/sync/status?include_diagnostic=true" \
  --header "@${AUTH_HEADER_FILE}"; then
  echo "Workable status request failed; response body withheld." >&2
  exit 1
fi
python3 -m json.tool < "$RESPONSE_FILE"
echo ""
echo "========================================"
echo "Done."
