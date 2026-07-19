# Bullhorn Live Validation Runbook (operator)

Commands and gates for validating the Bullhorn connector against a real client
instance, staging-only, flag-gated. This is an operator checklist — not
client-facing prose. Pairs with:

- `docs/BULLHORN_BUILD_PLAN.md` §0.2 (test-access strategy) and §8 (verification gates)
- `docs/BULLHORN_CLIENT_ONBOARDING.md` §2–§4 (credential ticket, admin checklist, test records)

Golden rule: **every write class needs an explicit go from Sam.** The instance
holds production data. Read-only first; writes only against client-created test
records.

Rollback at any point: set `BULLHORN_ENABLED=off` (per env) and/or clear the
org's `bullhorn_connected` flag — the resolver then routes nothing to Bullhorn
and every hook is a no-op. The flag defaults to `False`, so "off" is the safe
resting state.

---

## Prerequisites (before Phase 1)

- [ ] Credentials received from the client per onboarding §2 (Bullhorn support
      ticket): `BULLHORN_CLIENT_ID`, `BULLHORN_CLIENT_SECRET`, plus the API
      user's `BULLHORN_USERNAME` / `BULLHORN_PASSWORD`.
- [ ] API user entitlements confirmed per onboarding §3. The connect pre-flight
      enforces exactly these (see `bullhorn_sync/connect.py::_REQUIRED_ENTITLEMENTS`):
      - Candidate: `GET`
      - JobOrder: `GET`
      - JobSubmission: `GET`, `POST`
      - Note: `PUT`
- [ ] Edition pre-checks: classic Bullhorn ATS, **REST API** (not Recruitment
      Cloud). Confirm the instance's monthly REST API call limit (onboarding §1)
      is comfortably above expected sync volume.
- [ ] Test records created and owned by the client per onboarding §4: one
      JobOrder + two Candidates + their JobSubmissions, clearly named (e.g.
      "TAALI TEST — do not action").
- [ ] **Redirect URI decision.** The onboarding ticket (§2) registers
      `https://www.taali.ai/integrations/bullhorn/callback`. Default plan: that
      URL must be **live before Phase 1**. Alternative: if the OAuth key has a
      single registered redirect URI, the connect flow may omit `redirect_uri`
      entirely and Bullhorn uses the registered one (`BullhornAuth` supports a
      `None` redirect_uri). Pick one and confirm before connecting.
- [ ] Staging deployed with the `bullhorn` branch; `BULLHORN_ENABLED` set on the
      **staging** service only. Never on prod.

Prepare permission-restricted request files once in a dedicated validation
shell. Disable inherited command tracing and set the private file-creation mask
before loading credentials from the approved secret store (never shell
history). This keeps the staging JWT and Bullhorn credentials out of process
arguments and child-process environments; the exit trap removes every file
even when a later command fails.

```bash
set -eu
set +x
umask 077
unset STAGING_ADMIN_JWT BULLHORN_USERNAME BULLHORN_CLIENT_ID \
  BULLHORN_CLIENT_SECRET BULLHORN_PASSWORD

read -r -p 'Staging base URL (https://...; no trailing path): ' STAGING
python3 - "$STAGING" <<'PY'
from urllib.parse import urlsplit
import sys

raw = sys.argv[1]
value = raw.rstrip("/")
parsed = urlsplit(value)
if (
    not value
    or raw != raw.strip()
    or raw not in {value, value + "/"}
    or "\r" in value
    or "\n" in value
    or parsed.scheme != "https"
    or not parsed.hostname
    or parsed.username is not None
    or parsed.password is not None
    or parsed.path not in {"", "/"}
    or parsed.query
    or parsed.fragment
):
    raise SystemExit("invalid staging base URL: require an HTTPS origin with no userinfo/path")
PY
STAGING="${STAGING%/}"

VALIDATION_TMP_DIR="$(mktemp -d)"
chmod 700 "$VALIDATION_TMP_DIR"
cleanup_validation_files() { rm -rf -- "$VALIDATION_TMP_DIR"; }
trap cleanup_validation_files EXIT

STAGING_AUTH_HEADER_FILE="$VALIDATION_TMP_DIR/staging-auth.headers"
BULLHORN_CONNECT_PAYLOAD_FILE="$VALIDATION_TMP_DIR/bullhorn-connect.json"
BULLHORN_CAPTURE_TOKEN_STATE_FILE="$VALIDATION_TMP_DIR/bullhorn-capture-token.json"
BULLHORN_USERNAME_FILE="$VALIDATION_TMP_DIR/bullhorn-username.raw"
BULLHORN_CLIENT_ID_FILE="$VALIDATION_TMP_DIR/bullhorn-client-id.raw"
BULLHORN_CLIENT_SECRET_FILE="$VALIDATION_TMP_DIR/bullhorn-client-secret.raw"
BULLHORN_PASSWORD_FILE="$VALIDATION_TMP_DIR/bullhorn-password.raw"
BULLHORN_PII_PATTERN_FILE="$VALIDATION_TMP_DIR/known-client-values.patterns"

# Silent prompts keep values out of history. For unattended validation, replace
# these reads with approved secret-store assignments that do not echo or export.
read -r -s -p 'Staging admin JWT: ' STAGING_ADMIN_JWT; printf '\n'
read -r -s -p 'Bullhorn API username: ' BULLHORN_USERNAME; printf '\n'
read -r -s -p 'Bullhorn client ID: ' BULLHORN_CLIENT_ID; printf '\n'
read -r -s -p 'Bullhorn client secret: ' BULLHORN_CLIENT_SECRET; printf '\n'
read -r -s -p 'Bullhorn API password: ' BULLHORN_PASSWORD; printf '\n'

if [[ -z "$STAGING_ADMIN_JWT" \
      || ${#STAGING_ADMIN_JWT} -gt 16384 \
      || "$STAGING_ADMIN_JWT" == *$'\r'* \
      || "$STAGING_ADMIN_JWT" == *$'\n'* ]]; then
  printf '%s\n' 'Invalid staging admin JWT' >&2
  exit 1
fi
printf 'Authorization: Bearer %s\n' "$STAGING_ADMIN_JWT" > "$STAGING_AUTH_HEADER_FILE"
printf '%s' "$BULLHORN_USERNAME" > "$BULLHORN_USERNAME_FILE"
printf '%s' "$BULLHORN_CLIENT_ID" > "$BULLHORN_CLIENT_ID_FILE"
printf '%s' "$BULLHORN_CLIENT_SECRET" > "$BULLHORN_CLIENT_SECRET_FILE"
printf '%s' "$BULLHORN_PASSWORD" > "$BULLHORN_PASSWORD_FILE"
: > "$BULLHORN_CONNECT_PAYLOAD_FILE"
: > "$BULLHORN_PII_PATTERN_FILE"
unset STAGING_ADMIN_JWT BULLHORN_USERNAME BULLHORN_CLIENT_ID \
  BULLHORN_CLIENT_SECRET BULLHORN_PASSWORD
chmod 600 \
  "$STAGING_AUTH_HEADER_FILE" \
  "$BULLHORN_CONNECT_PAYLOAD_FILE" \
  "$BULLHORN_USERNAME_FILE" \
  "$BULLHORN_CLIENT_ID_FILE" \
  "$BULLHORN_CLIENT_SECRET_FILE" \
  "$BULLHORN_PASSWORD_FILE" \
  "$BULLHORN_PII_PATTERN_FILE"

python3 - "$VALIDATION_TMP_DIR" "$BULLHORN_CONNECT_PAYLOAD_FILE" <<'PY'
import json
import os
from pathlib import Path
import sys

secret_names = {
    "STAGING_ADMIN_JWT",
    "BULLHORN_USERNAME",
    "BULLHORN_CLIENT_ID",
    "BULLHORN_CLIENT_SECRET",
    "BULLHORN_PASSWORD",
}
if secret_names & os.environ.keys():
    raise SystemExit("credential environment was not cleared before payload build")

raw_dir, payload_path = map(Path, sys.argv[1:])
credential_files = {
    "username": raw_dir / "bullhorn-username.raw",
    "client_id": raw_dir / "bullhorn-client-id.raw",
    "client_secret": raw_dir / "bullhorn-client-secret.raw",
    "password": raw_dir / "bullhorn-password.raw",
}
payload = {}
for name, path in credential_files.items():
    value = path.read_text(encoding="utf-8")
    if not value or "\r" in value or "\n" in value:
        raise SystemExit(f"invalid Bullhorn credential input: {name}")
    payload[name] = value
payload_path.write_text(
    json.dumps(payload, separators=(",", ":")),
    encoding="utf-8",
)
PY

for credential_file in \
  "$BULLHORN_USERNAME_FILE" \
  "$BULLHORN_CLIENT_ID_FILE" \
  "$BULLHORN_CLIENT_SECRET_FILE" \
  "$BULLHORN_PASSWORD_FILE"; do
  : > "$credential_file"
  rm -f -- "$credential_file"
done
```

---

## Phase 1 — Staging validation (explicit subscription-write approval)

Get Sam's explicit approval for the isolated Bullhorn subscription writes in
this phase before starting. The capture creates and deletes one uniquely named
temporary subscription. The connect-triggered sync establishes Taali's durable
subscription. No Candidate, JobOrder, JobSubmission, or Note is mutated.

1. **Capture fixtures first, before the first `/connect`.** The capture login
   rotates Bullhorn's single-use refresh-token chain. The required private token
   artifact retains every rotated refresh token atomically before the capture
   uses its access token, so a capture failure cannot silently strand the chain.
   Keep that mode-0600 artifact until `/connect` succeeds; never copy it into the
   fixture directory, logs, shell variables, tickets, or source control.

   Use the dedicated client-created test JobOrder, then make one harmless manual
   change to that test record in the Bullhorn UI while the bounded event wait is
   running. This proves the real official `ENTITY` / `entityEventType` envelope;
   an immediate empty poll alone validates only subscription lifecycle.

   ```bash
   read -r -p 'Dedicated Bullhorn test JobOrder ID: ' BULLHORN_TEST_JOB_ORDER_ID
   case "$BULLHORN_TEST_JOB_ORDER_ID" in
     ''|*[!0-9]*) printf '%s\n' 'Invalid test JobOrder ID' >&2; exit 1 ;;
   esac
   python backend/scripts/bullhorn_capture_fixtures.py --allow-live \
     --credentials-file "$BULLHORN_CONNECT_PAYLOAD_FILE" \
     --token-state-file "$BULLHORN_CAPTURE_TOKEN_STATE_FILE" \
     --out tests/fixtures/bullhorn_recorded --max 5 \
     --job-order-id "$BULLHORN_TEST_JOB_ORDER_ID" \
     --require-event --event-wait-seconds 120
   unset BULLHORN_TEST_JOB_ORDER_ID
   test "$(stat -f '%Lp' "$BULLHORN_CAPTURE_TOKEN_STATE_FILE" 2>/dev/null || \
     stat -c '%a' "$BULLHORN_CAPTURE_TOKEN_STATE_FILE")" = 600
   ```

   Add `--candidate-id <dedicated-test-candidate-id>` only when the chosen test
   JobOrder has no submission from which the tool can derive a Candidate.

   - [ ] Fixtures written for ping, status_list, entitlements, job_orders,
         job_submissions, event_subscription_create, and a non-empty event_poll.
   - [ ] Every serialized JobSubmission's `jobOrder.id` belongs to a serialized
         captured JobOrder; the explicit test JobOrder is present.
   - [ ] When a candidate ID is supplied or available from a submission,
         `candidate.json`, `notes.json`, and `file_attachments.json` are present.
         `job_submission_history.json` is present only when submissions exist.
   - [ ] Spot-check one file: names/emails/phones are faked; free text, numeric
         values, identifiers, and dates are pseudonymized; no token survives.
   - [ ] The rotated-token artifact exists outside the fixtures and is mode 0600.
         If capture failed, preserve it privately and stop for credential-chain
         recovery; do not retry provider calls with an older refresh token.

   Verify known client values without putting PII or credentials in `grep` argv,
   shell history, or output. Enter optional known PII values silently, one per
   prompt; submit an empty value to finish. The verifier also reads all four
   credentials directly from the private payload file and reports filenames
   only. Never pass a known real value as a command-line argument.

   ```bash
   while true; do
     read -r -s -p 'Known client value to reject (empty to run): ' known_client_value
     printf '\n'
     [[ -n "$known_client_value" ]] || break
     printf '%s\n' "$known_client_value" >> "$BULLHORN_PII_PATTERN_FILE"
     unset known_client_value
   done
   unset known_client_value
   python3 - "$BULLHORN_PII_PATTERN_FILE" \
     "$BULLHORN_CONNECT_PAYLOAD_FILE" tests/fixtures/bullhorn_recorded <<'PY'
import json
from pathlib import Path
import sys

pattern_path, credential_path, fixture_dir = map(Path, sys.argv[1:])
credentials = json.loads(credential_path.read_text(encoding="utf-8"))
patterns = [value for value in credentials.values() if isinstance(value, str) and value]
patterns.extend(
    value for value in pattern_path.read_text(encoding="utf-8").splitlines() if value
)
offenders = []
for fixture_path in fixture_dir.rglob("*.json"):
    fixture = fixture_path.read_text(encoding="utf-8")
    if any(pattern in fixture for pattern in patterns):
        offenders.append(str(fixture_path))
if offenders:
    raise SystemExit("known client value found in: " + ", ".join(offenders))
print("fixture secret/PII verifier: clean")
PY
   : > "$BULLHORN_PII_PATTERN_FILE"
   ```

   - [ ] The uniquely named temporary subscription
         (`TaaliFixtureCapture-<random>-DELETE-ME`) was deleted. If the script
         warns cleanup was not confirmed, stop and delete only that exact owned
         identifier manually before continuing.

2. **Connect immediately after capture** (discovery → OAuth → entitlement
   pre-flight → status-list fetch → stage-map seed → tracked initial full sync):

   ```bash
   curl -sS -X POST "$STAGING/api/v1/bullhorn/connect" \
     --header "@$STAGING_AUTH_HEADER_FILE" \
     --header "Content-Type: application/json" \
     --data-binary "@$BULLHORN_CONNECT_PAYLOAD_FILE"
   ```

   - [ ] Response reports `bullhorn_connected: true`, a non-zero
         `seeded_stage_rows`, and an `initial_sync` run/status path.
   - [ ] If it fails on entitlements, the message names the missing entity/verb —
         send that back to the client's admin (onboarding §3).

   After a confirmed successful connect has durably stored its own new rotated
   token, securely remove the capture-only recovery artifact:

   ```bash
   : > "$BULLHORN_CAPTURE_TOKEN_STATE_FILE"
   rm -f -- "$BULLHORN_CAPTURE_TOKEN_STATE_FILE"
   ```

3. **Poll the connect-triggered initial full sync; do not enqueue a duplicate.**

   ```bash
   curl -sS "$STAGING/api/v1/bullhorn/sync/status" --header "@$STAGING_AUTH_HEADER_FILE"
   curl -sS "$STAGING/api/v1/bullhorn/status"      --header "@$STAGING_AUTH_HEADER_FILE"
   ```

   `/connect` already calls `connect_and_start_full_sync`. Only if its returned
   `initial_sync` explicitly reports dispatch fallback/failure, and after checking
   no run is active, use this manual retry once:

   ```bash
   curl -sS -X POST "$STAGING/api/v1/bullhorn/sync" \
     --header "@$STAGING_AUTH_HEADER_FILE" \
     --header "Content-Type: application/json" \
     --data-binary '{"mode":"full"}'
   ```

   Eyeball checklist:
   - [ ] Role count ≈ open JobOrders in Bullhorn.
   - [ ] Candidate/application counts ≈ JobSubmissions on those jobs.
   - [ ] `unmapped_status_count` is sane; any unmapped statuses are listed and
         a stage-map row can be added in the settings UI.
   - [ ] Stage-map **read** direction looks right: a JobSubmission whose remote
         status is an interview/placed/rejected category resolves to the right
         Taali stage (not stuck at funnel top unless genuinely unmapped).
   - [ ] `event_subscription_active: true` after the sync established the
         subscription (event-driven incremental path is armed).
   - [ ] Applied date populated: pick an imported application and confirm the
         applied date (from JobSubmission `dateAdded`) shows on the decision
         surface, not blank.
   - [ ] No candidate emails were sent (assessments-only policy; job comms stay
         in the ATS).

STOP. Do not proceed to writes without Sam's go.

---

## Subscription provenance, migration, and recovery invariants

- Taali owns only the deterministic subscription
  `taali-<environment-namespace>-org-<organization-id>`. Local lifecycle state
  must prove that exact identifier, namespace, active/pending state, and anchor
  epoch before any event API call. A legacy, foreign-environment, or cloned
  subscription is reported as `event_subscription_health: invalid_provenance`;
  Taali makes no provider call, does not mutate the local checkpoint, and never
  auto-deletes or auto-adopts the remote identifier.
- Legacy adoption is an explicit maintenance change, never an automatic deploy
  side effect. Pause Bullhorn workers for the org, acquire its provider mutex,
  record the old identifier and current environment namespace, and have an
  authorized operator either (a) seed a pending lifecycle for the exact expected
  deterministic identifier or (b) clear the unproven local lifecycle so Taali
  creates that expected identifier. Resume only after a complete gap rehydrate
  and ID-set reconciliation succeeds. Delete a legacy remote subscription only
  after separately proving it belongs to Taali and recording approval; never
  delete an unknown or foreign identifier.
- Fresh/recreated/retention-gap recovery rehydrates every active submission,
  Candidate profile/CV, Note revision, and exact deletion tombstone before it
  advances the watermark. Ordinary incremental sweeps remain watermark-limited.
  The saved watermark is the run start, not finish, so changes arriving during
  the sweep are included next time. Complete active-ID reconciliation repairs a
  physically missing Bullhorn-only application; Workable-linked records remain
  authoritative and are excluded.
- A Note `DELETED` event revokes every imported revision, retains an append-only
  audit tombstone, and sets `for_agent: false`; deleted/superseded note text is
  excluded from agent context. A later recreate produces a distinct live
  revision, and a second delete produces a distinct tombstone.
- `retry_pending` is operationally degraded, not success. Beat reports
  `status: degraded` and `/bullhorn/status` reports pending checkpoint, poison,
  lifecycle, or provenance health until the exact retry/recovery completes.

The Redis per-org mutex, ownership heartbeat, final pre-commit ownership guards,
Postgres row locks, durable event intent, and epoch compare-and-swap close the
known worker-overlap races. They cannot make Bullhorn and Postgres one atomic
transaction: a process can still die after a remote call and before a local
commit, or a human/integration can edit Bullhorn concurrently. Exact replay,
idempotent upserts, complete gap rehydrate, and nightly reconciliation are the
recovery mechanisms. Production validation therefore requires real Redis and
Postgres; SQLite unit tests do not prove cross-process row-lock behaviour.

---

## Phase 2 — Gated writes (explicit go per write class)

Writes ONLY against the client's §4 test records. Get an explicit go from Sam for
**each** of: note, move (advance), reject. Verify each in the Bullhorn UI.

For each test record, drive the write through the normal decision/notes surfaces
in staging (not by hand-calling the client), then confirm in Bullhorn:

- [ ] **Post note** on a test Candidate. Confirm in Bullhorn the Note exists,
      under the org's configured note action, HTML renders literally (angle
      brackets/ampersands escaped, newlines as line breaks — the note body is
      HTML-escaped before send).
- [ ] **Move / advance** a test JobSubmission. Confirm the JobSubmission status
      flipped to the org's advanced/interview status (never the placed/confirmed
      status), and the local `bullhorn_status` + local-write stamp updated.
- [ ] **Reject** a test JobSubmission. Confirm the status flipped to the org's
      rejected-category status, and a `bullhorn_rejected` event was recorded
      locally.
- [ ] **PUT/POST inversion** behaves: note create uses PUT, JobSubmission update
      uses POST (Bullhorn's inverted verbs). A verb mismatch surfaces as an API
      error, not a silent no-op.
- [ ] Unmapped intent (no stage-map row for the target) raises the terminal
      needs-mapping failure rather than guessing a status.

STOP after each class; log the result before requesting the next go.

---

## Phase 3 — Shadow mode (real syncs, writes still limited)

Run the org on the normal sync cadence in staging for a bake period. Writes stay
limited to test records (or off) unless Sam widens the go.

- [ ] Scheduled full + incremental (event-poll) syncs run without errors across
      several cycles.
- [ ] Event poll checkpoints cleanly: no "batch has no requestId" warnings in the
      logs (if they appear, note the instance returns batches without a
      requestId — the batch still processes via gap sweep, but flag it).
- [ ] Daily reconcile: counts of roles/candidates/applications stay consistent
      with Bullhorn; no drift, no duplicate applications on resync.
- [ ] Rate limiting respected: no sustained 429s; sync backs off rather than
      hammering (watch against the instance's monthly call limit).
- [ ] Token rotation holds: refresh token rotates and persists; no auth-strand
      loop.

---

## Sign-off checklist (before flag-off merge → prod)

- [ ] Phase 1 approved subscription writes clean; fixtures captured + committed.
- [ ] Phase 2 all three write classes verified in the Bullhorn UI, each with an
      explicit go.
- [ ] Phase 3 bake period clean (syncs, reconcile, rate limits, token rotation).
- [ ] Alembic single-head confirmed (`python -m alembic heads` from `backend/`).
- [ ] Redirect URI live (or single-URI omit path confirmed).
- [ ] Known follow-up acknowledged: the post-handover advance/reject warning
      predicates are Workable-keyed and do **not** yet fire for Bullhorn orgs
      (tracked separately).

Rollback (any time): `BULLHORN_ENABLED=off` per env; flag default is `False`.
