# Bullhorn Integration — Build & Test Plan

> _Status: PLAN (2026-07-02). Decision context: prospective client runs Bullhorn; assessment concluded integrate (~4–8 wks) rather than accelerate the full-ATS build (7–9 mo MVP, and Bullhorn staffing agencies won't replace their system of record anyway). Full research: memory `bullhorn_integration_assessment`. Hard constraint from Sam: **nothing reaches production behavior until thoroughly tested** — prod auto-deploys from `main`, so isolation is enforced by branch + flag strategy below._

## 0. Isolation strategy (how "out of prod" is enforced)

`main` auto-deploys to prod (web + 2 workers + Vercel; migrations run on boot). So:

1. **Seam refactor PRs** (Phase 1) DO merge to `main` — they are zero-behavior-change refactors of existing Workable code paths, proven by the existing test suite + arch gate. Merging early avoids months of drift on shared files (`workable_op_runner.py`, sync runner, call sites).
2. **All Bullhorn feature code** lives on a long-lived **`bullhorn` integration branch** (same pattern as the `ats` branch), deployed only to the staging environment for gate testing. Prod never sees it.
3. **Final merge to `main`** happens only after Phase 4 sign-off, and even then behind **`BULLHORN_ENABLED = False`** (default) mirroring the `MVP_DISABLE_WORKABLE` gating pattern: all `/bullhorn/*` routes 503, sync tasks no-op, op-runner handlers unreachable. Migrations in that merge are **additive-only** (new nullable columns + 2 new tables) — safe to apply on prod boot with the feature off.
4. Per-org enablement is the last step, for the client's org only, with monitoring.

Alembic discipline: check heads before/after every merge between `bullhorn` and `main` (multi-head = prod boot failure; GitHub "CLEAN" misses it).

## 1. Scope

**In scope (what the client actually needs — Taali as scoring/assessment layer on their Bullhorn):**
- Import: JobOrder → Role, Candidate → Candidate, JobSubmission → CandidateApplication (+ notes/history context)
- CV retrieval: fileAttachments + `/resume/convertToText` → existing cv_parse pipeline
- Scoring/decisions run unchanged on imported data (ATS-agnostic already)
- Write-back: status move, rejection (org's `rejectedJobResponseStatus`), notes (HTML)
- Event-subscription polling + incremental sweep + nightly reconciliation

**Out of scope (v1):** assessments-marketplace-style callback (Workable-provider analog), invite-stage automation, Appointment writes, mass update, any Placement-tier status writes (blocked by Bullhorn REST anyway).

## 2. Phase 0 — Client preconditions (week 0, parallel with Phase 1)

Blocking checks — any failure kills or reshapes the build:
1. **Edition**: NOT ATS Growth/Team (no API access at all).
2. **Product**: classic Bullhorn ATS, NOT Salesforce-based Recruitment Cloud (different API entirely).
3. **Credential ticket**: client files a Bullhorn Resource Center support ticket requesting OAuth client_id/client_secret + a dedicated API user for a 3rd-party integration (use the Kombo template wording), naming our redirect URI. Turnaround: plan for days.
4. **Entitlements**: API user provisioned with Candidate / JobOrder / JobSubmission / Note read+write (verify via `GET /entitlements/{entity}` at connect).
5. **Rate budget**: their instance defaults to 100k calls/month shared across their integrations — confirm headroom; ask for a raise if their book is large.

The build does NOT wait on this — Phases 1–3 run against a local fake; credentials are needed only for Phase 4.

## 3. Phase 1 — ATS adapter seam (≈1 wk, merges to `main`, zero behavior change)

Today there is no seam: 8 call sites construct `WorkableService` directly; 30+ `workable_*` columns on core models. Introduce the minimal seam Bullhorn needs — no speculative generality (simplest model that works):

1. **`ATSProvider` protocol** (`components/integrations/base.py`) with only the methods the shared machinery calls: `list_jobs`, `list_candidates_for_job`, `get_candidate`, `get_cv_file`, `move_application(status)`, `reject_application(reason)`, `post_note(html)`, `healthcheck`. `WorkableProvider` wraps the existing `WorkableService` verbatim.
2. **Refactor the 8 call sites** to resolve a provider from the org (`org.ats_provider()` → workable | bullhorn | none) instead of importing `WorkableService`. Workable-only surfaces (OAuth routes, provider outbox) stay Workable-named.
3. **`op_runner` dispatch generalization**: op handlers call `provider.move_application(...)` etc.; per-org mutex, retry/backoff, gated-vs-ungated semantics, and idempotency re-query stay exactly as-is (they are the proven, hard-won parts).
4. **Do NOT migrate `workable_*` columns** to a generic shape — renaming live prod columns is churn with no client value. Bullhorn gets its own small column set (Phase 2); a shared `ats_stage_map` table serves both going forward.

Ship as 2–3 small PRs, each proven by: full pytest (suites run in isolation — batch runs are flaky via shared SQLite), arch gate, vitest. These merge to `main` and deploy — acceptable because behavior is provably unchanged.

## 4. Phase 2 — Bullhorn connector (≈2–3 wks, on `bullhorn` branch)

### 4.1 Client + auth (`components/integrations/bullhorn/service.py`)
- Region discovery via `GET rest.bullhornstaffing.com/rest-services/loginInfo?username=…` (never hardcode cluster/swimlane or corpToken; always use returned `restUrl`).
- OAuth authorization_code (documented automated `action=Login` variant — no browser). **Access token = 10 min; refresh tokens are single-use and rotate on every exchange** → persist the new refresh token in the same DB transaction as its first use (losing one strands the org; this is the #1 auth footgun). Encrypted storage, same pattern as Workable tokens.
- REST session: `POST /rest-services/login` → `{BhRestToken, restUrl}`; reuse session until 401 → refresh → re-login (login rates are separately throttled — never login per request). `GET /ping` for health.
- Rate limiter + real exponential backoff on 429 (≥9,000 429s in 5 min can disable the API user — the limiter must be conservative, not just polite).
- **PUT=create / POST=update is inverted vs REST convention** — encode in the client once, cover with contract tests, never let callers pick verbs.

### 4.2 Data model (additive migration, 1 alembic revision)
- `Organization`: `bullhorn_client_id/secret` (encrypted), `bullhorn_refresh_token` (encrypted), `bullhorn_username`, `bullhorn_rest_url`, `bullhorn_connected`, `bullhorn_last_sync_*`, `bullhorn_event_subscription_id`, `bullhorn_event_request_id` (checkpoint).
- `Candidate`: `bullhorn_candidate_id` (indexed), `bullhorn_data` JSON.
- `CandidateApplication`: `bullhorn_job_submission_id` (indexed), `bullhorn_status`, `bullhorn_status_local_write_at` (local-write-wins guard, mirrors `workable_stage_local_write_at`).
- `Role`: `bullhorn_job_order_id` (indexed, unique per org), `bullhorn_job_data` JSON.
- **New table `ats_stage_map`** (`org_id`, `ats`, `remote_status`, `taali_stage`, `is_reject`): Bullhorn statuses are per-org free-text strings — seed at connect time from `GET /settings/jobResponseStatusList` + the three categorization settings (`interviewScheduledJobResponseStatus`, `confirmedJobResponseStatus`, `rejectedJobResponseStatus`); recruiter can adjust in settings. Never hardcode status strings.

### 4.3 Sync engine (`components/integrations/bullhorn/sync_service.py`)
- Reuse the sync-runner/mutex/progress scaffolding; Bullhorn specifics:
  - Reads via `/search/{entity}` (Lucene) with mandatory `fields` param batching; `/query/JobSubmissionHistory` for stage timelines.
  - **Event subscription polling** (`PUT /event/subscription` for Candidate, JobOrder, JobSubmission INSERT/UPDATE/DELETE): each GET is a **destructive queue read** — checkpoint `requestId` BEFORE processing, re-fetch last batch by `requestId` on crash (at-least-once). Treat events as dirty-flags only (`updatedProperties` has field names, not values) → re-fetch entity state. Ordering undocumented — don't rely on it.
  - **Poll cadence is a rate-budget decision**: 60s = 43k calls/mo (half the default quota on events alone). Default **180s**, configurable per org; recruiting latency tolerance makes this fine.
  - Fallback incremental sweep on `dateLastModified` (deletes do NOT surface there — events are the only delete signal) + nightly count-based reconciliation (mirrors Bullhorn's own Data Replication approach).
  - Unconsumed events purge after 7 days even while a subscription remains live.
    A poll older than the conservative 6-day recovery window triggers a sweep +
    reconciliation. A separately vanished subscription is recreated on its
    deterministic owned id; no undocumented subscription TTL is assumed.
- CV: prefer original "Resume"-typed fileAttachment (free-text type — filter loosely) → `/file/.../raw`; `/resume/convertToText` as text fallback → existing cv_parse.
- Celery tasks eager-imported in `app/tasks/__init__.py` (worker drops them otherwise).

### 4.4 Write-back
- Op-runner handlers via the Phase-1 provider seam: `move_application` = `POST /entity/JobSubmission/{id} {"status": <org-mapped value>}`; reject = org's `rejectedJobResponseStatus`; `post_note` = `PUT /entity/Note` with HTML `comments`, `action` from org's `commentActionList`, `personReference` + `jobOrder` set.
- Expect per-org workflow-validation failures on status writes (Bullhorn runs server-side validation; placement-tier statuses are hard-blocked) → these surface as terminal op failures back to the Decision Hub, same as Workable terminal failures.
- Local-write-wins guard on `bullhorn_status_local_write_at` so the next event poll doesn't clobber our own write.

### 4.5 Config + routes + frontend
- `BULLHORN_ENABLED` setting (default `False`) gating everything, mirroring `MVP_DISABLE_WORKABLE` semantics (403/503 + task no-ops).
- `domains/bullhorn_sync/routes.py`: connect (credentials + username → loginInfo → token exchange → entitlement pre-flight → status-list seed), sync start/status/cancel, admin diagnostic.
- FE: `BullhornConnection.jsx` (settings connect card + status), stage-map editor (small), reuse BackgroundJobsPanel for sync progress. ~300–500 lines, mirrors Workable UI.

## 5. Phase 3 — Testing without credentials (≈1–2 wks, overlaps Phase 2)

**Fake Bullhorn server** (pytest fixture app) implementing the contract from official docs: loginInfo, OAuth with **rotating single-use refresh tokens**, REST login/ping with expiring sessions, search/query with mandatory `fields`, the destructive event queue with `requestId` re-fetch, file + convertToText endpoints, PUT/POST inversion, entitlements, 429 responses.

Contract tests that must pass (the failure modes the research flagged):
1. Refresh-token rotation survives a worker crash mid-exchange (token persisted before use; stale token → recovers via re-auth path, never silent strand).
2. Session 401 mid-sync → refresh → resume without duplicate upserts.
3. Event checkpoint: crash after GET, before processing → replay by requestId, no lost events; idempotent re-processing.
4. Seven-day unread-event purge on a live subscription → stale-poll sweep +
   reconciliation; separately forced subscription disappearance → recreate + gap sweep.
5. Per-org status mapping: two fake orgs with different status lists both round-trip move/reject correctly; unmapped status → surfaced, not guessed.
6. Local-write-wins: our status write followed by a stale inbound event does not revert the stage.
7. 429 storm → backoff keeps request rate under the user-disable threshold.
8. Verb inversion: any accidental PUT-update/POST-create fails the contract test.
9. Existing Workable suite green after seam refactor (Phase 1 gate, re-run on `bullhorn` branch after each rebase).

Run suites in isolation (batch = flaky). FE: vitest + arch gate. Local migration test on a throwaway Postgres container (not localhost:5432).

**Staging deploy** from `bullhorn` branch (the `ats`-branch staging pattern; stand the staging service up first if it isn't live yet): flag ON in staging only, seeded demo org, fake-Bullhorn pointed via config override for E2E (connect → sync → score → decide → write-back → event round-trip).

## 6. Phase 4 — Live validation against the client's instance (≈1 wk after credentials arrive)

Their instance is **production data with no sandbox** — treat every write as irreversible:
1. **Read-only phase first**: connect staging to their instance; full sync into a quarantined staging org (real PII — encrypted tokens, no dumps, staging access limited); verify entity mapping, status-list seed, CV retrieval, rate consumption vs their monthly budget.
2. **Write phase, explicitly gated**: client creates a dedicated test Candidate + JobOrder + JobSubmission; each write class (move, reject, note) validated against those records only, each behind an explicit go from Sam (never gate an irreversible remote write on tool-rejection alone).
3. **Shadow run** ~3–5 days: event polling + sync live, decisions computed but write-back queued-and-held; diff held ops against what the client's recruiters actually did.
4. Sign-off checklist: sync fidelity, write round-trip, rate budget < 50% of quota, token rotation observed across a real 10-min expiry cycle, reconciliation clean.

## 7. Phase 5 — Prod merge + enablement (days)

1. Rebase `bullhorn` → PR to `main`: feature flag default OFF, additive-only migrations, alembic single-head verified. Prod deploys with zero behavior change.
2. Flip `BULLHORN_ENABLED` + per-org enable for the client org only; run the Phase-4 checklist once against prod; monitor op-runner failure rates + rate consumption for the first week.
3. Metering: any new Anthropic-calling path (none expected — scoring is unchanged) must go through `MeteredAnthropicClient`; CI gate enforces.

## 8. Timeline & risks

| Phase | Duration | Gate |
|---|---|---|
| 0 Client preconditions | wk 0 (parallel) | edition/product/entitlements confirmed; ticket filed |
| 1 Adapter seam → `main` | ~1 wk | zero-behavior-change proven; prod unaffected |
| 2 Connector on `bullhorn` branch | ~2–3 wks | code complete behind flag |
| 3 Fake-server contract tests + staging E2E | ~1–2 wks (overlaps 2) | all 9 contract classes green; staging E2E green |
| 4 Live validation (needs creds) | ~1 wk | read-only → gated writes → shadow diff clean |
| 5 Prod merge + per-org enable | days | flag-off merge; client org flipped |

**Total: ~5–7 wks** elapsed (within the assessed 4–8; Criteria Corp's 4–8 wk per-customer figure is the external calibration).

Top risks: client fails a Phase-0 precondition (kills/reshapes the deal — check FIRST); refresh-token strand in prod workers (mitigated by transactional persistence + contract test 1); event-gap silent data staleness (mitigated by reconciliation + dead-subscription sweep); their monthly rate quota too small for their book size (measured in Phase 4 read-only); `bullhorn`↔`main` drift on shared files (mitigated by landing the seam in `main` first and rebasing weekly).

**Fallback**: if the credential ticket stalls or preconditions fail, the Kombo Bullhorn Assessment connector is the buy-side hedge (days to live, monthly fee, must be ripped out if we ever join the Marketplace — no-middleware rule).
