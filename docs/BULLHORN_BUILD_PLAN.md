# Bullhorn Integration — Detailed Build Plan (working spec)

> _Status: BUILDING (2026-07-09). Companion to `BULLHORN_INTEGRATION_PLAN.md` (phased strategy, merged #849). This doc is the file-level spec the build executes against. Client pre-checks CONFIRMED 2026-07-09: not ATS Growth, classic Bullhorn ATS, client will provide credentials._

## 0. Test-access strategy (how WE test without owning a Bullhorn instance)

Four tiers, cheapest first — 1 and 2 are the build's backbone; 3 is the live gate; 4 is a later strategic buy:

1. **Fake Bullhorn server (primary, ours, free).** A pytest-hosted FastAPI app implementing the documented contract: loginInfo cluster discovery, OAuth with single-use rotating refresh tokens, REST login/BhRestToken sessions with TTL + `/ping`, `/search//{entity}` + `/query/JobSubmissionHistory` with mandatory `fields`, destructive `/event/subscription` queue with `requestId` re-fetch, `/settings/jobResponseStatusList` + categorization settings, file attachments + `/resume/convertToText`, `/entitlements/{entity}`, PUT-create/POST-update inversion, 429 + user-disable simulation. Every contract test and staging E2E runs against this. It is the ONLY place destructive/edge paths (token strand, event-gap, 429 storm) can be tested at all — no real instance allows that safely.
2. **Recorded-fixture validation.** When client credentials arrive, a read-only capture script records real responses (sanitized) for the entities we touch; fixtures feed the fake server so its shapes stay honest against at least one real instance.
3. **Client instance, gated (live validation).** Their creds are issued FOR our integration, so we can drive them ourselves — but it is production data: read-only phase first; writes only against a client-created test Candidate + JobOrder + JobSubmission; each write class needs an explicit go from Sam. Ask the client for: API creds (support ticket), the API user's entitlement list, and 2–3 test records they create and own.
4. **Bullhorn partner sandbox (defer).** A real sandbox comes with the Marketplace program ($5K/yr + $1–4K validation) — the right move at Bullhorn customer #2, not for one client. (A standalone non-partner sandbox is forum-reported ~$12K/yr — worse value than the program.)

## 1. Branch/PR topology (prod isolation)

- `feat/ats-provider-seam` → PR to `main`: **PR-1** (seam refactor, zero behavior change). Merges to main/prod — safe because behavior is proven unchanged by the full existing suite.
- `bullhorn` (long-lived, created off main after PR-1 merges): **PR-2 … PR-6** land here via short-lived branches or direct commits. Deploys to staging only (staging service setup is a separate gated step — never auto-created). Rebase onto main weekly.
- Final flag-off merge `bullhorn` → `main` only after live validation sign-off (Phase 4 of the integration plan).
- Alembic: single-head check before/after every merge in both directions.

## 2. PR-1 — ATS provider seam (zero behavior change, → main)

**New `backend/app/components/integrations/base.py`:**
```python
class ATSProvider(Protocol):          # typing.Protocol, runtime-checkable not required
    ats: str                          # "workable" | "bullhorn"
    def list_jobs(...) -> list[dict]
    def list_candidates_for_job(...) -> Iterator[dict]
    def get_candidate(candidate_ref) -> dict
    def get_cv_file(candidate_ref) -> tuple[bytes, str] | None
    def move_application(application, target_status) -> None
    def reject_application(application, reason) -> None
    def post_note(application, html) -> None
    def healthcheck() -> dict
```
Only the methods the SHARED machinery (op_runner, sync trigger sites, context enrichment) actually calls today — verify the exact set against the live call-site inventory before coding; drop/rename to match reality. No speculative methods.

**`WorkableProvider`** (`components/integrations/workable/provider.py`): thin delegation onto the existing `WorkableService`/`workable_actions_service` functions — no logic moves, no behavior change. Workable-only surfaces (OAuth routes, assessments-provider outbox, sync internals) are NOT behind the protocol.

**Resolution:** `Organization.ats_provider()` (or module-level `resolve_ats_provider(org)`) returns `WorkableProvider | None` based on existing connection state — later grows a bullhorn arm. The ~8 call sites outside `components/integrations/workable/` switch from constructing `WorkableService` to resolving the provider. Call sites whose usage is intrinsically Workable-specific (OAuth connect flow in `identity_access/organization_routes.py`, workable_sync routes) stay direct — do not force them through the seam.

**Gate:** full backend pytest suites in isolation + vitest + arch gate all green with zero test edits (except pure import-path updates); diff reviewed for behavior parity.

## 3. PR-2 — Models, config, stage map (→ `bullhorn` branch)

One additive alembic revision:
- `organization`: `bullhorn_username`, `bullhorn_client_id`, `bullhorn_client_secret` (encrypted, same secrets pattern as workable tokens), `bullhorn_refresh_token` (encrypted), `bullhorn_rest_url`, `bullhorn_connected` (bool, default false), `bullhorn_last_sync_at/status/summary`, `bullhorn_sync_progress` JSON, `bullhorn_event_subscription_id`, `bullhorn_event_request_id` (checkpoint), `bullhorn_config` JSON (mirrors `WorkableConfigBase` role: poll cadence, actor defaults).
- `candidate`: `bullhorn_candidate_id` (String, indexed), `bullhorn_data` JSON.
- `candidate_application`: `bullhorn_job_submission_id` (String, indexed), `bullhorn_status` (String), `bullhorn_status_local_write_at` (DateTime).
- `role`: `bullhorn_job_order_id` (String, indexed, unique `(org_id, bullhorn_job_order_id)`), `bullhorn_job_data` JSON.
- **New table `ats_stage_map`**: `id`, `org_id` FK, `ats` (String), `remote_status` (String), `taali_stage` (String, one of PIPELINE_STAGES), `is_reject` (bool), unique `(org_id, ats, remote_status)`.
- Settings: `BULLHORN_ENABLED: bool = False` in `platform/config.py` + `BULLHORN_EVENT_POLL_SECONDS: int = 180`.
- Schemas: `BullhornConfigBase` in `schemas/organization.py`.
- Migration tested on throwaway Postgres container (NOT localhost:5432).

## 4. PR-3 — Bullhorn API client + auth (→ `bullhorn`)

`backend/app/components/integrations/bullhorn/service.py` (+ `auth.py` if cleaner):
- **Discovery:** `GET https://rest.bullhornstaffing.com/rest-services/loginInfo?username=…` → oauthUrl/restUrl; cache on org (`bullhorn_rest_url`), refresh on auth failure. Never hardcode swimlanes or corpToken.
- **OAuth:** authorization_code with documented automated variant (`action=Login&username&password` server-side) for connect; `POST /oauth/token` exchange; **refresh tokens are single-use — persist the NEW refresh token to the org row in the same transaction/flush before first use of the new access token**. Access token TTL 10 min.
- **REST session:** `POST {restUrl}/login?version=*&access_token=…` → `{BhRestToken, restUrl}`; hold session in-memory per client instance; on 401 → refresh flow → re-login ONCE, then fail op (op_runner retry takes over). Never login per-request. `ping()` for healthcheck.
- **Verb discipline:** internal `_create(entity, data)` = PUT, `_update(entity, id, data)` = POST. Callers never choose verbs.
- **Rate limiting:** token bucket well under 1,500 req/min shared budget (default ~5 req/s), exponential backoff on 429 (both flavors), hard circuit-breaker if 429 count in a rolling 5-min window exceeds ~500 (an order of magnitude under Bullhorn's 9,000 user-disable threshold).
- Typed methods needed by sync/write-back: `search_job_orders`, `search_candidates`, `query_job_submissions`, `get_job_submission_history`, `get_status_list()` (`/settings/jobResponseStatusList` + the 3 categorization settings), `update_job_submission_status`, `create_note`, `list_file_attachments`, `get_file_raw`, `convert_resume_to_text`, `get_entitlements(entity)`, event-subscription methods (`create_subscription`, `poll_events(max_events)`, `refetch_events(request_id)`, `delete_subscription`).
- `fields=` param mandatory on every read; page reads defensively (treat 500 rows as the /search cap).

## 5. PR-4 — Fake Bullhorn server + contract tests (→ `bullhorn`)

`backend/tests/fakes/bullhorn_app.py` (FastAPI app, in-memory state, deterministic) + `backend/tests/integrations/bullhorn/test_contract_*.py`.
Fake features: seedable orgs (status lists differ per org), token issuance with rotation + strand detection (reusing an old refresh token → 401 like real), session TTL fast-forward, destructive event queue + requestId re-fetch + 7-day unread-event purge, an explicit subscription-disappearance control (lifetime unspecified), 429 injection, entitlement config, verb inversion enforcement (PUT on existing id → error, POST on missing id → error), file + convertToText endpoints.
**The 9 contract-test classes (all must pass):**
1. refresh-rotation crash-safety (kill between exchange and use → recovery, no strand)
2. 401 mid-sync → refresh → resume, no duplicate upserts
3. event checkpoint crash → requestId replay, no loss, idempotent re-process
4. dead subscription → detect, recreate, gap-sweep triggered
5. two orgs with different status lists round-trip move/reject correctly; unmapped status surfaces as needs-mapping (never guessed)
6. local-write-wins: our write + stale inbound event ≠ revert
7. 429 storm → backoff keeps rate under disable threshold (assert on fake's counters)
8. verb inversion attempts fail loudly
9. Workable suite still green (seam parity re-check on this branch)

## 6. PR-5 — Sync engine + write-back + tasks (→ `bullhorn`)

`components/integrations/bullhorn/sync_service.py` + `sync_runner` reuse:
- Full sync: JobOrder→Role upsert (mapped structural fields + `bullhorn_job_data` blob), open JobSubmissions per JobOrder → Candidate + CandidateApplication upserts (`bullhorn_status` + mapped `pipeline_stage` via `ats_stage_map`), JobSubmissionHistory → application events, notes → context.
- CV: "Resume"-typed fileAttachment (loose match) → raw bytes → existing `process_document_upload`/cv_parse path; fallback `convert_resume_to_text`.
- Incremental: event-subscription poll task (Celery beat, `BULLHORN_EVENT_POLL_SECONDS`, per-org, only when `bullhorn_connected` AND `BULLHORN_ENABLED`): checkpoint `bullhorn_event_request_id` BEFORE processing; events = dirty flags → re-fetch entity; deletes handled from events only. `dateLastModified` sweep as fallback; nightly count reconciliation.
- Per-org mutex shared with write-back (same pattern as Workable — reuse the mutex util, distinct key namespace `bullhorn:{org_id}`).
- Write-back: `BullhornProvider(ATSProvider)` implementing move/reject/note via client; op_runner resolves provider through the PR-1 seam — **no new op types, no changes to gated/ungated semantics or retry policy**. Set `bullhorn_status_local_write_at` on successful write.
- Rescore triggering: reuse the context-digest pattern; **respect the no-auto-paid-rescore policy — sync updates context but never auto-triggers paid re-evaluation**.
- Celery: new tasks eager-imported in `app/tasks/__init__.py`.

## 7. PR-6 — Routes + frontend (→ `bullhorn`)

- `domains/bullhorn_sync/routes.py` under `/api/v1/bullhorn/*`, ALL gated 503 when `BULLHORN_ENABLED` is false (mirror `MVP_DISABLE_WORKABLE` gating): `POST /connect` (username+client_id+secret+password one-time → discovery → token exchange → entitlement pre-flight via `/entitlements` → status-list fetch → seed `ats_stage_map` with categorization-setting defaults → store encrypted creds), `GET /status`, `POST /sync`, `GET /sync/status`, `POST /sync/cancel`, `GET /stage-map`, `PUT /stage-map`, `GET /admin/diagnostic`.
- FE (mirror Workable patterns, shared purple design tokens): `features/integrations/BullhornConnection.jsx` (connect card in RecruiterSettingsPage, status display), stage-map editor (small table: remote status → Taali stage + reject checkbox), BackgroundJobsPanel wiring for sync progress. Reuse existing polling context.
- FE gated behind the org `bullhorn_connected`/platform flag exposure in bootstrap payload.

## 8. Verification gates (every PR)

- Backend: `rtk pytest` per-suite in isolation (batch runs flaky — shared SQLite), via main repo venv from the worktree backend dir.
- FE: `rtk vitest run` + arch gate script; FE deps via symlink to main checkout's node_modules.
- Migration: throwaway Postgres container; alembic single-head assert.
- Metering: no new Anthropic call paths expected; if any appear they MUST use the metered client (CI gate enforces).
- File-size gate: respect `scripts/check_file_sizes.py` — keep new modules under the cap (split sync_service if needed).
- Review: adversarial multi-agent review per phase (correctness, parity, security/creds handling) before commit.

## 9. Execution order

| Step | What | Where | Gate |
|---|---|---|---|
| 1 | PR-1 seam | `feat/ats-provider-seam` → main | full suite green, parity review |
| 2 | Create `bullhorn` branch; land this doc | `bullhorn` | — |
| 3 | PR-2 models+config, PR-3 client+auth, PR-4 fake server (parallel-ish) | `bullhorn` | migration test; client unit tests vs fake |
| 4 | PR-5 sync+write-back | `bullhorn` | contract tests 1–9 green |
| 5 | PR-6 routes+FE | `bullhorn` | vitest+arch gate; E2E vs fake |
| 6 | Staging deploy + E2E; client creds arrive → capture fixtures → live validation runbook | staging | Phase-4 sign-off |
| 7 | Flag-off merge → main; per-org enable | main/prod | checklist in integration plan §7 |
