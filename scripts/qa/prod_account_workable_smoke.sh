#!/usr/bin/env bash
set -euo pipefail

API_BASE="${TAALI_API_BASE_URL:-https://resourceful-adaptation-production.up.railway.app/api/v1}"
TEST_EMAIL="${TAALI_TEST_EMAIL:-sampatel@deeplight.ae}"
TEST_PASSWORD="${TAALI_TEST_PASSWORD:-}"
SELECTED_JOB_SHORTCODES="${WORKABLE_JOB_SHORTCODES:-}"
PARITY_SCOPE="${PARITY_SCOPE:-auto}"
HTTP_MAX_TIME_SEC="${HTTP_MAX_TIME_SEC:-30}"
SYNC_TIMEOUT_SEC="${SYNC_TIMEOUT_SEC:-1200}"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-5}"
REQUIRE_GROWTH="${REQUIRE_GROWTH:-1}"

if [[ -z "$TEST_PASSWORD" ]]; then
  echo "error: TAALI_TEST_PASSWORD is required" >&2
  exit 10
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
  exit 11
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
  exit 11
fi

auth_header=( -H "Authorization: Bearer ${TOKEN}" )

curl_get_with_retry() {
  local url="$1"
  local output_file="$2"
  local max_attempts="${3:-4}"
  local sleep_seconds="${4:-2}"
  local code=""

  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    code="$(curl -sS --max-time "$HTTP_MAX_TIME_SEC" -o "$output_file" -w "%{http_code}" "$url" "${auth_header[@]}")"
    if [[ "$code" == "200" ]]; then
      break
    fi
    if [[ "$code" != "429" && "$code" != "500" && "$code" != "502" && "$code" != "503" && "$code" != "504" ]]; then
      break
    fi
    if (( attempt < max_attempts )); then
      sleep "$sleep_seconds"
    fi
  done

  printf '%s' "$code"
}

status_file="$TMP_DIR/status_baseline.json"
status_code="$(curl_get_with_retry "${API_BASE}/workable/sync/status" "$status_file" 6 2)"
if [[ "$status_code" != "200" ]]; then
  echo "error: failed to fetch baseline sync status (HTTP ${status_code})" >&2
  cat "$status_file" >&2
  exit 12
fi

read -r baseline_roles baseline_apps baseline_cands <<<"$(python3 - "$status_file" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
snap = payload.get("db_snapshot") or {}
roles = snap.get("roles_active", payload.get("db_roles_count", 0))
apps = snap.get("applications_active", payload.get("db_applications_count", 0))
cands = snap.get("candidates_active", 0)
print(f"{int(roles or 0)} {int(apps or 0)} {int(cands or 0)}")
PY
)"

echo "Baseline snapshot: roles=${baseline_roles} applications=${baseline_apps} candidates=${baseline_cands}"

start_payload_file="$TMP_DIR/sync_start_payload.json"
SYNC_PAYLOAD="$(python3 - "$start_payload_file" "$SELECTED_JOB_SHORTCODES" <<'PY'
import json
import sys

payload_path = sys.argv[1]
raw_shortcodes = sys.argv[2].strip()
payload = {"mode": "metadata"}

if raw_shortcodes:
    selected = []
    seen = set()
    for chunk in raw_shortcodes.replace("\n", ",").split(","):
        code = chunk.strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        selected.append(code)
    if selected:
        payload["job_shortcodes"] = selected

with open(payload_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh)

print(json.dumps(payload))
PY
)"
echo "Sync payload: ${SYNC_PAYLOAD}"

start_file="$TMP_DIR/sync_start.json"
start_code="$(curl -sS -o "$start_file" -w "%{http_code}" -X POST "${API_BASE}/workable/sync" "${auth_header[@]}" -H "Content-Type: application/json" --data "@${start_payload_file}")"
if [[ "$start_code" != "200" ]]; then
  echo "error: failed to start metadata sync (HTTP ${start_code})" >&2
  cat "$start_file" >&2
  exit 12
fi

RUN_ID="$(python3 - "$start_file" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
run_id = payload.get("run_id")
print(run_id if run_id is not None else "")
PY
)"

if [[ -z "$RUN_ID" ]]; then
  echo "error: sync start response missing run_id" >&2
  cat "$start_file" >&2
  exit 12
fi

echo "Started metadata sync run_id=${RUN_ID}"

end_ts=$(( $(date +%s) + SYNC_TIMEOUT_SEC ))
final_status=""
final_phase=""
final_errors=""
final_file="$TMP_DIR/status_final.json"

while true; do
  now_ts="$(date +%s)"
  if (( now_ts > end_ts )); then
    echo "error: sync timed out after ${SYNC_TIMEOUT_SEC}s (run_id=${RUN_ID})" >&2
    exit 13
  fi

  poll_file="$TMP_DIR/status_poll.json"
  poll_code="$(curl_get_with_retry "${API_BASE}/workable/sync/status?run_id=${RUN_ID}" "$poll_file" 6 2)"
  if [[ "$poll_code" != "200" ]]; then
    echo "error: status poll failed (HTTP ${poll_code})" >&2
    cat "$poll_file" >&2
    exit 13
  fi

  read -r in_progress phase status roles_done roles_total c_seen c_upserted errs <<<"$(python3 - "$poll_file" <<'PY'
import json
import sys
p = json.load(open(sys.argv[1]))
in_progress = "1" if p.get("sync_in_progress") else "0"
phase = p.get("phase") or "-"
status = p.get("status") or p.get("workable_last_sync_status") or "running"
roles_done = int((p.get("jobs_processed") or 0))
roles_total = int((p.get("jobs_total") or 0))
c_seen = int((p.get("candidates_seen") or 0))
c_upserted = int((p.get("candidates_upserted") or 0))
errors = p.get("errors") or []
err_text = str(errors[0]) if errors else ""
print(f"{in_progress} {phase} {status} {roles_done} {roles_total} {c_seen} {c_upserted} {err_text.replace(' ', '_')}")
PY
)"

  echo "run=${RUN_ID} phase=${phase} status=${status} roles=${roles_done}/${roles_total} candidates=${c_seen} upserted=${c_upserted}"

  if [[ "$in_progress" == "0" ]]; then
    cp "$poll_file" "$final_file"
    final_status="$status"
    final_phase="$phase"
    final_errors="$errs"
    break
  fi

  sleep "$POLL_INTERVAL_SEC"
done

if [[ "$final_status" == "failed" || "$final_status" == "cancelled" ]]; then
  echo "error: sync finished with status=${final_status} phase=${final_phase} first_error=${final_errors}" >&2
  cat "$final_file" >&2
  exit 14
fi

read -r final_roles final_apps final_cands <<<"$(python3 - "$final_file" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
snap = payload.get("db_snapshot") or {}
roles = snap.get("roles_active", payload.get("db_roles_count", 0))
apps = snap.get("applications_active", payload.get("db_applications_count", 0))
cands = snap.get("candidates_active", 0)
print(f"{int(roles or 0)} {int(apps or 0)} {int(cands or 0)}")
PY
)"

echo "Final snapshot: roles=${final_roles} applications=${final_apps} candidates=${final_cands}"

if [[ "$REQUIRE_GROWTH" == "1" ]]; then
  if (( final_roles <= baseline_roles )) || (( final_apps <= baseline_apps )); then
    echo "error: expected roles/applications to grow beyond baseline. baseline=${baseline_roles}/${baseline_apps}, final=${final_roles}/${final_apps}" >&2
    exit 15
  fi
fi

roles_file="$TMP_DIR/roles.json"
roles_code="$(curl_get_with_retry "${API_BASE}/roles" "$roles_file" 6 3)"
if [[ "$roles_code" != "200" ]]; then
  echo "error: failed to fetch roles for parity check (HTTP ${roles_code})" >&2
  cat "$roles_file" >&2
  exit 16
fi

effective_parity_scope="$(printf '%s' "$PARITY_SCOPE" | tr '[:upper:]' '[:lower:]')"
if [[ "$effective_parity_scope" == "auto" ]]; then
  if [[ -n "$SELECTED_JOB_SHORTCODES" ]]; then
    effective_parity_scope="selected"
  else
    effective_parity_scope="all"
  fi
fi
if [[ "$effective_parity_scope" != "all" && "$effective_parity_scope" != "selected" ]]; then
  echo "error: PARITY_SCOPE must be one of auto|all|selected (got '${PARITY_SCOPE}')" >&2
  exit 16
fi

parity_meta="$(python3 - "$roles_file" "$effective_parity_scope" "$SELECTED_JOB_SHORTCODES" <<'PY'
import json
import sys

rows = json.load(open(sys.argv[1]))
scope = (sys.argv[2] or "all").strip().lower()
selected_raw = sys.argv[3] if len(sys.argv) > 3 else ""

selected = {
    token.strip().upper()
    for token in selected_raw.replace("\n", ",").split(",")
    if token.strip()
}
if not isinstance(rows, list):
    rows = []

role_ids = []
matched_shortcodes = set()

for row in rows:
    role_id = row.get("id")
    shortcode = (row.get("workable_job_id") or "").strip().upper()
    if scope == "selected":
        if role_id is not None and shortcode in selected:
            role_ids.append(str(role_id))
            matched_shortcodes.add(shortcode)
    else:
        if role_id is not None:
            role_ids.append(str(role_id))

print(f"{' '.join(role_ids)}|{len(rows)}|{len(matched_shortcodes)}|{len(selected)}")
PY
)"

IFS='|' read -r ROLE_IDS roles_api_total matched_selected_shortcodes selected_shortcodes_count <<<"$parity_meta"
roles_api_total="${roles_api_total:-0}"
matched_selected_shortcodes="${matched_selected_shortcodes:-0}"
selected_shortcodes_count="${selected_shortcodes_count:-0}"

if [[ "$effective_parity_scope" == "selected" && -z "$ROLE_IDS" ]]; then
  echo "error: selected parity scope found no role rows for shortcodes='${SELECTED_JOB_SHORTCODES}'" >&2
  exit 16
fi

ui_role_count=0
ui_app_count=0
for role_id in $ROLE_IDS; do
  ui_role_count=$((ui_role_count + 1))
  apps_file="$TMP_DIR/role_${role_id}_apps.json"
  apps_code="$(curl_get_with_retry "${API_BASE}/roles/${role_id}/applications?include_cv_text=false" "$apps_file" 6 2)"
  if [[ "$apps_code" != "200" ]]; then
    echo "error: failed to fetch applications for role ${role_id} (HTTP ${apps_code})" >&2
    cat "$apps_file" >&2
    exit 16
  fi
  app_len="$(python3 - "$apps_file" <<'PY'
import json
import sys
rows = json.load(open(sys.argv[1]))
print(len(rows) if isinstance(rows, list) else 0)
PY
)"
  ui_app_count=$((ui_app_count + app_len))
done

echo "Parity snapshot: scope=${effective_parity_scope} roles_checked=${ui_role_count} applications_sum=${ui_app_count}"

if [[ "$effective_parity_scope" == "all" ]]; then
  if (( ui_role_count != final_roles )); then
    echo "error: roles parity mismatch (roles API=${ui_role_count}, sync snapshot=${final_roles})" >&2
    exit 16
  fi

  if (( ui_app_count != final_apps )); then
    echo "error: applications parity mismatch (roles/*/applications sum=${ui_app_count}, sync snapshot=${final_apps})" >&2
    exit 16
  fi
else
  if (( matched_selected_shortcodes != selected_shortcodes_count )); then
    echo "error: selected-role parity mismatch (selected_shortcodes=${selected_shortcodes_count}, matched_roles=${matched_selected_shortcodes}, roles_api_total=${roles_api_total})" >&2
    exit 16
  fi
fi

echo "PASS: Workable production-account smoke succeeded for ${TEST_EMAIL} (run_id=${RUN_ID})."
