# Taali platform audit and remediation report

- **Audit date:** 2026-07-15
- **Repository:** `sampatel3/tali-platform`
- **Audited revision:** `0e562f2f44b0608de17490fc2c580c68645a3287` plus the current local remediation worktree
- **Release status:** **not released and not deployed**

## Executive verdict

The review found material correctness, durability, security, performance,
cost, website, documentation, and test-quality issues. The current worktree
remediates the safely actionable findings documented below without weakening
product behavior. Known policy, evidence, deployment, compatibility, and
architecture residuals are called out explicitly; this is not a claim that
every future optimization or production condition is solved. The changes
preserve or improve the result delivered to the user: they remove duplicate
work, lost work, excess round trips, unnecessary provider calls, unbounded
reads, and misleading feature states rather than deleting useful capability.

This is not yet a release claim. The remediation is packaged on the local
`codex/platform-audit-remediation` branch in seven dependency-ordered commits,
but it has not been pushed, reviewed, or deployed. The complete default non-production suites,
separate PostgreSQL contracts, and measured coverage are green locally;
supported-runtime CI, a production-shaped migration rehearsal, and a
reviewable PR state remain release-candidate gates. External configuration,
controlled deployment, and post-deploy smoke are later production-release
validation. Product/legal
decisions and evidence-volume questions are also kept separate from code
defects rather than being falsely labelled fixed.

The highest-risk local defects now addressed are:

1. User-triggered asynchronous work could be acknowledged before durable intent
   existed, then be lost on broker failure or duplicated after worker failure.
2. The role-wide Process flow ran from web-process lifetime and could show stale
   or misleading progress.
3. Manual pre-screen fan-out could replay paid work around ambiguous crash
   windows.
4. A malformed holistic model response could silently become a valid zero
   score and reject a real candidate.
5. Railway runtime database selection could prefer a public connection string,
   and deployment readiness expected sensitive dependency detail from a
   deliberately redacted public endpoint.
6. Rate limiting did not reliably distinguish clients behind Railway without a
   narrowly trusted proxy signal.
7. Workable OAuth state, pagination origins, integration-secret storage, API-key
   ownership, and public error payloads had security gaps.
8. Several incomplete features could appear configurable even though their
   security or implementation substrate did not exist.
9. Collection endpoints, analytics, chat history, polling, and frontend bundles
   had avoidable database, network, CPU, and browser costs.
10. Historical plans, deployment instructions, environment names, and showcase
    routing had drifted from the executable product.

## Status language used in this report

| Status | Meaning |
|---|---|
| **Fixed locally** | Present in the current worktree. It is not a production claim. |
| **Targeted verification passed** | The named focused checks passed. It does not replace the final full suite. |
| **Intentional** | Retained because removing it would reduce utility, safety, compatibility, or auditability. |
| **Release-candidate gate** | Must be completed before this worktree can become an approved deployment artifact. |
| **Post-deploy validation** | Runs only after an approved release candidate, external configuration, and controlled deployment; it is not an RC prerequisite. |
| **External / policy** | Requires deployment access, provider configuration, production data, legal/product ownership, or a deliberate compatibility decision. |

## Scope, method, and limitations

The review covered backend application and worker paths, migrations, frontend
routes and feature modules, CI and deployment scripts, environment contracts,
tests, architecture ratchets, product/deployment/audit documentation, and the
open GitHub pull-request queue. The frozen pre-packaging snapshot spanned 652
tracked paths (630 modified and 22 deleted) plus 121 untracked paths. The
tracked diff contained 18,808 additions and 11,975 deletions with no binary
changes; nothing was staged at capture. Those paths are now preserved in seven
local commits. That breadth is one reason the supported-runtime and
reviewable-PR release gates remain mandatory.

The audited base was detached at `0e562f2f` (`Redesign agent prompts and unblock
task setup (#1026)`, 2026-07-15). The remediation now lives on local branch
`codex/platform-audit-remediation`; there is no pull request and no push has
been made.

Local verification used Python 3.12.3 and Node 24.7, while CI now pins the
supported runtime contract to Python 3.11.9 and Node 22.23.1. The backend
deployment image also pins Python 3.11.9; the frontend deployment contract
currently declares Node `>=22.12.0`, not an exact patch. Local Compose,
deployment documentation, and migration verification agree on PostgreSQL 16.
Supported-runtime CI is therefore a release gate even when a local check is
green.

Website review used source inspection and the historical 156-finding UX audit
(50 P1, 76 P2, 30 P3). That audit records all five remediation PRs as merged,
all 16 dangerous-interaction P1s as closed, a zero machine UI-guard baseline,
and explicitly retains shared-button consolidation, context-sensitive naming,
named-stage/global background-work UX, and lower-priority polish as future
product/design work rather than silently calling the whole inventory closed.
This review also used
route/SEO/UI tests, build and bundle policy, frontend architecture checks, and
the in-app browser. Router tests cover `/`, `/demo`, `/showcase/jobs`,
`/developers`, `/blog`, the article, legal pages, lead/thanks routes, and the
404; the production build emitted the expected assets and its SPA-fallback
contract passed. Desktop and 390×844 mobile browser checks found one H1 on each
reviewed page, no horizontal overflow, working mobile navigation, and no
browser-console errors. The deployed frontend/backend were also inspected and
are still on the older release: production advertises the retired
`api.taali.ai` endpoint, exposes the older detailed health contract, and reports
degraded readiness because worker/scoring heartbeats are missing. Nothing was
deployed by this audit. No physical-device, assistive-technology/screen-reader,
automated accessibility, or field/Core Web Vitals run was performed.

## Remediation ledger: bugs and non-functioning behavior

### Asynchronous work and paid-call durability

| Finding | User/business impact | Local remediation | Status |
|---|---|---|---|
| Role Process intent was coupled to a request/web process and publish success. | Closing the browser, a web restart, or broker failure could lose work; duplicates could overlap. | `process_role_dispatch.py` now commits a `BackgroundJobRun` before publish, stores the exact recovery payload, uses per-role advisory/row locking, retries publication, recovers through Beat, claims with worker heartbeat/lease ownership, and suppresses duplicate delivery. Cancel and status are durable. | **Fixed locally; 47 relevant targeted tests passed.** |
| A stale Process worker could be automatically replayed after an ambiguous paid-call window. | Duplicate provider spend and potentially conflicting candidate state. | A stale worker is failed conservatively and the role is unblocked; ambiguous paid work is not automatically replayed. Per-candidate checkpoints make known-safe continuation possible. | **Fixed locally.** |
| Manual pre-screen was a broad fan-out without durable per-application delivery ownership. | Broker loss, duplicate calls, invisible partial progress, or accidental repayment. | Migration 175 adds durable batch items. A run and all items are persisted before bounded Celery fan-out. Dispatch leases, token compare-and-swap, `SKIP LOCKED`, one-minute recovery, and an `attempting` marker before provider I/O distinguish safe retries from terminal `ambiguous` outcomes. | **Fixed locally; 15 operational tests and the 131-test pre-screen regression set passed.** |
| Outreach sending, pool rescoring, agent-chat turns, decision reevaluation, and manual agent runs could lose publish intent or overlap. | Accepted user work could vanish or execute twice. | Migration 174 and the new dispatch services add durable receipts, stable dispatch/provider keys, attempts, due times, leases, exact-payload recovery, ownership checks, and bounded redispatch. | **Fixed locally; `test_async_dispatch_recovery.py` covers publisher failure, duplicate delivery, stale ownership, exact-payload recovery, and paid-run recovery.** |
| Fireflies webhook and integration outbox work lacked durable inbox/lease semantics. | Provider retries could duplicate interviews or delivery could stall invisibly. | Migration 173 adds a unique Fireflies inbox, delivery attempts/due times/leases, an interview provider identity constraint, and leased outbox recovery. | **Fixed locally; deployment of migration still required.** |
| Unified Process displayed stale local state and refreshed caches immediately after merely enqueueing work. | UI could imply completed work or invalidate useful data before results existed. | Status is backed by durable job/Redis state; the UI distinguishes queued and dispatching; stale cache/auto-reject refresh after enqueue was removed; `score=all` observes active-job guards; graph refresh propagates explicit resync intent. | **Fixed locally.** |

### Scoring and pre-screen correctness

| Finding | Impact | Local remediation | Status |
|---|---|---|---|
| Holistic `overall` was optional and defaulted to zero. | A degraded but schema-valid model response could become a silent zero-score rejection across the holistic default path. | `overall` is required. Missing output enters structured retry/failure handling and persists no score; a genuine model-emitted zero remains valid. | **Fixed locally and regression-tested.** |
| Pre-screen and downstream consumers used divergent role/JD/criteria representations and sometimes non-authoritative score fields. | The same candidate could be judged against different recruiter intent, or a later full score could contaminate a pre-screen decision. | Stage 1, both scoring sub-agents, and full scoring share canonical role inputs while preserving constraints, authored intent, and teach examples. Decisions and snapshots read only the durable penalized `genuine_pre_screen_score_100`; legacy rows without provenance fail open to full scoring. | **Fixed locally.** |
| Gate cards could use a downstream role threshold instead of the Stage-1 cutoff. | A candidate who passed pre-screen could be represented as a pre-screen reject. | The enforced/stamped gate threshold owns the Stage-1 decision; the role send bar stays a separate downstream policy. | **Fixed locally.** |
| Pre-screen false rejects were structurally difficult to measure. | Cost savings could hide the most harmful scoring error. | A bounded shadow sampler full-scores actual filtered candidates, and both divergence reporting and gate calibration consume survivor plus shadow-reject pairs. Live enforcement stays explicit. | **Fixed locally; operational enablement and adequate sample volume remain external.** |
| Copy/paste overlap could hard-cap by default and tokenization skipped non-Latin text. | False-positive or language-dependent harm. | Detection is always recorded but defaults to a neutral recruiter flag; the legacy cap is opt-in through `FRAUD_COPY_PASTE_ACTION=cap`. Tokenization is Unicode letter/number aware. | **Fixed locally.** |
| Full-score engines applied different integrity handling and prompt-cache layouts. | Inconsistent score behavior and unnecessary model input cost. | Both engines apply the bounded timeline/unverified-claim layer. Both holistic calls use the same one-hour ephemeral cache layout for stable role context. | **Fixed locally and focused-request-tested.** |
| A new score request could trigger redundant standing work. | Repeated provider cost and queue pressure. | Event-driven enqueue, activation bootstrap, a bounded five-minute backlog sweep, duplicate-job guards, credit/budget gates, and a 50-item per-tick auto-score cap drain work steadily without buying extra agent reasoning cycles. | **Fixed locally.** |

### Authentication, authorization, security, and error disclosure

| Finding | Impact | Local remediation | Status |
|---|---|---|---|
| New unverified users could log in while UI/product copy implied verification. | Account-control inconsistency. | Login now requires verification. Migration 172 safely grandfathers already-active owners so rollout does not lock out existing workspaces. Verification-token replay and password-reset invalidation are tested. | **Fixed locally; migration required.** |
| A late profile-bootstrap response could restore a logged-out user or clear a newer login after token/session rotation. | Cross-session state could reappear, a valid newer login could disappear, or a half-login token could remain cached. | Authentication requests now carry a generation and token identity. Logout/new login invalidates older success and failure handlers, failed profile bootstrap rolls back private state, and same-session sliding-token rotation remains valid. | **Fixed locally; 4 race regressions passed.** |
| Password guidance treated bcrypt's limit like a character count. | A multibyte password could cross bcrypt's boundary despite appearing shorter than 72 characters. | Backend validation and frontend copy now state and enforce the 72 **UTF-8 byte** limit; the unused Passlib dependency was removed. | **Fixed locally and boundary-tested.** |
| API key administration and organization security/integration settings were not uniformly owner-only. | Workspace members could control machine credentials or access policy. | API-key create/list/revoke and organization mutation now require an organization owner. | **Fixed locally.** |
| Workable OAuth callback state was not cryptographically bound to the initiating user/workspace. | Login CSRF or cross-workspace connection risk. | Short-lived signed state includes user, organization, audience, and nonce; callback verification rejects invalid or expired state. Frontend forwards the state value. | **Fixed locally.** |
| Workable base/pagination URLs could change origin. | SSRF/credential-forwarding risk. | Callback and pagination URL validation now enforce safe schemes and the approved origin; tests reject unsafe origin changes. | **Fixed locally.** |
| Integration secrets shared generic encryption assumptions and Fireflies webhook secret could remain plaintext. | Secret exposure and difficult key rotation. | New and re-saved secrets use dedicated integration-secret encryption with current/previous-key reads; Fireflies API/webhook writes are encrypted. Readers retain an unversioned/plaintext compatibility path for existing rows. | **New writes fixed locally; inventory, re-encryption/rotation of legacy rows, and production key provisioning remain release work.** |
| Provider/sandbox/graph/task exceptions were serialized into candidate, recruiter, job, or debug responses. | Sensitive tokens, paths, provider bodies, or internals could leak. | Stable public error codes replace raw exceptions; detailed exceptions stay in server logs. Assessment result/timeline/git evidence, Workable sync, graph debug, reconciliation, and task-exhaustion paths are redacted. | **Fixed locally; targeted redaction tests passed.** |
| Admin health used a shared/general secret boundary. | Excess blast radius and ambiguous operator authentication. | A dedicated `ADMIN_SECRET` protects the exact admin-route inventory. Production startup now fails closed unless it is non-empty, at least 32 characters, non-default, and distinct from the application secret. The architecture gate parses endpoint bodies and requires a real `_require_admin(request)` call, so comments or strings cannot spoof enforcement; provider detail is absent from public readiness. | **Fixed locally; production value required before boot.** |
| In-memory rate-limit fallback could grow without bound and Redis failure could become a permanent latch. | Memory pressure or silently disabled distributed protection. | Fallback storage is bounded and fails closed for new keys, expired mixed windows are reclaimed, and Redis initialization retries after cooldown. The production-only test compatibility wrapper was removed. | **Fixed locally; 70 limiter/apply/EEO tests passed in the focused run.** |
| Forwarded IP handling was either spoofable or collapsed all Railway users into one proxy bucket. | Attackers could rotate spoofed headers, or legitimate users could throttle one another. | One canonical resolver now feeds middleware logging/rate limits plus job-page apply/EEO and marketing lead capture. Railway's validated `X-Real-IP` is explicit opt-in; generic forwarded chains are used only behind configured trusted proxy CIDRs and are walked from the trusted peer. Spoofed/untrusted `X-Forwarded-For` and invalid Railway-header regressions are pinned. | **Fixed locally and contract-tested.** |
| Broad/always-pass security and CORS assertions did not pin actual behavior. | Regressions could pass CI. | Tests now assert exact allowed/blocked contracts, including disabled Workable webhook behavior. | **Fixed locally.** |

### Assessments, payments, and user-visible state

| Finding | Impact | Local remediation | Status |
|---|---|---|---|
| Browser interval callbacks, suspension, and hidden tabs could make the assessment timer drift. | Candidates could see an inaccurate remaining time. | Remaining time is derived from a wall-clock deadline; delayed callbacks catch up and invalid timestamps fail closed. | **Fixed locally and tested.** |
| Timeout finalization and post-claim scoring had crash/race windows. | Work could be discarded, scored from an unverified starter state, or finalized twice. | Timeout finalization yields to candidate submission, preserves in-progress work, commits terminal state before waking the role, and sweeps expired work. Post-claim recovery grades only an exact verified pushed candidate branch/HEAD and refuses unsafe starter recovery. | **Fixed locally and targeted tests cover races/failures.** |
| Assessment status screen changed return paths around hooks. | React hook-order runtime error after loading transitions. | Hook order is stable across status modes and covered by a rerender regression. | **Fixed locally.** |
| Stripe top-up delivery lacked explicit replay/non-grant coverage. | Duplicate credit grants or granting on the wrong event. | Checkout completion is idempotent; replay is tested; `payment_intent.succeeded` does not grant credits. Deployment docs name only `checkout.session.completed`. | **Fixed locally; 2 focused webhook tests passed; Stripe Dashboard setup remains external.** |
| Static investor/demo content linked to a query-parameter auth bypass on `/jobs`. | The demo could bounce to login or weaken recruiter route policy. | A dedicated fixture-only `/showcase/jobs` route replaces the live-URL/window-search workaround. Protected `/jobs` has one policy; the static deck link and route contract are tested. | **Fixed locally; 46 SEO/static-route tests passed.** |
| Candidate evidence without an internal URL rendered a dead link. | Public report users saw a non-functional affordance. | It now renders a non-link name when no safe URL exists; public snapshots omit internal/ATS links. | **Fixed locally and tested.** |

## Feature truthfulness: unavailable, gated, and intentionally retained

The audit treats a feature flag, route, or scaffold as functional only when its
runtime implementation and safety controls exist. Configuration alone is not
proof of capability.

| Capability | Current truth | Enforcement |
|---|---|---|
| Enterprise SAML SSO | **Unavailable**, not partly functional. There is no assertion-consumer endpoint, signed AuthnRequest, audience/issuer validation, or replay protection. | API reports unavailable, enable attempts return 501, and legacy enforcement flags are cleared on the next owner save. It must not be marketed as working. |
| Workspace-enforced 2FA | **Unavailable** because no second-factor challenge exists. | Enable attempts return 501 and stored legacy enforcement cannot lock users out. |
| `portfolio_agent` | **Reserved, unavailable capability name**; cohort features are not implemented. Its inert package was deleted rather than preserving a fake runtime scaffold. | The canonical registry entry remains `available=False`; production flag evaluation fails closed even if a database row is enabled. |
| `capability_auditor` | **Reserved, unavailable capability name**; it produces no findings. Its inert package was deleted. | The canonical registry entry remains `available=False`; fail closed. |
| `causal_mode` | **Reserved, unavailable capability name**; causal inference/claim validation is absent. Its inert package was deleted. | The canonical registry entry remains `available=False`; fail closed. |
| `GRAPH_OUTCOME_PRIOR_ENABLED` | **Scaffold only**. Bounded shadow-payload math exists, but the graph fetch deliberately returns no prior. | Configuration rejects `true`; no score nudge can be applied until a bias-gated shadow-data review and durable activation path exist. |
| Fitted-policy shadow/promotion | **Dormant**. Nightly fitting and manual/test bookkeeping primitives exist, but the production decision engine remains rule-driven. | Equivalent ordered training inputs have a deterministic fingerprint and reuse the current fitted row before grid/agentic search; per-organization advisory locking and bounded pending output prevent duplicate compute/rows. No production path loads or promotes it, and no scheduler opens/records/concludes shadow runs, so candidates still fail closed pending the durable lifecycle and explicit operator activation. |
| `bias_monitor_continuous` | **Reserved per-org capability flag, unavailable.** This is distinct from the real adverse-impact aggregate service/task, which is implemented behind the environment-level `PRESCREEN_ADVERSE_IMPACT_MONITOR_ENABLED` control. | The registry stays `available=False` because the per-org flag is not wired to scheduling or alert delivery. The actual opt-in monitor uses segregated voluntary EEO self-ID, small-cell suppression, aggregate-only persistence/API output, and honest `insufficient_data`; production enablement still requires governance and an alert owner. |
| Direct task-authoring API | Deliberately disabled in the normal product. | `TASK_AUTHORING_API_ENABLED` remains an operator gate; the useful role-driven generation/battle-test/approval path remains. |
| Workable inbound webhook | Reserved but not an active ingestion feature. | Signature-verifying endpoint remains disabled/501 until a durable consumer is explicitly shipped; scheduled/manual OAuth sync is the supported flow. |
| Stripe top-ups | Functional through Checkout completion only. | One grant event, replay-safe. Other payment-intent events intentionally do not grant. |
| Public developer API base | Optional, not a second required backend. | Derived from `VITE_API_URL` unless a separately verified `VITE_PUBLIC_API_BASE_URL` is deliberately configured. |

## Website and frontend review

### Prior UX audit and current remediation state

The July UX audit recorded 156 issues: 50 P1, 76 P2, 30 P3, and no P0.
The merged work documented in `docs/UX_AUDIT_2026-07.md` addressed the broad P1
classes: payload/round-trip waste, invisible asynchronous work, dangerous
interactions, inconsistent error states, mobile form behavior, route guards,
legal/marketing dead ends, publish validation, and design-token drift. This
worktree extends those fixes rather than removing capability.

Current frontend changes include:

- canonical protected/public/showcase route policy, a real 404, legal routes,
  static multi-page entries, and route-specific SEO metadata;
- explicit queued/dispatching background-job state and a unified Process dialog
  that previews all four steps before execution;
- chat-history pagination with stale-response generation guards and correct
  tool-result stitching;
- collection pagination helpers that preserve complete filter options while
  default list screens remain bounded;
- per-tab stale-while-revalidate caches with LRU bounds and logout clearing;
- hidden-tab polling suppression for agent chat and pool rescore;
- route/module lazy loading and manual vendor chunks for React, charts, Monaco,
  icons, and graph rendering;
- bundle budgets for raw and gzip JS/CSS plus the application entry;
- scoped semantic graph/design tokens rather than hard-coded canvas colors;
- assessment timer and hook-order corrections;
- legal/privacy pages, developer API tests, marketing/showcase route tests, and
  dead-link prevention on public candidate snapshots.

The machine UI guard reports zero unresolved token or component-policy
violations. Frontend architecture and motion-system gates pass. The final
127-file/873-test gated run is warning-free: React scheduling warnings fell from 58
to zero, Router future-flag warnings from 30 to zero, and Motion diagnostics
from two to zero. No console suppression was added, and CI now rejects those
warning classes so the clean signal cannot silently regress.

### Frontend optimization assessment

These optimizations keep the same or better result:

| Change | Efficiency gain without capability loss |
|---|---|
| Bounded initial collections + `loadAllPages` where completeness is required | Faster first render and smaller payloads while filters/export-like consumers can still retrieve every page. |
| Chat message pagination | Long conversations no longer hydrate the entire history; older messages remain explicitly loadable. |
| Stale-while-revalidate resource/document caches | Navigating back paints immediately and still revalidates; caches are capped and cleared across auth boundaries. |
| Visibility-aware polling | Hidden tabs stop spending network/CPU while visible state remains current on return. |
| Lazy routes and vendor chunks | Initial pages do not download Monaco, Cytoscape, or charts until needed; those features remain available. |
| Bundle budget | Prevents silent regressions rather than deleting a feature to meet a one-time target. |
| SQL-side filter facets/pagination | The UI receives accurate, complete facets without pulling full rows or CV bodies. |

### Post-deploy website verification still required

After the coordinated release, repeat the deployed-domain matrix at desktop
and mobile widths: landing navigation, login/register/reset/verification,
protected-route redirects and safe `next`, `/demo`, `/showcase/jobs`, legal and
blog static entries, developer portal, application form, assessment start/timer/
submit, recruiter Process progress/cancel, Workable callback, Stripe return,
404s, keyboard focus, screen-reader/assistive-technology behavior, automated
accessibility findings, reduced motion, and no horizontal overflow. Capture
real Core Web Vitals and confirm cache headers/compression at the CDN. The local
browser pass cannot prove that production received the new assets and services.

## Backend and database optimization assessment

| Area | Finding and remediation | Status |
|---|---|---|
| Collection reads | Roles/tasks/drafts/requisitions/careers now use stable limit/offset reads and SQL filtering. Careers has a constant-query-count regression. | **Fixed locally.** |
| Base analytics | Historical assessment rows are no longer hydrated for Python aggregation; one SQL aggregate computes totals, rates, score buckets, dimensions, duration, and weekly completion. | **Fixed locally; parity regression passed.** |
| Large route ownership | Process logic moved into a 279-line domain route and dedicated dispatch service; collection/analytics/query responsibilities were also extracted. Exact file-size ratchets prevent re-growth. | **Improved locally.** |
| Architecture enforcement | AST gates detect all supported decorator and imperative route-registration forms, require real admin-guard calls, flatten the assembled FastAPI route table (including lazy included routers), and compare actual authentication/agent-action calls. Exact fail-closed inventories cover public/token ingress and one intentional generated-user-route collision; comments, strings, filename changes, mounts, and include prefixes cannot bypass the checks. | **Fixed locally; 19 architecture-gate tests passed.** |
| Remaining file bloat | Forty-two backend files remain on exact legacy baselines. The gate enforces 500 physical lines for route/service modules and 1,000 for every other `app` module; it rejects growth above every exact baseline, so moving or renaming an oversized file cannot evade policy. This is maintainability debt, not an excuse for a risky blind rewrite in an already large patch. | **Non-release refactor debt; ratchet and bypass regressions pass.** |
| Worker placement | Paid/long-running scoring, processing, delivery, recovery, and reconciliation are off request/web-process lifetime. | **Fixed locally.** |
| Database connection | Runtime web/workers use only Railway's private `DATABASE_URL`; `DATABASE_PUBLIC_URL` is deploy-tool-only for migrations outside Railway. | **Fixed locally; 25 database/deployment contract tests passed earlier in this audit.** |
| Fresh database and PostgreSQL semantics | A canonical `000_initial_schema` reconstructs the pre-Alembic base so a genuinely empty PostgreSQL database can traverse the full chain. The supported wrapper rejects unversioned partial schemas, takes a bounded advisory lock, applies every revision, and validates model/invariant parity. Migration 176 restores database-side `now()` defaults for `candidate_applications.pipeline_stage_updated_at` and `application_outcome_updated_at`, a PostgreSQL-only defect that SQLite masked. Runtime contracts also exercise real JSONB/JSON-array search, event idempotency-key uniqueness, update-immutable audit triggers (DELETE remains intentionally available for cascade cleanup), advisory-lock serialization/release, and disjoint `FOR UPDATE SKIP LOCKED` claims. | **Fixed locally; 17/17 bootstrap, immutability, and runtime tests passed on disposable PostgreSQL 16 through sole head 176.** |
| Test isolation | Backend tests select shared in-memory SQLite before app import, avoid disk `test.db`, and have a dedicated real-Postgres CI contract. The only `sqlite:///./test.db` occurrences now assert production rejection behavior. | **Fixed locally.** |

The backend size gate now covers every Python module under `app`: route/service
modules have a strict 500-line ceiling, all other modules have a 1,000-line
ceiling, and 42 oversized legacy files have exact ratcheted baselines. Baselines
were lowered when files shrank, including the process-dispatch extraction;
renaming or moving a hotspot cannot create a blanket exemption. Synthetic
regressions prove that an oversized renamed module and an imperative
`add_api_route` route cannot evade the gate.

The online canonical migration path is the supported contract and a disposable
PostgreSQL 16 database completed `000→176`. Migration 176 also generated valid
offline PostgreSQL SQL for the incremental `175→176` step. This report does
not claim that the entire historical chain supports Alembic's offline/mock
connection mode: legacy migration 015 still assumes a live connection. That
offline limitation does not invalidate the successfully exercised online path,
but it must not be misstated as a full-chain offline-SQL result.

## Cost optimization assessment

No optimization below reduces model quality, recruiter evidence, assessment
depth, or recovery guarantees.

| Cost source | Remediation | Why output is preserved or improved |
|---|---|---|
| Repeated failed pre-screen calls | Six-hour error backoff, with a fresh CV overriding backoff. | Stops the documented 7,668-repeat pattern while still retrying and immediately honoring new candidate evidence. See the residual transient-error refinement below. |
| Huge auto-score bursts | 50 eligible applications per role/tick plus event-driven intake and bounded backlog sweep. | Same backlog is drained steadily with less queue pressure and no candidate loss. |
| Paid no-op agent cycles | Survey-based early exit when no candidates, questions, or intent gaps are actionable. | Skips only a cycle whose correct result is “nothing to do.” |
| Duplicate provider delivery | Durable receipts, leases, stable idempotency keys, exact-payload recovery, and ambiguous-terminal state. | Prevents paying twice while making uncertain outcomes visible instead of guessing. |
| Repeated dormant fitted-policy searches | A versioned deterministic fingerprint covers ordered training inputs/configuration. Equivalent current candidates are reused before grid or agentic search; per-organization serialization and bounded pending output prevent duplicate fitting work. | The same fitted result is retained for identical evidence, while changed evidence/configuration still produces a fresh candidate. Reuse does not activate or auto-promote the dormant model. |
| Holistic input tokens | Stable role context is cached on both Sonnet calls. | Dynamic candidate evidence remains uncached and current; the complete recruiter report is retained. |
| Retired Haiku aliases | Fallback resolution tries the configured current model before historical aliases. | Avoids a guaranteed retired-model failure and retry while retaining compatibility fallbacks and the same successful model result. |
| CV parsing | Anthropic Message Batches remain used for latency-tolerant parsing. | Keeps discounted asynchronous execution where interactivity is unnecessary; interactive recruiter scoring uses recoverable per-app fan-out. |
| Analytics/list payloads | SQL aggregation, projections, bounded pages, and `load_only` that excludes `cv_text` from adverse-impact aggregation. | Returns the same aggregate/list meaning without hydrating heavy bodies. |
| Browser polling/download | Visibility-aware polling, bounded per-tab caches, route lazy loading, vendor chunking, and bundle budgets. | UI remains current and all features remain available on demand. |
| Git-backed assessment test setup | The mock branch allocator enumerates once and uses session-scoped temporary roots. | Preserves production Git behavior while reducing a representative success-path test from about 34.35 seconds to 0.23 seconds and still covering 501 occupied branch names. |
| Runtime dependency surface | Test-only packages moved to development requirements and unused Passlib was removed. | Production images and audits process fewer packages without removing runtime capability. |
| Cost observability | Retry/validation telemetry, usage events, call logs, and cost-per-outcome tooling remain intact. | Efficiency can be measured rather than inferred; audit artifacts were not removed. |

The holistic recruiter report is intentionally retained for completed holistic
scores, including clear rejects. It is the durable explanation/audit artifact,
not redundant decoration. Removing it would make the product less useful and
less defensible.

## Redundancy, superseded features, and workarounds

### Removed or replaced locally

| Item | Resolution |
|---|---|
| Historical `intent_parser` sub-agent and its obsolete test | Removed; durable `RoleIntent` is canonical. Phase documentation now says so. |
| Duplicate scoring schema surface | The old component schema file was removed; `app.cv_matching.schemas` is canonical. |
| Four stale static preview HTML pages | Removed where React preview/product routes supersede them. |
| Query-string/window-location auth bypass for the investor Jobs demo | Removed; `/showcase/jobs` is a dedicated public fixture route. |
| `_RateLimitStoreCompatibility` | Removed; it existed only to accommodate old tests and complicated production behavior. |
| Orphan credit-ledger helper | Deleted after import-graph inspection showed no production consumer and duplicate ledger-mutation responsibility. Canonical credit behavior remains covered by its existing service/tests. |
| Inert capability packages and shared stub helper | Deleted the no-op packages for the four reserved names (`portfolio_agent`, `capability_auditor`, `bias_monitor_continuous`, and `causal_mode`) plus `_stub_helpers`. Their canonical registry reservations remain unavailable/fail-closed. This did not delete the real adverse-impact aggregate service/task; that separate monitor remains environment-controlled and governance-gated. |
| Broken root demo-data seeder | Deleted `scripts/seed_data.py`: it had no consumer, imported the retired `app.core` layout, could not run against the current models, and embedded a demo credential. Current bootstrap/experiment seed paths remain intact. |
| Two pass-only “tests” | Replaced with real assertions: role-intent prompt length is capped at 1,200 characters, and exemplar selection proves `k=2` prompt/side-effect bounds. |
| Historical plan presented as active backlog | `RALPH_TASK.md`, README, and `PRODUCT_PLAN.md` now consistently identify it as a historical implementation record and point executable work to issues/PRs. |
| Stale deployment/environment instructions | Direct `railway up` is explicitly unsupported for production; health endpoints, `ADMIN_SECRET`, PostgreSQL 16 Compose/README guidance, Vite 8, `VITE_PUBLIC_API_BASE_URL`, Stripe grant event, UTF-8 password-byte copy, and disk-SQLite guidance now match code. |
| Unused scoring batch experiment | Previously superseded and removed; stale `runner_batch` documentation was corrected. Scoring uses durable per-application fan-out, while Message Batches remain only where asynchronous latency is appropriate. |

### Deliberately retained compatibility

Legacy score aliases, old assessment/application rows, route aliases such as
`/reporting` and `/copilot`, integration-secret current/previous-key reads plus
the unversioned/plaintext legacy-row fallback, and granular role-setting
fallbacks are compatibility bridges for existing data, bookmarks, or clients.
They are not safe deletion targets merely because they contain the word
“legacy.” The plaintext credential path is migration debt, not an acceptable
steady state: inventory and re-encrypt or rotate existing rows before removing
it. Remove any other bridge only after a migration, consumer inventory,
production telemetry window, rollback plan, and contract tests prove that the
same user result is preserved.

The synchronous SQLAlchemy engine remains an architectural predecessor while
the platform's long-running work has moved to workers. Converting the entire
application to async database access is a separate measured migration, not an
automatic performance win and not appropriate as an unbounded rewrite inside
this remediation diff.

## Tests: redundancy removed, coverage added, and remaining gaps

The tree contains broad backend and frontend test suites, but collection counts
alone are not evidence that important branches are covered. The audit therefore
records specific behavioral proofs plus one uninterrupted final backend run and
its branch-coverage result; partial runs used while repairing stale tests are not
presented as release evidence.

### Test improvements in the worktree

- durable dispatch tests cover broker failure, duplicate delivery, stale leases,
  exact payloads, provider idempotency, ambiguous paid windows, and recovery;
- migration/production contracts cover private runtime database selection,
  redacted public readiness, authenticated admin health, worker topology, and
  environment requirements;
- OAuth state, URL origin, key rotation, owner authorization, proxy IP, secret
  redaction, CORS, and disabled integration contracts are exact assertions;
- pre-screen tests cover authoritative provenance, thresholds, Unicode,
  integrity parity, calibration sampling, adverse-impact aggregation, and
  operational fan-out;
- frontend tests cover route policy, showcase isolation, legal/SEO metadata,
  timer catch-up, status hook order, Process payloads, pagination races, caches,
  filter completeness, provider callbacks, and public dead links;
- collection-query tests assert bounded stable pages, complete SQL facets,
  constant query count, and aggregate parity;
- Stripe tests assert both replay idempotency and that payment-intent events do
  not grant credits;
- a one-off AST/hash duplicate scan during integration found zero duplicate
  implementation bodies at that snapshot. It is not presented as a maintained
  repository gate; the reproducible import-reachability and architecture gates
  provide the durable enforcement described below;
- the reachability scanner currently reports 670 modules, 21 explicit runtime
  roots, 665 reachable modules, and zero candidates. Roots are only `app.main`,
  `app.tasks`, `app.models`, and 18 exact approved CLI modules; arbitrary
  `__main__` guards and prefix lookalikes cannot self-declare liveness. It
  models parent packages, ignores imports reachable only through
  `TYPE_CHECKING`, `if False`, or a non-approved main-guard body, reports a
  non-empty unreachable `__init__.py`, and ignores an empty one. Ten focused
  scanner regressions cover relative imports, dead cycles, self-importing
  packages, CLI success/failure, lookalikes, type/dead imports, and package
  initializers;
- architecture tests inspect the assembled FastAPI application rather than
  text alone: AST-enforced admin calls, decorator/imperative registrations,
  include-time prefixes, nested mounts, exact public/token ingress, actual
  write authentication, agent/action parity, and the one intentional duplicate
  generated user route are all pinned;
- backend CI compiles `alembic`, `app`, `scripts`, and `tests`, checks the sole
  migration head, Ruff E9/F failures, the all-module file-size ratchet,
  dependency integrity/audit, reachability dead code, PostgreSQL runtime
  contracts, full pytest/coverage, and PR/push-aware diff whitespace. Frontend
  architecture, motion, UI-token, dependency, test, build, bundle, and
  built-route gates remain represented in frontend CI;
- CI reproducibility/cost controls pin Ubuntu 24.04, Python 3.11.9, Node
  22.23.1, every third-party action to a full commit SHA, and PostgreSQL 16.14
  to an image digest. A generated hash-locked backend dependency graph is
  validated against both input requirement files and installed with
  `--require-hashes`; concurrency cancels superseded branch/PR runs, and a
  conservative path classifier skips unaffected backend/frontend jobs while
  unknown paths run both.

The exact workflow supply-chain pins are
`actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5`,
`actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065`,
`actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020`, and
`actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02`.
PostgreSQL is
`postgres:16.14@sha256:17e67d7b9890c99b055ba1e0d5c5be4ec27c9d3a72bda32db24a5e5d8a85af0c`.
The lock records input digest
`4b686ff622e8415dc009908a9e7318b0f359303eb9e21b1d233e7c341ff05c09`;
the verifier recomputes it from production and development requirement inputs
before either backend job installs the fully hashed graph.

### Targeted verification evidence available now

Do not sum these figures; several sets overlap.

| Focus | Result recorded during remediation |
|---|---|
| Pre-screen/scoring regressions | 131 passed |
| Pre-screen operational durability | 15 passed |
| Pre-screen architecture gates | 18 passed |
| Process-role durability and related backend paths | 47 passed |
| Agent-v2 focused set | 18/18 passed |
| Limiter/apply/EEO focused set | 70/70 passed before canonical client-IP integration; the later canonical-IP integration set passed 65/65 |
| Health/impact/snapshot set | 35 passed |
| Database/deployment contract set | Latest combined startup/Railway set passed 42/42 |
| SEO/static-route set | 46 passed |
| Stripe replay/non-grant | 2/2 passed |
| Developer API reference | 1/1 passed |
| Backend file-size ratchet | Passed: route/service modules ≤500 lines, every other `app` module ≤1000, and 42 exact legacy baselines; two bypass regressions passed |
| One-off duplicate implementation scan | Zero AST/hash duplicates at the integration snapshot; not a maintained gate |
| Dead-code reachability graph | 670 modules / 21 explicit roots / 665 reachable / zero candidates; 10 focused scanner regressions and fail-on-candidates gate passed |
| Backend architecture gates | 19/19 passed across route ownership, assembled collisions/authentication, admin-call AST checks, ingress inventories, and agent/action parity |
| Frontend architecture + motion | Passed |
| Frontend UI token/component policy | Passed with zero violations |
| Frontend ESLint + TypeScript contract | Passed |
| Full frontend gated Vitest | 127 files / 873 tests passed in 20.10 seconds; zero warning diagnostics (58 React scheduling, 30 Router, and 2 Motion diagnostics reduced to zero). CI now preserves Vitest failures and independently fails on those warning classes. |
| Frontend production build + bundle budget | Passed; largest vendor chunk was the graph bundle at 434.15 kB |
| Frontend dependency audit | 0 vulnerabilities |
| Complete default non-production backend pytest selection | 5,574 passed / 8 PostgreSQL-only skipped / 16 live production-smoke tests deselected; zero failed in one uninterrupted 302.40-second branch-tip run. PostgreSQL behavior is covered separately below. |
| Backend coverage | 75.06% combined line-and-branch coverage: 53,061/67,611 lines (78.48%) and 12,961/20,346 branches (63.70%). `.coveragerc` combined ratchet raised from 35% to 74% and revalidated; the XML and raw data were emitted only as ephemeral local artifacts and removed from the worktree. |
| Backend dependency integrity/audit | `pip check` passed; `pip-audit` found zero known vulnerabilities in the hash-locked graph |
| Static/syntax/diff checks | Full backend `compileall` and Ruff scopes passed; both workflow YAML files and all 31 embedded shell blocks parsed; tracked-diff and untracked-text whitespace/EOF checks passed |
| PostgreSQL bootstrap/runtime/immutability | 17/17 passed on PostgreSQL 16; online `000→176`, five search indexes, event idempotency-key uniqueness, two update-immutable triggers (DELETE intentionally allowed), timestamp defaults, timeout enum, malformed/partial/lock-timeout failure paths, JSONB search, advisory locking, and `SKIP LOCKED` claims |
| Migration 176 offline increment | `175→176` PostgreSQL SQL generation and sole-head checks passed; no full historical offline-chain claim is made |
| CI/lock contracts | Hash-lock validation, exact runtime/action/image pins, concurrency, path-scope, production-target, and warning-gate workflow tests passed |

### Insufficient-test and governance gaps

1. The final backend run measured 75.06% combined line-and-branch coverage:
   78.48% line coverage and 63.70% branch coverage. The combined ratchet now
   enforces 74%, up from 35%. Aggregate coverage is not sufficient assurance for
   every payment, authorization, provider-failure, worker, and hiring-decision
   branch; raise it incrementally with risk-focused tests without deleting hard
   branches or marking them `no cover` merely to improve the number.
2. The application contains 223 `pragma: no cover` annotations (221 at the
   audited HEAD); 201 sit on broad exception handlers. The two net-new
   annotations protect closed internal discriminators, while three newly added
   testable failure branches had their exclusions removed and gained direct
   regressions. Burn down the legacy exception exclusions behind fault-injection
   tests; the measured percentages exclude those clauses and must not be read as
   proof that every defensive branch was exercised.
3. The normal backend suite intentionally uses isolated in-memory SQLite, while
   PostgreSQL-only modules require `TEST_POSTGRES_URL`. CI provisions
   PostgreSQL, and this worktree's 17 focused PostgreSQL 16 bootstrap,
   immutability, JSONB, advisory-lock, and `SKIP LOCKED` contracts are green
   through sole head 176. A recent production-shaped snapshot rehearsal remains
   the data-upgrade release gate; the already-completed empty-database run
   should not be described as outstanding.
4. Frontend Vitest, ESLint, TypeScript, production build, bundle budget, and
   local built-route/browser checks are green. They must be repeated in CI and
   the route/provider matrix repeated after deployment; local success is not a
   deployed-site claim.
5. Real Anthropic, E2B, Resend, GitHub, Stripe, Workable, Fireflies, Railway,
   Vercel, and CDN behavior cannot be proven with mocks. Use controlled staging
   or production smoke credentials; never run paid/destructive checks from an
   unreviewed local tree.
6. Assessment/scoring validity is not equivalent to software test coverage.
   The prior production deep dive found very low meaningful-candidate volume;
   calibration, adverse-impact, and predictive-validity claims require governed
   real outcomes.
7. The dead-code gate proves reachability from 21 exact reviewed roots, not
   symbol-level usage. Dynamic imports remain the principal blind spot, and a
   zero module-candidate result is not proof that every retained function/class
   is live. Scanner-root or AST-policy changes require review; arbitrary main
   guards, prefixes, type-only imports, dead branches, and non-empty package
   initializers are already covered by focused regressions.
8. Local Python 3.12/Node 24 verification does not replace the supported
   Python 3.11.9/Node 22.23.1 CI matrix, especially after broad
   framework/dependency upgrades.

## Outstanding Codex/GitHub review work

GitHub was inspected on 2026-07-15. There are 25 open pull requests: 11 drafts
and 14 non-drafts. Sixteen currently report merge conflicts and nine report
mergeable. None has requested reviewers or a GitHub review decision. That does
not mean they are approved; it means review ownership is absent.

The two newest Codex PRs are:

| PR | State | Review evidence |
|---|---|---|
| [#1023 Add multi-user job collaboration controls](https://github.com/sampatel3/tali-platform/pull/1023) | Draft, conflicting | Zero reviews and zero review threads returned; no requested reviewer. |
| [#1021 Make sourcing and post-evaluation flow agent-driven](https://github.com/sampatel3/tali-platform/pull/1021) | Draft, conflicting | Zero reviews and zero review threads returned; no requested reviewer. |

Thread-level review inspection also found **16 unresolved historical review
threads**: PR #876 has 2, #855 has 1, #852 has 2, #638 has 5, and #557 has 6.
Five are attached to outdated diffs and eleven are active (six P1, five P2).
Current HEAD is associated with merged PR #1026, not an open PR. Open PR #1023
contains that commit as an ancestor but is 13 commits ahead and has no review
threads; every branch with unresolved feedback has diverged from this worktree.
Those comments therefore cannot safely be folded into this remediation branch
or resolved on GitHub without branch-specific review authority.

| PR | Active unresolved feedback |
|---|---|
| [#876](https://github.com/sampatel3/tali-platform/pull/876) | Validate reject-sweep state before approval (P1); reuse the sweep offer when chat enables auto-reject (P2) |
| [#855](https://github.com/sampatel3/tali-platform/pull/855) | Gate dragging for spec-derived criteria (P2) |
| [#852](https://github.com/sampatel3/tali-platform/pull/852) | Recompute scoring inputs after specification edits (P1); remove the branch's local `node_modules` symlink (P2) |
| [#638](https://github.com/sampatel3/tali-platform/pull/638) | Four active workflow/vendor-drift security findings: private-token isolation, checkout credentials, PR-script isolation (all P1), and untracked-file safety (P2) |
| [#557](https://github.com/sampatel3/tali-platform/pull/557) | Keep target workers stopped through cutover (P1); restore PostgreSQL before first web boot (P2) |

Current-tree applicability was checked separately. The #876 sweep-offer flow
and #855 drag implementation are not present here; the current criteria editor
has no drag behavior. The #852 spec-edit contract now re-derives criteria and
quotes an explicit, recruiter-authorized rescreen rather than silently spending,
and `frontend/node_modules` is a normal ignored directory rather than the
branch's tracked symlink. The #638 drift-gate workflow/script and #557 region
migration plan are absent from this tree. None of the 11 active comments exposes
an unfixed current-tree path, but their own PR branches still require the listed
changes or explicit closure as superseded.

The remaining queue is old enough and overlaps enough with the current product
that it should be triaged explicitly, not bulk-merged:

- **Conflicting:** [#876](https://github.com/sampatel3/tali-platform/pull/876),
  [#852](https://github.com/sampatel3/tali-platform/pull/852),
  [#788](https://github.com/sampatel3/tali-platform/pull/788),
  [#735](https://github.com/sampatel3/tali-platform/pull/735),
  [#604](https://github.com/sampatel3/tali-platform/pull/604),
  [#553](https://github.com/sampatel3/tali-platform/pull/553),
  [#532](https://github.com/sampatel3/tali-platform/pull/532),
  [#502](https://github.com/sampatel3/tali-platform/pull/502),
  [#437](https://github.com/sampatel3/tali-platform/pull/437),
  [#401](https://github.com/sampatel3/tali-platform/pull/401),
  [#381](https://github.com/sampatel3/tali-platform/pull/381),
  [#377](https://github.com/sampatel3/tali-platform/pull/377),
  [#347](https://github.com/sampatel3/tali-platform/pull/347), and
  [#339](https://github.com/sampatel3/tali-platform/pull/339), plus #1023/#1021.
- **Currently mergeable, still unreviewed/unowned:**
  [#855](https://github.com/sampatel3/tali-platform/pull/855),
  [#784](https://github.com/sampatel3/tali-platform/pull/784),
  [#744](https://github.com/sampatel3/tali-platform/pull/744),
  [#638](https://github.com/sampatel3/tali-platform/pull/638),
  [#557](https://github.com/sampatel3/tali-platform/pull/557),
  [#432](https://github.com/sampatel3/tali-platform/pull/432),
  [#430](https://github.com/sampatel3/tali-platform/pull/430),
  [#400](https://github.com/sampatel3/tali-platform/pull/400), and
  [#382](https://github.com/sampatel3/tali-platform/pull/382).

For each PR, compare its intent with the current main/tree, then choose one of:
assign reviewer and update; extract a still-needed small change; or close as
superseded with a link to the replacement. “Mergeable” is only GitHub's
conflict calculation, not a quality or relevance verdict. The local audit tree
is not attached to any PR and must not be treated as review-complete.

## Honest residual risk register

These items are not hidden under “fixed.” Some require a deliberate decision
because the wrong optimization would make results less accurate or the product
less useful.

| Priority | Residual | Why it remains | Required next decision/action |
|---|---|---|---|
| P0 / deployment | **The deployed site is still old and operationally degraded:** it advertises the retired API origin, exposes the older detailed health contract, and reports missing worker/scoring heartbeats. | Local remediation cannot change or validate the running Railway/Vercel release. A degraded production check cannot be overridden by a green local worktree. | Treat production as not release-ready: complete the release-candidate gates, deploy through the controlled path, then require redacted health/readiness and both queue canaries before declaring recovery. |
| P1 / cost and capability | **Fitted-policy shadow/promotion is dormant while the nightly fitter is scheduled.** Equivalent ordered inputs now reuse the current fingerprinted candidate before expensive search, per-organization work is serialized, and pending output is bounded. Changed evidence can still consume DB/CPU to fit a candidate, but the production engine never loads it and no scheduler opens, records, or concludes durable shadow runs. | Fitted output is currently only a fail-closed safety input to governed rule retunes. Automatically wiring the bookkeeping would still lack durable per-decision shadow identity, realised-outcome linkage, and operator activation; compute deduplication is not feature activation. | Measure the remaining scheduled fit's cost and safety value. Keep it only if that value is justified; otherwise disable the dormant fit schedule without weakening the live rule retuner. Before learned-policy activation, implement the durable shadow lifecycle, bias/outcome gates, observability, and explicit operator promotion. |
| Medium / scaffold | **`GRAPH_OUTCOME_PRIOR_ENABLED` is not a functional feature.** Bounded shadow math exists, but the fetch returns `None` and configuration rejects enablement. | Outcome-learned graph signals can reproduce historical bias; a numeric nudge without evidence and governance would make matching less trustworthy. | Keep it unavailable in product/configuration. Activate only after graph retrieval is durable, the shadow distribution and predictive value are reviewed, the autoresearch bias gate passes, and rollback/monitoring exist. |
| Medium / security migration | **Legacy integration credentials may still use the unversioned/plaintext read fallback.** | The fallback prevents breaking existing Workable/Fireflies rows during rollout; new encrypted writes alone do not transform old data. | Inventory existing rows, re-encrypt or rotate them with the production integration key, verify previous-key rollback, and remove plaintext reads only after telemetry proves the migration complete. |
| Medium / runtime | **Local and supported runtimes differ:** local checks used Python 3.12/Node 24; CI targets Python 3.11.9/Node 22.23.1, the backend image pins Python 3.11.9, and frontend deployment declares Node `>=22.12.0`. | Parser, dependency, event-loop, build-tool, and native-package behavior can differ even when unit tests pass locally; the frontend host can select a different Node 22 patch than CI. | Require the full supported-runtime CI matrix before the release candidate and align the frontend hosting runtime as tightly as the provider supports. |
| Medium / staging | **Broad framework/dependency upgrades have cross-cutting compatibility risk.** | Unit mocks cannot fully exercise server lifecycle, auth/security middleware, provider SDKs, PDF/browser tooling, or deployment packaging under real infrastructure. | Stage the complete upgraded graph on supported runtimes; run migration, auth, provider, file/PDF, worker, and rollback smoke before production. Do not “optimize” by deleting supported behavior to make the upgrade easier. |
| Medium / accessibility | **The website pass did not include physical devices, automated accessibility tooling, or assistive-technology/screen-reader use.** | Source review, desktop/mobile browser smoke, keyboard focus, and reduced-motion checks do not prove semantic announcements, focus order, touch behavior, or real-device rendering. | Run automated accessibility checks plus keyboard and screen-reader smoke on representative public, auth, candidate, assessment, and recruiter flows after the release candidate is deployed to staging. |
| Medium | **A5 input-window divergence:** pre-screen sees untruncated CV/JD while holistic uses 14k/8k windows. | Silently truncating the gate could miss late must-haves; silently expanding holistic may raise token cost and latency. | Choose a canonical evidence-window policy, test long-document must-have placement, and measure accuracy/cost before rollout. |
| Medium | **R6 transient error delay:** all recent pre-screen errors back off up to six hours. | Backoff fixed a severe 7,668-repeat cost incident, but treats rate limits/timeouts like deterministic failures. | Classify errors; use a short bounded retry for transient 429/5xx/timeout and long backoff for deterministic failures. Align the docstring. |
| Low | **C5 cache/staleness mismatch:** Workable context is in the holistic cache key but not the rerun trigger. | Context churn can pay for a cache miss without a coherent product rescore policy. | Decide which Workable changes are material; coarsen the key or add matching invalidation, then measure hit rate. |
| Low | **S3 pre-screen session overhead:** three to four committed DB sessions surround one fast call. | Some separation is load-bearing for FK visibility and metering durability. | Fold safe hit-count work into an existing transaction and benchmark; move writes only if audit/order guarantees remain. |
| Medium / policy | **F3 protected-characteristic handling:** conversational guidance is stronger than the deterministic reject path. | Free-text CV/ATS context can contain protected/proxy information. | Establish a code-level non-use/redaction invariant, legal review, and shadow/adverse-impact evidence before stronger automation. |
| Medium / policy | **F4 automated-decision notice/explanation/appeal:** internal provenance exists but candidate-facing process is incomplete. | This is product/legal workflow, not a safe backend-only guess. | For opted-in auto-disqualify orgs, design candidate notice, job-relevant explanation, human review/appeal, and jurisdiction policy. |
| Medium / evidence | Adverse-impact monitor can be enabled with insufficient voluntary data. | Code cannot manufacture lawful representative data. | Define voluntary-data process, owner, alert response, retention, and minimum sample policy before enabling. |
| Medium / maintainability | 42 backend oversized legacy files, the frontend `AppShell`, and 11 oversized frontend pages remain ratcheted. Backend policy is strict at 500 lines for route/service modules and 1,000 for all others. | Large-scale mechanical splitting inside an already broad behavioral remediation would increase merge and regression risk. | Burn down incrementally behind exact parity tests; lower baselines whenever files shrink and do not add new exemptions. |
| Low / test quality | The frontend warning backlog is closed and the combined backend floor is 74%, but backend branch coverage is still 63.70%. | Aggregate coverage can hide weak failure-path coverage even with a green suite. | Raise branch coverage incrementally around payment, authorization, provider failure, worker recovery, and hiring-decision risk; do not delete hard branches or exclude them merely to improve the number. |
| Product validation | Assessment/scoring instrument has limited real-outcome evidence. | Unit tests prove software behavior, not hiring validity or candidate experience. | Resume governed volume, monitor funnel and outcome calibration, validate predictive/fairness claims, and keep irreversible AI reject recommendations human-confirmed by default. |

## Release and production status

### Local verification results

Complete default non-production backend suite: **5,574 passed, 8
PostgreSQL-only skipped, 16 live production-smoke tests deselected, zero
failed** in one uninterrupted branch-tip run (302.40 seconds). The skipped PostgreSQL
semantics were exercised by the separate 17/17 PostgreSQL 16 contract run
below; live production smoke was not run from this unreviewed tree.

Backend coverage: **75.06% combined line-and-branch** — 53,061/67,611 lines
(78.48%) and 12,961/20,346 branches (63.70%). The enforced combined floor is
now **74%** (raised from 35%) and passed; the XML report was an ephemeral local
artifact rather than a committed review artifact, and both it and the raw
coverage data were removed from the worktree after measurement.

Fresh PostgreSQL migration result: **17/17 bootstrap, immutability, and runtime
tests passed on PostgreSQL 16.** A disposable empty database traversed
`000_initial_schema` through `176_restore_application_timestamp_defaults`; all
five migration-160 search indexes, the event idempotency-key uniqueness
constraint, both migration-143/169 update-immutable triggers (DELETE
intentionally allowed for cascade cleanup), the restored application timestamp
defaults, timeout enum value, and `VARCHAR(255)` revision capacity were present. Partial and lock-timeout
databases received no migration DDL, the advisory lock was released, and real
PostgreSQL semantics passed for JSONB/JSON-array candidate search, rejected
audit-event updates, advisory-lock serialization, and disjoint `SKIP LOCKED`
claims.
Incremental `175→176` offline SQL generation passed; the full historical
offline chain was not claimed because legacy migration 015 requires a live
connection.

### Required release sequence

1. Review the seven local commits, confirm the branch is clean, push it, and
   open a draft PR. The former detached working snapshot is now preserved, but
   the unpushed branch is not an approved release artifact.
2. Run the complete backend CI contract on Python 3.11.9: validate and install
   the hashed requirements lock, dependency integrity/audit; compile `alembic`,
   `app`, `scripts`, and `tests`; Ruff E9/F; sole-head, all-module file-size,
   reachability dead-code, PostgreSQL runtime-contract, full pytest, coverage,
   and PR/push-aware diff-whitespace gates.
3. Run `python -m app.scripts.database_migrate` against a recent
   production-like snapshot/backup in staging. The genuinely empty and
   fail-closed PostgreSQL 16 paths are already green; a production-shaped data
   upgrade remains the final migration rehearsal.
4. Run the complete frontend CI contract on Node 22.23.1: exact install,
   dependency audit, architecture/motion/UI lint, ESLint, TypeScript check,
   full Vitest, production build, bundle budget, and built-route smoke.
5. Assign code-owner, security, data-policy, and product reviewers to the draft
   PR, then resolve overlap with the open PR queue recorded above. Do not infer
   approval from a lack of comments.
6. Provision distinct production `SECRET_KEY`, `INTEGRATION_ENCRYPTION_KEY`, and
   `ADMIN_SECRET`; inventory and re-encrypt or rotate legacy plaintext/
   unversioned integration credentials; verify previous-key rollback and the
   same model, metering, database, Redis, frontend, and policy settings across
   web/general/scoring services.
7. Stage the broad framework/dependency graph on the supported runtimes and run
   migration, authentication, provider, PDF/file, worker, queue, and rollback
   smoke. Keep `GRAPH_OUTCOME_PRIOR_ENABLED=false`; record whether the dormant
   nightly fitted-policy job's measured safety value justifies its compute cost.
8. Apply migrations and deploy only through
   `scripts/railway/deploy_production.sh`. Runtime services must use private
   `DATABASE_URL`; the wrapper may use `DATABASE_PUBLIC_URL` only for the
   external migration step.
9. Require public `/health`, redacted `/ready`, and authenticated
   `/admin/health`; require both general and scoring queue canaries and the
   intended live Anthropic/E2B/Resend/GitHub capabilities.
10. Configure Stripe Dashboard to deliver only the documented top-up grant event
   (`checkout.session.completed`) plus any explicitly non-grant events needed
   for observability. Verify signature, replay, and one-credit-grant behavior.
11. Configure `VITE_PUBLIC_API_BASE_URL` only if a separate public origin is
    verified; otherwise use the backend-derived `/public/v1` base.
12. Keep the adverse-impact monitor off until the voluntary-data process,
    minimum cells, alert owner, and response plan are approved; then verify that
    only aggregates leave the segregated projection.
13. After production deployment, run the manual website/accessibility and
    controlled provider smoke matrix described above, monitor errors, queues,
    cost, and duplicate suppression, and retain a rollback path.

## Final release classification

- **Code remediation:** substantial and locally complete for the safely actionable findings listed as fixed.
- **Integrated release candidate:** **not yet**; blocked by supported-runtime CI, production-shaped migration rehearsal, dependency staging, and a reviewed PR.
- **Production release validation:** **not attempted**; it begins only after an approved release candidate, external configuration, and controlled deployment, then requires the post-deploy health/queue/provider/website smoke matrix.
- **Production:** **not changed by this audit**.
- **External follow-up:** coordinated Railway/Vercel rollout, Stripe configuration, provider smoke, adverse-impact governance, GitHub PR triage/review ownership, and live website validation.
