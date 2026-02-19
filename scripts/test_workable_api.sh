#!/bin/bash
# Test Workable API integration against production backend.
#
# Option A - Admin endpoint (no user login needed):
#   SECRET_KEY="your-railway-secret" EMAIL=sampatel@deeplight.ae ./scripts/test_workable_api.sh
#
# Option B - User token:
#   AUTH_TOKEN="..." ./scripts/test_workable_api.sh
#   EMAIL=sampatel@deeplight.ae PASSWORD="..." ./scripts/test_workable_api.sh
#
# Option C - Use Railway CLI to get SECRET_KEY:
#   SECRET_KEY=$(railway variables --json | python3 -c "import sys,json; print(json.load(sys.stdin).get('SECRET_KEY',''))")
#   EMAIL=sampatel@deeplight.ae SECRET_KEY="$SECRET_KEY" ./scripts/test_workable_api.sh

set -e

BACKEND_URL="${BACKEND_URL:-https://resourceful-adaptation-production.up.railway.app}"
API_BASE="${BACKEND_URL}/api/v1"
TEST_EMAIL="${EMAIL:-sampatel@deeplight.ae}"

if [ -n "$SECRET_KEY" ]; then
  echo "Using admin diagnostic endpoint for $TEST_EMAIL..."
  echo "========================================"
  curl -s -X GET "${API_BASE}/workable/admin/diagnostic?email=${TEST_EMAIL}" \
    -H "X-Admin-Secret: ${SECRET_KEY}" | python3 -m json.tool
  echo ""
  echo "========================================"
  echo "Done."
  exit 0
fi

if [ -z "$AUTH_TOKEN" ]; then
  if [ -z "$EMAIL" ] || [ -z "$PASSWORD" ]; then
    echo "Set SECRET_KEY + EMAIL (admin), or AUTH_TOKEN, or EMAIL + PASSWORD." 1>&2
    exit 1
  fi
  echo "Logging in as $EMAIL..."
  RESP=$(curl -s -X POST "${API_BASE}/auth/jwt/login" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=${EMAIL}&password=${PASSWORD}")
  AUTH_TOKEN=$(echo "$RESP" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || true)
  if [ -z "$AUTH_TOKEN" ]; then
    echo "Login failed: $RESP" 1>&2
    exit 1
  fi
  echo "Login OK."
fi

echo ""
echo "Calling GET /workable/diagnostic..."
echo "========================================"
curl -s -X GET "${API_BASE}/workable/diagnostic" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" | python3 -m json.tool
echo ""
echo "========================================"
echo "Done."
