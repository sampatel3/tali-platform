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

Set creds in the staging environment (not shell history):

```bash
export BULLHORN_USERNAME=...        # API user
export BULLHORN_CLIENT_ID=...
export BULLHORN_CLIENT_SECRET=...
export BULLHORN_PASSWORD=...         # API user password
```

---

## Phase 1 — Read-only validation (no go needed beyond "connect this org")

Connect the client's org in staging with the flag on for that org only, then run
one manual sync and eyeball the results. Nothing here writes to Bullhorn.

1. **Connect** (discovery → OAuth → entitlement pre-flight → status-list fetch →
   stage-map seed). Via the staging admin/API:

   ```bash
   curl -sS -X POST "$STAGING/api/v1/bullhorn/connect" \
     -H "Authorization: Bearer $STAGING_ADMIN_JWT" \
     -H "Content-Type: application/json" \
     -d '{"username":"'"$BULLHORN_USERNAME"'","client_id":"'"$BULLHORN_CLIENT_ID"'","client_secret":"'"$BULLHORN_CLIENT_SECRET"'","password":"'"$BULLHORN_PASSWORD"'"}'
   ```

   - [ ] Response reports `bullhorn_connected: true` and a non-zero
         `seeded_rows` (stage-map seeded from the org's status categorization).
   - [ ] If it fails on entitlements, the message names the missing entity/verb —
         send that back to the client's admin (onboarding §3).

2. **Capture fixtures** (read-only, plus one throwaway event subscription it
   creates and deletes). Keeps the fake server honest against this real instance:

   ```bash
   python backend/scripts/bullhorn_capture_fixtures.py --allow-live \
     --out backend/tests/fixtures/bullhorn_recorded --max 5
   ```

   - [ ] Fixtures written for ping, status_list, entitlements, job_orders,
         job_submissions, notes, file_attachments, job_submission_history,
         event_subscription_create, event_poll.
   - [ ] Spot-check one file: names/emails/phones are faked, no `BhRestToken` /
         secret survives (grep the dir for `REDACTED` and for any real client name).
   - [ ] Throwaway subscription (`TaaliFixtureCapture-DELETE-ME`) was deleted;
         if the script warned it could not delete, delete it manually.

3. **One manual sync** into the isolated staging org:

   ```bash
   curl -sS -X POST "$STAGING/api/v1/bullhorn/sync" \
     -H "Authorization: Bearer $STAGING_ADMIN_JWT" \
     -H "Content-Type: application/json" \
     -d '{"mode":"full"}'
   # poll run progress and overall status:
   curl -sS "$STAGING/api/v1/bullhorn/sync/status" -H "Authorization: Bearer $STAGING_ADMIN_JWT"
   curl -sS "$STAGING/api/v1/bullhorn/status"      -H "Authorization: Bearer $STAGING_ADMIN_JWT"
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

- [ ] Phase 1 read-only clean; fixtures captured + committed.
- [ ] Phase 2 all three write classes verified in the Bullhorn UI, each with an
      explicit go.
- [ ] Phase 3 bake period clean (syncs, reconcile, rate limits, token rotation).
- [ ] Alembic single-head confirmed (`python -m alembic heads` from `backend/`).
- [ ] Redirect URI live (or single-URI omit path confirmed).
- [ ] Known follow-up acknowledged: the post-handover advance/reject warning
      predicates are Workable-keyed and do **not** yet fire for Bullhorn orgs
      (tracked separately).

Rollback (any time): `BULLHORN_ENABLED=off` per env; flag default is `False`.
