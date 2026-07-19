# Taali platform audit and remediation report

- **Audit period:** 2026-07-15–2026-07-19
- **Repository:** `sampatel3/tali-platform`
- **Integrated baseline:** current `origin/main` at `9580f1aa7bfff3aa65ba82a6382a91987b50f0d0` (merged PR #1064) is an ancestor of the audit branch, plus the current additive continuation
- **Release status:** **draft PR [#1043](https://github.com/sampatel3/tali-platform/pull/1043) remains open; the evidence-confirmed bloat cleanup is locally validated, requires refreshed PR CI/review, and is not released or deployed**

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

This is not yet a release claim. The remediation has been reconciled through
`main` at `9580f1aa` / merged PR #1064 and continued on the audit branch; the
cleanup has not been reviewed, approved, or deployed. Material
migration, related-role, ATS, metering, authorization, and frontend changes
were added after the earlier `b19e087d` checkpoint and are now covered by fresh
local current-tree evidence. The exact current Node 22 frontend contract,
production build, bundle budget, local in-app browser pass, locked Python
backend suite, dependency/static gates, and retained-PostgreSQL migration and
SQL-cast proofs are green. Refreshed remote CI and review remain pending for the
cleanup commit. A production-shaped migration rehearsal
and review approval also remain release-candidate gates. External configuration,
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
open GitHub pull-request queue. The initial frozen snapshot included 22 paths
that had been removed; the integration review restored every path whose
compatibility or product value was not disproved. When upstream work later
removed two Sister Role dialog paths that remained in the original audit
branch, those were restored unchanged. A later upstream rename of
`jobPipelinePageUtils.js` was restored as a forwarding compatibility facade
with contract tests. The final bloat tranche then deleted only 20 files proven
unreachable or superseded: 19 frontend modules with no runtime or test importer
and one obsolete internal Bullhorn handler module whose dispatch seam had
already been superseded. Git retains the complete history. No migration, data
artifact, route, public API, externally importable compatibility root, or
test-only/unwired product capability was deleted to improve a metric.
That breadth is one reason the supported-runtime and reviewable-PR release gates
remain mandatory.

The audit began at `0e562f2f` (`Redesign agent prompts and unblock task setup
(#1026)`, 2026-07-15), moved onto `codex/platform-audit-remediation`, and first
reached a fully verified integration checkpoint at `b19e087d` / merged PR
#1044, after the plain-English decision integration in PR #1041, related-role
scoring in PR #1042, related-role integration in PR #1040, and
decision-presentation work in PR #1034. PR #1044 restored compact card summaries
without removing the causal explanation. The continuation then integrated
independent related-role workflows from PR #1045, worker-health ordering from
PR #1046, and bulk role controls from PR #1047, reaching `f8768124` through
local merge commits `9cf7968b` and `f1d331e4`. Later reconciliation advanced
through current `main` at `9580f1aa`; draft PR #1043 is cleanly based there at
the previously verified pushed checkpoint `84eddae`. The evidence-confirmed
bloat cleanup follows that checkpoint and the worktree remains undeployed.

At the earlier `b19e087d` checkpoint, the authoritative backend run used the
exact locked Python 3.11.9 environment with all 158 hashed development pins.
The matching frontend run used a clean detached worktree with Node 22.23.1 and
npm 10.9.8; exact install, audit, all gates, production build, bundle budget,
and built-route smoke passed. Those checkpoint results must not be presented as
current-tree proof; the newer 2026-07-18 and release-status sections record the
fresh frontend, backend, coverage, static, migration, and browser evidence for
the current stable tree.
The backend deployment image still pins Python 3.11.9; the frontend deployment
contract now declares Node `>=22.12.0 <23`, enforcing Vite 8's minimum and
matching CI's Node 22 major while allowing the host to apply compatible
security releases within that major. Local Compose,
deployment documentation, and migration verification agree on PostgreSQL 16.

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
contract passed. Desktop and 390×844 local mobile browser checks found one H1
on each reviewed page, no horizontal overflow, working mobile navigation, and
no browser-console errors. The final read-only production homepage check at
1280px also found one H1 and one main landmark after load, no horizontal
overflow, and no console warnings/errors. Production mobile was not freshly
repeated. A fresh post-#1042 local attempt could not keep an in-app browser tab
attached, so no unrelated browser workaround was substituted. The complete
Node 22 suite, route tests, production build, bundle gate, and 14-route built
preview smoke were repeated after PR #1044 and are the fresh executable
evidence for that earlier checkpoint merge. Navigation to the legacy
`api.taali.ai/health` endpoint did not
complete, so backend readiness and queue heartbeats were not verified. Nothing
was deployed by this audit. No physical-device,
assistive-technology/screen-reader, automated accessibility, or field/Core Web
Vitals run was performed. All browser evidence in this paragraph predates the
`f8768124` continuation. A fresh current-tree local landing-page pass at
390×844 additionally found one main landmark, ordered H1→H2/H3 semantics, no
heading skips, overflow, unnamed buttons, invalid links, duplicate IDs, or
console errors. That focused public-page pass does not substitute for staging
authentication, real-device, screen-reader, or field-performance evidence.

## 2026-07-16 continuation reconciliation

This section is additive to the earlier audit. It records what changed after
the last complete suite/browser checkpoint and distinguishes implementation
from verification. No file has been deleted in the diff from `f8768124`, no
deleted path is present in the working status, and `git diff --check` is clean
at this snapshot.

| Continuation area | Reconciliation and safety property | Current evidence/status |
|---|---|---|
| Published migration preservation | Published revision 175 was restored byte-for-byte to SHA-256 `2e0857870616c651e3f905759bd4002a92a85b352d5fa9c2a08468d439e5d58a`. Merge revisions 180 and 181 remain byte-for-byte at `806d9aa4b4d613ee62a363787142bdb61d54450d50812e7ef8359c3e82dda140` and `1e8909e35f4f06fb544f5c4f2e8eac093bf99103c2dab42fdde5de400ae0bc14`. Safety was added around the published migration rather than rewriting history. | **Verified by current hashes; no revision was deleted or renamed.** |
| Additive workspace-pause evidence | New revision 182 expands the workspace-event action constraint to the explicit `migrated` value, adds an append-only `workspace_pause_migration_audits` table, derives compatibility evidence from retained role/workspace events, and preserves later human actions. Existing source events are not rewritten. A compatibility event/version advance is recorded only when the migration evidence must become the latest shared-control fact. | **Fixed locally; focused SQLite and retained-PostgreSQL proofs passed.** |
| Related-role history retention | New irreversible revision 183 replaces the two destructive PostgreSQL `CASCADE` relationships with validated `RESTRICT` constraints while the old constraints remain active, and adds equivalent SQLite delete guards. The runtime rejects deleting an original role that owns related roles, soft-archives a related role with evaluation history, and permits a true hard delete only for an empty role. | **Fixed locally; migration and lifecycle-focused tests passed.** |
| Migration execution safety | The supported wrapper and Alembic online path now fail closed for dialects other than PostgreSQL/SQLite, reuse the advisory-locked connection, bound PostgreSQL lock waits, fence organization then role writers before crossing immutable 175, and validate the exact `migrated` action, append-only audit trigger, and named `RESTRICT` foreign keys. Revision 182 takes the organization-first fence before event-table DDL to match runtime lock order; revision 183 uses the role-to-evaluation order. | **18 focused migration checks and 35 adjacent runtime/lifecycle checks passed; current full suite remains pending.** |
| Retained PostgreSQL proof | Retained PostgreSQL 16.14 database `tali_audit_adv_fresh_183_f88c_20260716` reached sole head 183 from fresh; `tali_audit_adv_compat_181_f88c_20260716` upgraded 181→183 with exact role-event evidence retained, organization control version 7→8, one `migrated` event/audit row, and both foreign keys set to `RESTRICT`; `tali_audit_adv_lock_181_f88c_20260716` proved a forced organization-row lock fails within the bound at 181 without creating the audit table, then retries successfully to 183 with version 11→12. Raw `alembic check` reported no new operations. | **Targeted PostgreSQL proof passed. Databases and the retained PostgreSQL container were not dropped, deleted, or stopped.** |
| Workspace bulk controls | PR #1047's controls are treated as bulk role mutations, not a new workspace execution overlay: Pause changes currently running enabled roles while preserving unrelated existing holds; Resume attempts eligible paused roles and reports skipped roles that need budget/readiness attention. An all-skipped Resume leaves the concurrency version and latest actor unchanged so it can be retried truthfully. Home and Jobs use the explicit labels “Pause running agents” and “Resume eligible paused agents,” while an old database that still has a legacy overlay retains a visible owner-only recovery Resume even when both new bulk counts are zero. | **Implemented locally with focused route/hook/header coverage; complete current backend/frontend runs pending.** |
| Related-role mutation authority | Shared application outcome and ATS-stage mutations may act through a related role only when the caller has `EDIT_ROLE`, the application belongs to that related role's live projected roster, and the canonical application remains open. Ordinary calls retain the original-role permission path. The capability endpoint now uses the same edit permission, and action metadata records `acting_role_id`. | **Implemented fail-closed with positive and negative authorization regressions; final focused/full re-run pending.** |
| Related-role scoring fences | Every related-role scoring attempt re-reads live roster membership, role authority, global application state, CV/spec fingerprints, and attempt ownership before cache/provider work and again before persistence. Provider usage is reserved against the related role immediately before paid I/O. Revoked authority becomes a retryable hold, changed inputs retry quickly, removed/closed roster rows become excluded, and a replaced attempt cannot overwrite the winner. | **Implemented with adversarial tests in the worktree; full continuation verification pending.** |
| Focused manual runs | A recruiter-focused manual run is scoped to exactly one live application from the role's authorized roster. The orchestrator revalidates that scope at each provider/tool control fence; cohort-capable event cycles retain their intended broader behavior. | **Implemented with scope and mid-cycle revocation coverage; full continuation verification pending.** |
| Frontend authorization and role truth | Role mutations now fail closed while permission status is missing or errored, and unauthorized controls remain visible but disabled with a reason. Related-role job-spec, assessment, and candidate-action settings are source-owned/read-only; related-role scoring criteria, threshold, feedback, and budget remain available so the score-only role is not weakened. The related view fetches original-role task context for truthful read-only assessment display. | **Implemented with focused component regressions; clean Node 22 full run pending.** |
| Assessment retakes | The existing retake dialog is now part of the live candidate flow. A candidate without an assessment uses create; a candidate with an active assessment opens the dialog and uses the retake endpoint with the chosen task/reason. A failed retake leaves the dialog open and no related role can send or retake an assessment. | **Implemented with hook/dialog regressions; clean Node 22 full run pending.** |
| Frontend correctness and bounded rendering | Successful bulk mutations are no longer reported as failed merely because the follow-up status fetch failed; partial Resume results show a warning. The Agent header prefers the current run over stale last activity and no longer invents workspace-pause attribution for independent role holds. Pipeline distribution renders at most 100 dots while keeping exact scored and above/below counts in text, avoiding thousands of DOM nodes without reducing information. | **Implemented with focused tests; current in-app browser and full frontend gates pending.** |

The continuation did not deploy, modify production configuration, run paid
provider smoke, or perform destructive database cleanup. The exact current
full-suite, browser, remote-CI, and review state is recorded again in the
release section so earlier green evidence cannot be mistaken for a final
continuation result.

## 2026-07-19 latest-main continuation

This second continuation was reviewed against `origin/main` at `9580f1aa`.
That commit remains an ancestor of the working branch at an exact divergence of
0 behind / 37 ahead before the cleanup commit. A default rebase was attempted
and safely aborted because it would flatten 15 published merge commits and
replay 22 commits despite the branch already containing current `main`; exact
ancestry is the consistency proof used here. The retained local PostgreSQL
16.14 container, its proof databases, and the local preview process remain
intact.
No provider, deploy, Railway, Vercel, production database, or paid smoke call
was made.

| Area | Additive remediation and value | Current evidence/status |
|---|---|---|
| Workable synchronization | Exact provider pages are captured before mutation; incomplete/error pages cannot authorize deletion; every claimed row is fenced by run/lease ownership; durable scheduling, retry, and stale-run recovery avoid lost or duplicate work. CV downloads stream to an exact 5 MiB ceiling, reject invalid ceilings before any provider/rate-limit side effect, and do not forward bearer credentials across redirects. The mounted inbound webhook returns an explicit non-success instead of acknowledging and dropping unsupported events; scheduled pull remains the functional path. | **Focused Workable, privacy, download-boundary, and adjacent ATS contracts passed; no live credential call was made.** |
| Bullhorn synchronization | Bootstrap, incremental/full sync, event checkpoints, reconciliation, soft tombstoning, stale-run finalization, Redis/row ownership, and sealed-bootstrap recovery now fail closed at ambiguity. Full-sync absence is trusted only after an error-free complete walk. Files stream to the same 5 MiB ceiling; a deterministic oversized CV now imports candidate metadata without that attachment instead of retrying the entire candidate sync forever, while transient download failures still fail/retry normally. | **The complete Bullhorn suite passed 310/310 after the final attachment-boundary change; no live credential call was made.** |
| LLM/Voyage pricing and admission | Exact current model pricing replaces broad defaults; prompt-cache 5-minute/1-hour multipliers, batch rates, Agent SDK usage, streaming, Voyage embeddings, and role/org caps share the metering contract. Unsupported streaming shapes fail before provider I/O. The retired Opus 3 fixture generator now uses supported Opus 4.8 without an invalid non-default temperature. | **The focused pricing/admission suites and final immutable reservation-attribution refinement are complete and green locally. The full current backend suite passed after the final health/size-gate fixes.** |
| Paid-request identity | Durable v2 holds bind explicit presence/absence for organization, feature, role, user, entity/candidate, provider, model, and a canonical request digest. Historical v1 holds remain settleable for recovery but cannot authorize a new provider call. Candidate parse/rerank/grounding, synchronous/async Anthropic, Voyage, Agent SDK, streaming, and batch surfaces use the governed admission path. Ambiguous transport outcomes retain funded evidence; deterministic non-billable rejects release it; batch creation is never blindly replayed after an ambiguous response. | **More than 1,170 focused metering/cost/provider tests plus authority and architecture suites passed, followed by the named cancellation/retry-evidence refinement checks; no provider call was made.** |
| Provider error privacy | Structured LLM, public intake, CV parsing, pre-screen, scoring, Graphiti, E2B, Workable, Fireflies, Resend, GitHub, and Anthropic/Voyage metering paths use secret-safe codes or controlled domain messages. Across the source-guarded provider boundaries reviewed in this audit, raw SDK/provider body and traceback text is excluded from public responses, durable error fields, readiness caches, health payloads, and logs; diagnostics retain stable codes and allowlisted context instead. This is deliberately scoped to those reviewed boundaries rather than presented as a universal claim about every future integration. | **A 560-test consolidated privacy/runtime suite, 91 focused assessment/graph/Workable privacy tests, and the vendor GraphRAG/provider suites passed.** |
| Retry, startup, graph, and GitHub-health privacy | Caught Celery failures are translated to stable retry/result codes instead of passing raw exception objects or traceback text through the result backend. Railway web/worker startup diagnostics retain safe failure categories and connection context without raw exception messages. Graph provider outages remain distinguishable from true empty results without preserving a secret-bearing exception context. GitHub credential health returns and logs stable auth/HTTP/network categories rather than provider bodies or request exceptions. | **Implemented locally with focused retry, startup, graph, and GitHub-health privacy regressions.** |
| Central Sentry boundary | Web and worker initialization now share one allowlist-reconstructed, fail-closed event/transaction/breadcrumb boundary with explicit FastAPI/Starlette/SQLAlchemy/Celery integrations. Request bodies, headers, query strings, literal URLs, locals, SQL text, task arguments/results, exception values/chains, attachments, and arbitrary extras are excluded; task/route grouping remains useful through stable opaque identities. `sentry-sdk[fastapi]` is upgraded from 2.14.0 to 2.66.0, closing the FastAPI 0.137+ repeated sync-handler wrapping defect fixed upstream in 2.63.0. The obsolete direct Jinja workaround and multipart-warning suppression are removed. Web capture includes the explicit safe method set, including HEAD/OPTIONS; generative-AI span streaming and Celery trace propagation are disabled to bound cost and prevent inbound baggage reaching broker headers. | **143 affected tests passed with warnings treated as errors. A clean hashed runtime install started with a valid DSN, Sentry 2.66.0, Jinja absent, and MarkupSafe present; eight requests retained one sync-handler wrapper at depth one. Exact lock integrity, `pip check`, `pip-audit`, Ruff, compile, size, diff, and published-path preservation gates passed.** |
| Webhook boundaries | Workable, Fireflies, Resend, and Stripe signed routes stream at most 1 MiB before signature, organization, inbox, or provider work. Fireflies accepts documented V2 `sha256=` signatures and `meeting.transcribed`/`meeting_id` payloads while preserving V1 and the durable inbox. The canonical organization-scoped route is O(1). | **131 webhook/security/MCP/retry boundary tests passed. The legacy unscoped Fireflies route remains O(configured organizations) for a well-shaped invalid signature until provider configuration is migrated.** |
| Candidate search and sandbox compatibility | Empty/oversized model responses fail safely instead of becoming misleading results, exact paid holds cover parser/rerank/grounding, and obsolete E2B constructor/connect TypeError fallbacks were removed instead of silently retrying incompatible SDK shapes. Workspace names are collision-safe; legacy normalized repository paths are revalidated before materialization, so `..`, `.git`, absolute, and escaping paths cannot write outside the sandbox workspace. | **Focused parser/search/metering and candidate/E2B suites passed.** |
| Repository, calibrator, and file boundaries | Task repository paths, mock roots, and generated repository names reject traversal/symlink escapes and use digests only when lossy normalization would collide; ordinary historical names remain unchanged. Unicode escape normalization preserves non-ASCII content. Local calibrator objects have collision-safe identities with compatible legacy reads, exact size ceilings, and finite/monotonic semantic validation before remote data can replace a working cache. S3, Workable, and Bullhorn reads are streamed and bounded rather than buffered without limit. | **123 final cross-cut tests plus focused calibrator/task/E2B/download suites passed; direct symlink targets and legacy data were not deleted.** |
| Background-job progress | Score progress uses one conditional aggregate instead of three count queries and reuses the batch's cached role name instead of querying every four-second poll. Retained terminal progress reads only the requested scoped key instead of iterating the whole store. Dismissed terminal score runs stay dismissed across discovery polls, while a distinct new run identity reappears; same-mounted organization changes restart discovery and cancel the old scope's loops. | **13 backend progress-query/retention tests and 4 focused frontend race/scope tests passed.** |
| Migration head 195 | Revisions 190–195 are additive continuations on the sole migration head. Revision 195 hardens compatibility invariants while preserving published migrations; migration batching avoids oversize parameter sets. | **Retained PostgreSQL 16.14: fresh `000→195`, rerun at head, `189→195`, populated `181→195`, split `173+174→195`, sole-head/current/invariant checks, and zero-op `alembic check` passed. The proof database and container remain intact.** |
| ATS note delivery and rolling compatibility | Canonical note dispatch now carries exact provider/runtime identity, live authority checks, durable idempotency keys, provider-specific mutex namespaces, bounded retry attempts, and old-producer/new-worker enrichment for the exact legacy note payload. Pre-provider failures use compare-and-set terminalization, duplicate broker bodies cannot overwrite terminal rows, and Beat recovery skips unrecoverable old broker-only notes instead of failing them as corrupt. | **94 focused ATS note/resilience/mutex tests and 109 adjacent chat/invite/decision/stage tests passed; full backend suite passed after the final two gate fixes. No provider call was made.** |
| Related-role suppression SQL safety | SQL de-duplication now mirrors tolerant Python staleness semantics: malformed or overflow JSON score/id snapshots are treated as absent/non-suppressing instead of aborting the role-wide query. | **9 focused suppression tests passed; retained PostgreSQL proof showed `unknown`, `1e1000000`, and oversized integers return `NULL` while valid bounded values cast normally.** |
| Dormant task-selection prototype | The A2 selector had never been consumed by production and could bypass the current role-linked task/experiment/HITL/repository/delivery authority if registered blindly. It remains importable, directly testable, manually runnable, and fully retained, but is truthfully marked `experimental_unwired` and absent from the four-agent production registry. Its weekly full-template calibration scan was unscheduled because no production reader consumed the writes; the Celery task/module/table remain available. | **213 focused sub-agent/runtime/schedule tests passed. No capability or data was deleted.** |
| Pre-evaluation latency | False “parallel/independent” claims were removed. The four production signals remain serial because they share a non-thread-safe SQLAlchemy session and cold paths can write inside the caller transaction. The exact immutable-snapshot, separate-session, admission, write-set, ordering, stale-input, cancellation, and settlement prerequisites for safe parallelism are documented. | **Stable order/same-session regression passed. Unsafe speculative threading was not introduced.** |
| Frontend runtime and loading | Bullhorn polling pauses while the document is hidden and resumes when visible; terminal score notifications do not resurrect after dismissal; organization-scope changes cancel/restart the correct discovery loops. Public footer headings follow semantic order; supported routes remain code-split and within bundle budgets. No feature or result detail was removed to make the build smaller. | **Node 22 architecture/motion/chat/UI/ESLint/TypeScript gates passed; 198 files and 1,482 tests passed single-worker; the 3,441-module production build, bundle budget, desktop/protected/404 smoke, and 390×844 mobile local browser smoke passed with no console errors, broken images, or horizontal overflow.** |
| Supply chain and test quality | The exact Python environment passes integrity and vulnerability audit; the frontend dependency graph reports zero vulnerabilities. AST/title/body scans found no shadowed, duplicate, vacuous, or self-equality tests, and collection is clean. New Graphiti tenant/input/context-cleanup and hidden-tab polling regressions cover previously untested boundaries. | **Repo-wide Ruff, compile, file-size, diff whitespace, frontend static gates, and full backend suite passed: 8,359 passed, 54 skipped, 19 deselected. Coverage remains a separate measured gate to refresh before final release approval.** |
| Evidence-confirmed bloat cleanup | Static entrypoint/import reachability found 19 frontend modules unreachable from both production and tests (4,062 LOC / 165,294 bytes). Their exact owned selectors removed another 36,250 source bytes, including 11,858 bytes from globally loaded CSS. The obsolete internal Bullhorn dispatch module, dead Workable inline scorer, confirmed private helpers, unused settings, an unused assessment parameter, and a duplicate diagnostic implementation were retired. Unused Xterm packages and `python-docx`/`lxml` were removed from exact locks; `httpx2` was retained because Starlette imports it dynamically. | **Frontend static gates, 177 focused tests, the 198-file/1,482-test full suite, production build, bundle budget, CSS parsing, clean dependency tree, two clean Python lock installs, 499 focused backend tests, Ruff, compile, lock, file-size, dead-module, and diff checks passed. The complete backend run also passed: 8,359 passed, 54 skipped, 19 live-smoke tests deselected. Refreshed PR CI remains required.** |

The table records local implementation evidence, not release approval. The
coverage, structural-graph delta, remote draft-PR checks, review, staging
provider smoke, and controlled deployment gates remain authoritative.

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
| New decision-explanation summaries bypassed the existing legacy-reasoning humanizer. | Recruiters could see internal application IDs or raw `workable_stage`/`pipeline_stage`/scorer keys on older queued decisions; an over-broad cleanup could also erase a legitimate certification/project year such as `(2024)`. | The complete humanizer now lives in a shared service, the old domain import remains as a compatibility facade, and decision explanations reuse it before presentation. Non-year internal IDs are removed while plausible years, thresholds, scores, quoted multi-word stages, and clean prose are preserved and regression-tested. | **Fixed locally; the current merged-PR #1041 year-preservation P2 is addressed, but its GitHub thread remains unresolved.** |
| Pre-screen false rejects were structurally difficult to measure. | Cost savings could hide the most harmful scoring error. | A bounded shadow sampler full-scores actual filtered candidates, and both divergence reporting and gate calibration consume survivor plus shadow-reject pairs. Live enforcement stays explicit. | **Fixed locally; operational enablement and adequate sample volume remain external.** |
| Copy/paste overlap could hard-cap by default and tokenization skipped non-Latin text. | False-positive or language-dependent harm. | Detection is always recorded but defaults to a neutral recruiter flag; the legacy cap is opt-in through `FRAUD_COPY_PASTE_ACTION=cap`. Tokenization is Unicode letter/number aware. | **Fixed locally.** |
| Full-score engines applied different integrity handling and prompt-cache layouts. | Inconsistent score behavior and unnecessary model input cost. | Both engines apply the bounded timeline/unverified-claim layer. Both holistic calls use the same one-hour ephemeral cache layout for stable role context. | **Fixed locally and focused-request-tested.** |
| A new score request could trigger redundant standing work. | Repeated provider cost and queue pressure. | Event-driven enqueue, activation bootstrap, a bounded five-minute backlog sweep, duplicate-job guards, credit/budget gates, and a 50-item per-tick auto-score cap drain work steadily without buying extra agent reasoning cycles. | **Fixed locally.** |

### Requisition chat, uploads, and related roles

| Finding | Impact | Local remediation | Status |
|---|---|---|---|
| Ordinary requisition turns escalated to the primary/Sonnet model on every second turn. | Higher latency and model spend without a corresponding quality need. | Normal turns remain on the configured chat/Haiku path; escalation is limited to related-role, current-spec, or document-sensitive intent. | **Fixed locally; routing regression expects Haiku, Haiku, then primary model only for the sensitive turn.** |
| A newly uploaded source could appear in both user and system content. | Duplicate input tokens and ambiguous prompt ownership. | The originating turn carries the source once in user content; later turns hydrate the durable source once in system context. | **Fixed locally and occurrence-tested.** |
| Corrupt or unreadable attachment-only turns still constructed a provider request. | Paid no-op calls and inconsistent failure behavior. | They now return the same deterministic safe transcript/reply without constructing or invoking a provider; usable text or a readable attachment still calls normally. | **Fixed locally and tested.** |
| Recruiter and public intake uploads relied on incomplete client checks. | MIME spoofing, oversized reads, inconsistent errors, and wasted extraction/provider work. | Both server routes share an exact extension allowlist, maximum six files and 15 MiB each, MIME/extension coherence, bounded reads, and JPEG/PNG/GIF/WebP signature checks. Both UIs share the same picker/drop policy and surface safe 413/415/422 messages. | **Fixed locally and route/UI-tested.** |
| Other upload paths read bodies before enforcing bounds. | Avoidable memory, parser, storage, and database work. | Document uploads read at most 5 MiB plus one byte before storage/extraction; prospect CSV reads at most 10 MiB plus one byte before decoding/parsing and retains the 500-row cap. | **Fixed locally; repository scan found only bounded direct reads.** |
| Client-intake list/detail responses exposed more state than each consumer needed. | Larger payloads and leakage of internal hydration/source metadata. | The paged six-key list summary omits agent state; detail retains needed behavior while redacting internal source keys. Load-more preserves complete access. | **Fixed locally and privacy-tested.** |
| Candidate upload UI advertised legacy `.doc` while the actual parser supported PDF/DOCX. | Users could select a file that could never work. | Picker and drag/drop now agree on PDF/DOCX and reject unsupported input with a visible error. | **Fixed locally; compatibility component and test retained.** |
| Requisition chat/page growth was being handled inside oversized modules. | Higher change risk and repeated merge conflicts. | Attachment, grounding, capture-support, source, and upload responsibilities were extracted behind import-compatible re-exports. At the recorded checkpoint, service was 500 lines, capture 472, prompt 298, route 474, attachment service 300, capture support 209, and the page 1,185 against its 1,201 cap. | **Improved locally; historical size/architecture evidence passed. Final current LOC is pending the stable-tree metrics refresh.** |
| Related-role drafts could show a blank/stale header and cramped relationship card. | Sidebar and main panel could disagree or hide context. | PR #1040's title/status fallback and responsive header behavior were preserved in the modular architecture; grounded chat, related-role hydration, intent-aware specification updates, Jobs catalogue, and release safeguards from PRs #1027–#1040 were reconciled before the later PR #1034 decision-presentation, PR #1042 scoring-state, and PRs #1041/#1044 decision-copy integrations. | **Fixed locally; focused and full frontend suites passed at the earlier checkpoint; continuation rerun pending.** |
| Related-role scoring treated waiting/retrying as a thin running-state variant and refreshed only after a visible running transition. | The roster and score totals could remain stale when polling observed waiting/retrying directly before completion, while the action label invited duplicate work. | Waiting/retrying are first-class polled states with reasoned notices, accurate scoreable totals and disabled progress actions. The roster refreshes when any active state becomes terminal, including a skipped `waiting→completed` transition, while preserving the governed Process Candidates header extraction. An undefined upstream UI token was also replaced with the established semantic muted token. | **Fixed locally; PR #1042 regression, token, architecture, and full-suite gates passed at the earlier checkpoint; continuation rerun pending.** |

### Authentication, authorization, security, and error disclosure

| Finding | Impact | Local remediation | Status |
|---|---|---|---|
| New unverified users could log in while UI/product copy implied verification. | Account-control inconsistency. | Login now requires verification. Migration 172 safely grandfathers already-active owners so rollout does not lock out existing workspaces. Verification-token replay and password-reset invalidation are tested. | **Fixed locally; migration required.** |
| A late profile-bootstrap response could restore a logged-out user or clear a newer login after token/session rotation. | Cross-session state could reappear, a valid newer login could disappear, or a half-login token could remain cached. | Authentication requests now carry a generation and token identity. Logout/new login invalidates older success and failure handlers, failed profile bootstrap rolls back private state, and same-session sliding-token rotation remains valid. | **Fixed locally; 4 race regressions passed.** |
| Password guidance treated bcrypt's limit like a character count. | A multibyte password could cross bcrypt's boundary despite appearing shorter than 72 characters. | Backend validation and frontend copy now state and enforce the 72 **UTF-8 byte** limit; the unused Passlib dependency was removed. | **Fixed locally and boundary-tested.** |
| Python virtual environments retained an untracked bundled `setuptools` 65.5.0. | GitHub's installed-environment audit reported six findings across four advisories, and the production virtual environment could inherit the same bootstrap-package drift outside the runtime lock. | `setuptools==83.0.0` is now a direct production input and therefore appears in both exact hashed locks. A lock contract prevents silently dropping the replacement; no audit exception or mutable upgrade command was added. | **Fixed locally; fresh Python 3.11.9 dev install and a production-shaped 65.5.0→83.0.0 runtime replacement both passed hash install, integrity, import, and vulnerability audit.** |
| API key administration and organization security/integration settings were not uniformly owner-only. | Workspace members could control machine credentials or access policy. | API-key create/list/revoke and organization mutation now require an organization owner. | **Fixed locally.** |
| Workable OAuth callback state was not cryptographically bound to the initiating user/workspace. | Login CSRF or cross-workspace connection risk. | Short-lived signed state includes user, organization, audience, and nonce; callback verification rejects invalid or expired state. Frontend forwards the state value. | **Fixed locally.** |
| Workable base/pagination URLs could change origin. | SSRF/credential-forwarding risk. | Callback and pagination URL validation now enforce safe schemes and the approved origin; tests reject unsafe origin changes. | **Fixed locally.** |
| Integration secrets shared generic encryption assumptions and Fireflies webhook secret could remain plaintext. | Secret exposure and difficult key rotation. | New and re-saved secrets use dedicated integration-secret encryption with current/previous-key reads; Fireflies API/webhook writes are encrypted. Readers retain an unversioned/plaintext compatibility path for existing rows. | **New writes fixed locally; inventory, re-encryption/rotation of legacy rows, and production key provisioning remain release work.** |
| Provider/sandbox/graph/task exceptions were serialized into candidate, recruiter, job, or debug responses. | Sensitive tokens, paths, provider bodies, or internals could leak. | Stable public error codes replace raw exceptions. Diagnostics at the reviewed boundaries keep sanitized categories, stable codes, and allowlisted context rather than raw exception messages, provider bodies, or tracebacks. Assessment result/timeline/git evidence, Workable sync, graph debug, reconciliation, and task-exhaustion paths are redacted. | **Fixed locally; targeted redaction tests passed.** |
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
| `portfolio_agent` | **Reserved, unavailable capability name**; cohort features are not implemented. Its historical package is import-compatible but raises an explicit unavailable error. | The canonical registry entry remains `available=False`; production flag evaluation and compatibility calls both fail closed. |
| `capability_auditor` | **Reserved, unavailable capability name**; it produces no findings. Its historical package is import-compatible but never returns a fake empty audit. | The canonical registry entry remains `available=False`; fail closed. |
| `causal_mode` | **Reserved, unavailable capability name**; causal inference/claim validation is absent. Its historical package is import-compatible but never emits placeholder claims. | The canonical registry entry remains `available=False`; fail closed. |
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
- bundle budgets for raw and gzip JS/CSS plus the actual `main-*` application
  entry, while retaining `index-*` compatibility for older builds;
- shared recruiter/public requisition attachment validation and safe upload
  errors, plus consistent PDF/DOCX candidate picker and drop behavior;
- related-role title/status fallback and a responsive relationship header that
  preserves the extracted page architecture;
- related-role waiting/retrying notices, terminal roster refresh, truthful
  progress actions, and an additive legacy job-pipeline utility facade; the
  recorded checkpoint measured the modular page at 2,609 lines against its
  then-current 2,620-line ratchet;
- concise plain-English decision reasons and compact candidate summaries on
  cards, with full report density retained. They preserve true factor totals,
  display the fired rule's actual comparison operator, keep policy/context-only
  rationale when the recommendation slab is absent, avoid fabricated
  `Confidence 0%` and `0 < 0` chips, and expose unknown factors as accessible
  “unverified” state rather than falsely marking them missing;
- a candidate-summary fallback extraction that preserves behavior; the recorded
  checkpoint measured it at 24 lines and `CandidateStandingReportPage` at its
  then-current 2,262-line architecture ratchet;
- scoped semantic graph/design tokens rather than hard-coded canvas colors;
- assessment timer and hook-order corrections;
- legal/privacy pages, developer API tests, marketing/showcase route tests, and
  dead-link prevention on public candidate snapshots.

At the earlier `b19e087d` checkpoint, the machine UI guard reported zero
unresolved token or component-policy violations and the frontend architecture
and motion-system gates passed. That checkpoint's 156-file/1,102-test gated run
was warning-free: React scheduling warnings fell
from 58 to zero, Router future-flag warnings from 30 to zero, and Motion
diagnostics from two to zero. A fail-closed setup guard now blocks and reports
unexpected API XHR/fetch calls even when application error handling catches the
rejection. No console suppression was added, and CI rejects these warning and
network-leak classes so the clean signal cannot silently regress. The larger
current 198-file/1,480-test result is recorded separately in the latest-main
continuation and release sections.

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
| npm 10-compatible exact install | The lock now records DOMPurify's optional Trusted Types peer, keeps the security override unambiguous at 3.4.12, and uses jsdom 28.1's Node 22 undici bridge. This removes failed preview/install and test-retry work without changing shipped capability. |
| Fail-closed test network isolation | Shell and background-job clients use the canonical API facade; unexpected test XHR/fetch is rejected before dispatch and fails the test with its method/URL. This replaces deep-import/mock workarounds and prevents false-green integration tests. |
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
| Large route ownership | At the earlier measured checkpoint, Process logic had moved into a 279-line domain route and dedicated dispatch service; collection/analytics/query responsibilities were also extracted. Exact file-size ratchets prevent re-growth. | **Improved locally; the final current-tree LOC is pending the stable-tree metrics refresh.** |
| Agent tool-registry hotspot | At the earlier measured checkpoint, PR #1041's useful plain-English schema copy had initially grown the ratcheted registry from 2,692 to 2,703 physical lines. The copy moved into a focused module, the historical private names remain import-compatible aliases, all four queue-decision schemas are contract-tested, and that checkpoint measured the registry at 2,689 lines with its exact ceiling lowered to 2,689. | **Fixed locally without changing prompt or evidence behavior; final current-tree LOC is pending the stable-tree metrics refresh.** |
| Architecture enforcement | AST gates detect all supported decorator and imperative route-registration forms, require real admin-guard calls, flatten the assembled FastAPI route table (including lazy included routers), and compare actual authentication/agent-action calls. Exact fail-closed inventories cover public/token ingress and one intentional generated-user-route collision; comments, strings, filename changes, mounts, and include prefixes cannot bypass the checks. | **Fixed locally; the earlier checkpoint's 19 architecture-gate tests passed, while the exact final current count is pending the stable-tree metrics refresh.** |
| Remaining file bloat | The earlier checkpoint counted 43 backend files on exact legacy baselines. The gate enforces 500 physical lines for route/service modules and 1,000 for every other `app` module; it rejects growth above every exact baseline, so moving or renaming an oversized file cannot evade policy. This is maintainability debt, not an excuse for a risky blind rewrite in an already large patch. | **Non-release refactor debt; ratchet and bypass regressions passed historically. Final current counts are pending the stable-tree metrics refresh.** |
| Worker placement | Paid/long-running scoring, processing, delivery, recovery, and reconciliation are off request/web-process lifetime. | **Fixed locally.** |
| Database connection | Runtime web/workers use only Railway's private `DATABASE_URL`; `DATABASE_PUBLIC_URL` is deploy-tool-only for migrations outside Railway. | **Fixed locally; 25 database/deployment contract tests passed earlier in this audit.** |
| Fresh database and PostgreSQL semantics | A canonical `000_initial_schema` reconstructs the pre-Alembic base so a genuinely empty PostgreSQL database can traverse the full chain. The supported wrapper rejects unversioned partial schemas and unsupported dialects, takes a bounded advisory lock, reuses that connection through Alembic, fences immutable 175, bounds DDL/data lock waits, and validates exact schema invariants. Migration 176 historically restored application timestamp defaults; 177 persists chat-turn role versions; 178 adds CV-score dispatch approval; 179 restores required user-boolean nullability plus role-intent self-reference metadata; 180/181 merge the integrated heads without data changes; 182 adds non-destructive workspace-pause compatibility evidence; and 183 prevents related-role history cascades. Later additive revisions extend the sole chain to current head 189. Runtime contracts also exercise real JSONB/JSON-array search, event idempotency-key uniqueness, immutable audit updates, advisory-lock serialization/release, and disjoint `FOR UPDATE SKIP LOCKED` claims. | **Historical `000→179` and continuation-through-183 proofs remain recorded; the current retained PostgreSQL 16.14 proof passed fresh `000→189`, sole-head/current/invariant checks, 39 runtime contracts, and zero-operation autogenerate parity. A production-shaped snapshot rehearsal remains pending.** |
| Test isolation | Backend tests select shared in-memory SQLite before app import, avoid disk `test.db`, and have a dedicated real-Postgres CI contract. The only `sqlite:///./test.db` occurrences now assert production rejection behavior. | **Fixed locally.** |

The backend size gate now covers every Python module under `app`: route/service
modules have a strict 500-line ceiling and all other modules have a 1,000-line
ceiling. At the earlier checkpoint, 43 oversized legacy files had exact
ratcheted baselines; the final current count is pending the stable-tree metrics
refresh. Baselines were lowered when files shrank, including the process-dispatch extraction;
renaming or moving a hotspot cannot create a blanket exemption. Synthetic
regressions prove that an oversized renamed module and an imperative
`add_api_route` route cannot evade the gate.

The online canonical migration path is the supported contract. At the earlier
checkpoint, retained PostgreSQL 16.14 databases completed fresh `000→179`,
existing `178→179`, and `179→178→179`; an orphan preflight exited before writes,
left the database at 178, and preserved row counts. The continuation then proved
fresh `000→183`, exact-evidence `181→183`, and a bounded blocked-lock failure
that left revision/data unchanged before a successful retry to 183. Raw
`alembic check` remains zero-operation. Migration 176's historical incremental
`175→176` offline SQL result also remains valid, but this report does not claim
that the entire historical chain supports Alembic's offline/mock connection
mode because legacy migration 015 assumes a live connection. The complete
current retained-PostgreSQL contract is green through head 189; a recent
production-shaped snapshot rehearsal remains a release gate.

## Cost optimization assessment

No optimization below reduces model quality, recruiter evidence, assessment
depth, or recovery guarantees.

| Cost source | Remediation | Why output is preserved or improved |
|---|---|---|
| Repeated failed pre-screen calls | Deterministic/unknown errors retain the six-hour guard; an explicit 429/5xx/timeout/network failure receives one 30-minute retry, then returns to the long guard. A fresh CV resets the streak and overrides backoff. Python and SQL selectors share the same marker set. | Stops the documented 7,668-repeat pattern, lets one likely transient outage self-heal sooner, and immediately honors new candidate evidence without reopening an unbounded retry storm. |
| Duplicate SDK retries | Anthropic SDK retries are disabled on every reviewed app and vendored GraphRAG constructor. The governed wrapper permits at most two wire attempts only for classified transport timeout/connection, 408/409/429, or 5xx outcomes; validation, authentication, credits, local failures, partial streams, ambiguous batch creation, and post-success metering failures are not replayed. | Keeps the same successful result while preventing hidden or post-success calls from multiplying LLM spend. Each real retry gets fresh durable admission and attempt evidence. |
| Huge auto-score bursts | 50 eligible applications per role/tick plus event-driven intake and bounded backlog sweep. | Same backlog is drained steadily with less queue pressure and no candidate loss. |
| Paid no-op agent cycles | Survey-based early exit when no candidates, questions, or intent gaps are actionable. | Skips only a cycle whose correct result is “nothing to do.” |
| Duplicate provider delivery | Durable receipts, leases, stable idempotency keys, exact-payload recovery, and ambiguous-terminal state. | Prevents paying twice while making uncertain outcomes visible instead of guessing. |
| Repeated dormant fitted-policy searches | A versioned deterministic fingerprint covers ordered training inputs/configuration. Equivalent current candidates are reused before grid or agentic search; per-organization serialization and bounded pending output prevent duplicate fitting work. | The same fitted result is retained for identical evidence, while changed evidence/configuration still produces a fresh candidate. Reuse does not activate or auto-promote the dormant model. |
| Holistic input tokens | Stable role context is cached on both Sonnet calls. | Dynamic candidate evidence remains uncached and current; the complete recruiter report is retained. |
| Requisition model routing | Ordinary chat remains on the configured chat/Haiku model; only current-role/spec/document-sensitive intent escalates to the primary model. | Keeps normal conversational quality and preserves stronger reasoning where evidence-sensitive updates require it, without paying primary-model cost every second turn. |
| Requisition source tokens and corrupt uploads | A newly uploaded source is included once, then hydrated once as durable context on later turns; unreadable attachment-only turns return deterministically before provider construction. | Preserves all usable evidence while removing duplicated tokens and paid no-op calls. |
| Retired Haiku aliases | Fallback resolution tries the configured current model before historical aliases. | Avoids a guaranteed retired-model failure and retry while retaining compatibility fallbacks and the same successful model result. |
| CV parsing | Anthropic Message Batches remain used for latency-tolerant parsing. | Keeps discounted asynchronous execution where interactivity is unnecessary; interactive recruiter scoring uses recoverable per-app fan-out. |
| Analytics/list payloads | SQL aggregation, projections, bounded pages, and `load_only` that excludes `cv_text` from adverse-impact aggregation. | Returns the same aggregate/list meaning without hydrating heavy bodies. |
| Browser polling/download | Visibility-aware polling, scoped O(1) terminal-progress lookup, one aggregate score-progress query, cached role names, bounded per-tab caches, route lazy loading, vendor chunking, and bundle budgets. | UI remains current and all features remain available on demand while hidden tabs, completed jobs, and four-second polls perform materially less network/store/database work. |
| Git-backed assessment test setup | The mock branch allocator enumerates once and uses session-scoped temporary roots. | Preserves production Git behavior while reducing a representative success-path test from about 34.35 seconds to 0.23 seconds and still covering 501 occupied branch names. |
| Runtime dependency surface | Test-only packages moved to development requirements and unused Passlib was removed. | Production images and audits process fewer packages without removing runtime capability. |
| Cost observability | Retry/validation telemetry, usage events, call logs, and cost-per-outcome tooling remain intact. | Efficiency can be measured rather than inferred; audit artifacts were not removed. |

The holistic recruiter report is intentionally retained for completed holistic
scores, including clear rejects. It is the durable explanation/audit artifact,
not redundant decoration. Removing it would make the product less useful and
less defensible.

## Redundancy, superseded features, and workarounds

### Confirmed obsolete code removed

| Item | Evidence and resolution |
|---|---|
| Nineteen frontend modules | Starting from every Vite HTML entrypoint and following static/dynamic imports found no production or test path to these modules. They were superseded prototypes or abandoned presentation variants, totalling 4,062 LOC / 165,294 bytes. All 16 modules reachable only from tests were retained because some may be unwired product capability rather than dead code. |
| Dead frontend CSS | Only selector families exclusively owned by the 19 removed modules were retired. All 75 CSS files parse, no surviving exact-class consumer exists, and shared Home, candidate, graph-search, motion, and assessment selectors remain. This removes 36,250 raw source bytes; 11,858 bytes had been in globally imported styles. |
| Obsolete backend internals | The unused Bullhorn-only dispatcher/module had been superseded by the provider-neutral ATS lifecycles. The older Workable inline CV scorer had been superseded by the CV score orchestrator. Definition-only private helpers, an unused assessment parameter, and 12 settings with no reader were removed after reference and focused-test checks. Public-name candidates and compatibility roots remain pending external-consumer or rolling-deploy proof. |
| Duplicate diagnostic | `backend/app/scripts/workable_qa_diagnostic.py` is canonical and its indentation defect was fixed. `backend/scripts/workable_qa_diagnostic.py` is now a small compatibility entrypoint rather than a second drifting implementation. |
| Unused dependencies | `@xterm/xterm` and `@xterm/addon-fit` had no importer after the legacy terminal removal. `python-docx` was unused because DOCX parsing uses bounded standard-library ZIP/XML handling; removing it also removes `lxml` from the locked runtime. Starlette's dynamically imported `httpx2` dependency was explicitly retained. |

### Superseded paths preserved safely

| Item | Resolution |
|---|---|
| Historical `intent_parser` sub-agent and its obsolete test | Durable `RoleIntent` is canonical. The old path is a provider-free, unregistered facade with tests proving it cannot become a sixth sub-agent or issue model calls. |
| Duplicate scoring schema surface | Retained only as safe Pydantic payload views with isolated default factories; scoring logic remains canonical in the active service. |
| Four stale static preview HTML pages | Replaced with tiny noindex redirect/fallback documents to the React preview routes; the Jobs fallback preserves only the `agent=paused` and `agent=loading` variants. |
| Query-string/window-location auth bypass for the investor Jobs demo | Removed; `/showcase/jobs` is a dedicated public fixture route. |
| `_RateLimitStoreCompatibility` | Removed; it existed only to accommodate old tests and complicated production behavior. |
| Orphan credit-ledger helper | Retained as an explicit fail-closed facade. It rejects before touching the database and directs callers to reservation/metering or grant flows, avoiding the superseded non-locking generic mutation. |
| Inert capability packages and shared helper | Retained as import-compatible APIs that always raise registry-backed unavailable errors instead of returning placeholder output. The real adverse-impact aggregate service/task remains separate, environment-controlled, and governance-gated. |
| Broken root demo-data seeder | Replaced `scripts/seed_data.py` with a credential-free, no-write compatibility command that fails safely and points to the supported scoped seeders. Current bootstrap/experiment seed paths remain intact. |
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
records specific behavioral proofs plus one uninterrupted backend checkpoint
run and its branch-coverage result. That complete run predates the continuation;
partial continuation runs are not presented as final release evidence.

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
- at the earlier measured checkpoint, the reachability scanner reported 741
  modules, 29 explicit runtime roots, 736 reachable modules, and zero
  candidates. Roots were `app.main`,
  `app.tasks`, `app.models`, 18 exact approved CLI modules, and 8 exact
  compatibility/policy roots; arbitrary
  `__main__` guards and prefix lookalikes cannot self-declare liveness. It
  models parent packages, ignores imports reachable only through
  `TYPE_CHECKING`, `if False`, or a non-approved main-guard body, reports a
  non-empty unreachable `__init__.py`, and ignores an empty one. Fifteen focused
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
  to an image digest. Independently generated development-inclusive and
  runtime-only hash locks are validated against their inputs; CI installs the
  former, while production installs the latter with `--require-hashes
  --no-deps` into `/opt/venv`. Concurrency cancels superseded branch/PR runs, and a
  conservative path classifier skips unaffected backend/frontend jobs while
  unknown paths run both. The current workflow's built-preview smoke contract
  separately enumerates all 18 approved public routes and rejects duplicates,
  internal/authenticated paths, and preview drift.

The exact workflow supply-chain pins are
`actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5`,
`actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065`,
`actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020`, and
`actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02`.
PostgreSQL is
`postgres:16.14@sha256:17e67d7b9890c99b055ba1e0d5c5be4ec27c9d3a72bda32db24a5e5d8a85af0c`.
The 156-pin/3,103-hash development-inclusive lock records input digest
`f6c66b62cbdea489bc98cffd72bc7d75ffad7dc5af7501a406a0e66ce9c70728`.
The 124-pin/2,825-hash production runtime lock records input digest
`f20efcfed83cd9ccbc00da961174716fb9a471c78af0692de7e10638a11ac58a`.
Both verifiers recompute their own inputs before installation; runtime import,
integrity, and vulnerability-audit checks passed. At the earlier checkpoint,
all 34 then-tracked shell scripts passed `bash -n`, both workflow YAML files
parsed, and all 12 third-party action references were pinned to full SHAs. The
final current module/root/reachability, shell-script, and file-size figures are
left pending for the stable-tree metrics refresh rather than inferred here.

### Targeted verification evidence available now

Do not sum these figures; several sets overlap. Unless a row explicitly says
“continuation,” the figure is preserved evidence from the fully exercised
`b19e087d` checkpoint and is not a claim that the post-`f8768124` worktree has
already repeated that gate.

| Focus | Result recorded during remediation |
|---|---|
| Pre-screen/scoring regressions | 131 passed |
| Pre-screen operational durability | 15 passed |
| Pre-screen architecture gates | 18 passed |
| Process-role durability and related backend paths | 47 passed |
| Agent-v2 focused set | 18/18 passed |
| Limiter/apply/EEO focused set | 70/70 passed before canonical client-IP integration; the later canonical-IP integration set passed 65/65 |
| Health/impact/snapshot set | 35 passed |
| Database/release contract set | 59 release/workflow/lock tests passed; combined database/release evidence passed 78 with 4 fixture-only skips |
| Continuation migration focus | 18 passed across immutable-revision handling, additive 182/183 behavior, unsupported-dialect failure, external-connection reuse, exact invariant validation, and migration lock behavior |
| Continuation adjacent runtime/lifecycle focus | 35 passed across workspace bulk controls, role lifecycle/history retention, migration bootstrap, and related runtime contracts |
| Continuation retained PostgreSQL proof | Fresh `000→183`, exact-evidence `181→183`, bounded lock failure with no partial DDL/data, successful retry to 183, both named foreign keys `RESTRICT`, append-only compatibility audit, and zero-op autogenerate parity passed |
| SEO/static-route set | 46 passed |
| Stripe replay/non-grant | 2/2 passed |
| Developer API reference | 1/1 passed |
| Backend file-size ratchet | Passed: route/service modules ≤500 lines, every other `app` module ≤1000, and 43 exact legacy baselines; two bypass regressions passed |
| One-off duplicate implementation scan | Zero AST/hash duplicates at the integration snapshot; not a maintained gate |
| Dead-code reachability graph | 741 modules / 29 explicit roots / 736 reachable / zero candidates; 15 focused scanner regressions and fail-on-candidates gate passed |
| Backend architecture gates | 19/19 passed across route ownership, assembled collisions/authentication, admin-call AST checks, ingress inventories, and agent/action parity |
| Frontend architecture + motion | Passed |
| Frontend UI token/component policy | Passed with zero violations |
| Frontend ESLint + TypeScript contract | Passed |
| Full frontend gated Vitest — earlier checkpoint | Clean Node 22.23.1/npm 10.9.8 install: 156 files / 1,102 tests passed in 33.83 seconds; zero warning, unhandled-error, or unexpected-network diagnostics. Six network-isolation component tests cover feedback ordering/submission/version conflicts and recruiter Q&A success/empty/error states. Nine decision-presentation regressions added through PRs #1041/#1044 and reconciliation cover compact summaries, density, null display values, non-duplicated policy causes, and context-only rationale. CI preserves Vitest failures and independently fails on warning and network-leak classes. |
| Frontend production build + bundle budget — earlier checkpoint | Clean Node 22.23.1/npm 10.9.8 install: 3,410 modules built in 1.82 seconds; 208 files, 5,734,576 bytes raw (5.4689 MiB), 2,652,028 bytes gzip level 9 (2.5292 MiB), and 6,373,376 allocated bytes (6.0781 MiB on the retained temporary worktree filesystem). Raw/gzip bytes: main JS 71,502/19,405, CSS 231,546/39,670, graph 434,159/135,770, charts 412,998/105,280, Job Pipeline 174,018/49,132, Requisitions 45,601/13,038, Client Intake 12,641/4,298, and Candidate Standing Report 80,693/22,992. Bundle budgets and all 14 built-route HTTP smokes passed. |
| Frontend dependency audit | 0 vulnerabilities |
| Complete default non-production backend pytest selection — earlier checkpoint | Locked CPython 3.11.9: 5,937 passed / 8 skipped / 16 live production-smoke tests deselected; zero failures and zero warnings in one uninterrupted 260.61-second checkpoint run after the secure packaging-tool replacement. PostgreSQL behavior is covered separately below. |
| Backend coverage | 75.928303% combined line-and-branch coverage (70,403/92,723 covered units): 56,581/71,325 lines (79.328426%) and 13,822/21,398 branches (64.594822%). The enforced combined floor remains 74%; the ignored originals were left intact and copies were preserved outside the worktree after measurement, with neither committed. |
| Backend dependency integrity/audit | Both exact locks passed integrity/parity. Fresh Python 3.11.9 dev and production-shaped runtime installs replaced bundled `setuptools` with the exact 83.0.0 pin; runtime import and both `pip-audit` checks found zero known vulnerabilities. |
| Static/syntax/diff checks | Full backend `compileall` and Ruff scopes passed; both workflow YAML files, all 34 tracked shell scripts, and 12 action pins passed their checks; tracked and staged diff checks were clean |
| PostgreSQL migration/invariants — earlier checkpoint | Fresh `000→179`, existing `178→179`, and `179→178→179` passed on retained PostgreSQL 16.14; raw autogenerate parity was zero twice, schema invariants passed, and orphan preflight failed before writes with revision/data unchanged |
| Database/release evidence | 78 passed / 4 skipped. Skipped fixtures require creating and dropping databases; equivalent migration/invariant/fail-closed paths were exercised manually on retained databases, with no database or container deleted. |
| CI/lock contracts | Hash-lock validation, exact runtime/action/image pins, concurrency, path-scope, production-target, and warning-gate workflow tests passed |

### Insufficient-test and governance gaps

1. The current stable-tree backend run measured 79.372701% combined
   line-and-branch coverage: 82.750887% line coverage and 68.206181% branch
   coverage. The combined ratchet enforces 74%, up from 35%. Aggregate coverage
   is not sufficient assurance for every payment, authorization,
   provider-failure, worker, and hiring-decision branch; raise it incrementally
   with risk-focused tests without deleting hard branches or marking them `no
   cover` merely to improve the number.
2. The current source/test tree contains 254 `pragma: no cover` annotations.
   The majority sit on broad exception handlers and deliberately unreachable
   fake/provider compatibility methods. Burn down the
   legacy exception exclusions behind fault-injection tests; the measured
   percentages exclude those clauses and must not be read as proof that every
   defensive branch was exercised.
3. The normal backend suite intentionally uses isolated in-memory SQLite, while
   PostgreSQL-only modules require `TEST_POSTGRES_URL`. CI provisions
   PostgreSQL. The current retained-PostgreSQL 16.14 proof is green through sole
   head `195_compatibility_invariant_hardening`, including fresh `000→195`,
   rerun at head, `189→195`, populated `181→195`, and split-head
   `173+174→195` upgrades. A recent production-shaped snapshot
   rehearsal remains a data-upgrade release gate.
4. Current frontend Vitest, ESLint, TypeScript, production build, bundle budget,
   exact 18-route local preview smoke, and focused in-app browser checks are
   green on exact Node 22.23.1/npm 10.9.8. Remote CI/provider preview and the
   deployed-site matrix remain independent gates; local current-tree success is
   not a deployed-site claim.
5. Real Anthropic, E2B, Resend, GitHub, Stripe, Workable, Fireflies, Railway,
   Vercel, and CDN behavior cannot be proven with mocks. Use controlled staging
   or production smoke credentials; never run paid/destructive checks from an
   unreviewed local tree.
6. Assessment/scoring validity is not equivalent to software test coverage.
   The prior production deep dive found very low meaningful-candidate volume;
   calibration, adverse-impact, and predictive-validity claims require governed
   real outcomes.
7. The dead-code gate proves reachability from 32 exact reviewed roots across
   1,040 modules (1,035 reachable and zero candidates), not
   symbol-level usage. Dynamic imports remain the principal blind spot, and a
   zero module-candidate result is not proof that every retained function/class
   is live. Scanner-root or AST-policy changes require review; arbitrary main
   guards, prefixes, type-only imports, dead branches, and non-empty package
   initializers are already covered by focused regressions.
8. The current locked-Python backend suite, strict warnings, coverage, static,
   dependency, migration, frontend, build, bundle, and browser gates are green.
   Remote Ubuntu CI and the refreshed Vercel preview remain independent gates
   for the materially changed worktree because the continuation is not yet
   pushed.

## Outstanding Codex/GitHub review work

The original pre-publication 2026-07-16 snapshot contained 26 open PRs: 11
drafts and 15 non-drafts, with 17 unresolved threads (12 current and 5
outdated). That remains historical audit evidence. A live GitHub refresh after
PRs #1045–#1047 merged now shows **26 open PRs: 12 drafts and 14 non-drafts**.
The open queue has **16 unresolved threads: 11 current and 5 outdated**. Nine
remain actionable on their own open source branches (5 P1 and 4 P2); seven are
fixed or superseded but still unresolved, including the five outdated threads
and both #852 threads.

Merged PRs #1034, #1041, #1042, #1045, and #1047 have seven additional current
P2 threads whose code findings are addressed in this worktree without changing
their GitHub thread state. Across the ten thread-bearing PRs below, the current
snapshot is therefore **23 unresolved threads: 18 current and 5 outdated**.
Review-thread status is not the same as code status: only a reviewer or an
authorized maintainer should resolve or close those GitHub conversations.

| PR | Unresolved threads and current assessment |
|---|---|
| [#1043](https://github.com/sampatel3/tali-platform/pull/1043) | **Open draft; no reviews and no review threads at the cleanup checkpoint.** Its recorded base is current `main` at `9580f1aa7bfff3aa65ba82a6382a91987b50f0d0`, and GitHub reported the prior `84eddae` head merge-clean with `changes`, `merge-safety`, `postgres-contract`, backend, frontend, Vercel, and Vercel Preview Comments all passed. The cleanup commit requires refreshed checks before review approval. |
| [#1047](https://github.com/sampatel3/tali-platform/pull/1047) | Merged; 1 unresolved current P2 remains after another P2 was resolved. The remaining finding requires an owner to retain a legacy workspace-overlay Resume action when both new bulk counts are zero. The current worktree keeps that recovery button and adds the zero-count/ordinary-state regression; the GitHub thread remains unresolved. |
| [#1045](https://github.com/sampatel3/tali-platform/pull/1045) | Merged; 2 unresolved current P2 threads. Local merge `9cf7968b` preserves real terminal outcomes (`hired`, `withdrawn`, and other non-rejection closures) in related-role projections and folds `in_assessment` into the invited funnel count while retaining its sub-count. The GitHub threads remain unresolved. |
| [#1044](https://github.com/sampatel3/tali-platform/pull/1044) | Merged; no review threads. Its compact candidate-summary restoration is integrated without losing the causal, accessibility, read-only, middle-slot, null-display, or context-only safeguards in this branch. |
| [#1042](https://github.com/sampatel3/tali-platform/pull/1042) | Merged; 1 current P2: refresh the roster when waiting related-role scoring finishes without an observed running state. Fixed with active-to-terminal transition coverage in this reconciliation. |
| [#1041](https://github.com/sampatel3/tali-platform/pull/1041) | Merged; 1 current P2: preserve legitimate parenthesized four-digit years while stripping internal IDs. The shared humanizer now distinguishes plausible years such as `(2024)` from non-year IDs and has an exact regression; the merged-PR thread remains unresolved. |
| [#1034](https://github.com/sampatel3/tali-platform/pull/1034) | Merged; 2 current P2 threads: the fired-rule max/min operator direction and omitted policy rationale on approved/overridden read-only cards. Both are fixed and regression-tested in this reconciliation; the merged-PR threads remain unresolved pending review authority. |
| [#876](https://github.com/sampatel3/tali-platform/pull/876) | 2 current: reject-sweep validation P1 and sweep-offer reuse P2. |
| [#855](https://github.com/sampatel3/tali-platform/pull/855) | 1 current P2: spec-derived criteria drag gating. |
| [#852](https://github.com/sampatel3/tali-platform/pull/852) | 2 unresolved but superseded: current intent-aware rehydration/rescreen behavior covers specification edits, and this tree has no tracked `node_modules` symlink. |
| [#638](https://github.com/sampatel3/tali-platform/pull/638) | 5 total: 3 current P1, 1 current P2, and 1 outdated workflow/vendor-drift thread. |
| [#557](https://github.com/sampatel3/tali-platform/pull/557) | 6 total: 1 current P1, 1 current P2, and 4 outdated migration/cutover threads. |

Four open-PR threads reference three paths also modified here. Those branch-only
features are absent or superseded: PR #876's pending-reject sweep does not exist
in this tree, PR #855's `CriteriaEditor` has no drag implementation, and PR
#852's specification-edit behavior is superseded. The seven current threads on
merged PRs #1034/#1041/#1042/#1045/#1047 intersect behavior retained here and
have local code/test remedies. That does not resolve their GitHub thread state.
Do not bulk-comment, resolve, close, or merge those branches without
branch-specific review authority. For each open branch, assign an owner and
update it, extract a still-needed small change, or close it explicitly as
superseded with replacement evidence. Draft PR #1043 must receive the current
continuation, rerun, and review; no reviews/threads is not approval, and green
checks on its earlier pushed head are not evidence for this worktree.

## Honest residual risk register

These items are not hidden under “fixed.” Some require a deliberate decision
because the wrong optimization would make results less accurate or the product
less useful.

| Priority | Residual | Why it remains | Required next decision/action |
|---|---|---|---|
| P0 / deployment | **Production backend readiness remains unverified:** navigation to the legacy production API-health hostname did not complete. The current local built website passed desktop/mobile browser checks, but that is deliberately not a production-backend claim. | Local remediation cannot prove the running Railway/Vercel release, worker topology, or queue heartbeats, and no deployment was authorized. | Complete the release-candidate gates and controlled rollout, then require `/health`, redacted `/ready`, authenticated `/admin/health`, and both queue canaries before declaring production healthy. |
| P0 / remote verification | **The current continuation has exact complete local backend/coverage/static, frontend/build/browser, and retained-PostgreSQL results, but no current remote CI result.** | Local gates cannot substitute for the GitHub merge-head, provider preview, packaged service lifecycle, or reviewer approval. | Push draft PR #1043, require remote CI and preview checks on the new head, require review, and update this ledger only from captured results. |
| Medium / staging | **The Sentry 2.66.0 compatibility/privacy remediation is complete locally but not yet exercised in the remote release environment.** | The strict adversarial suite, clean runtime install, sync-handler wrapper regression, lock/audit checks, and web/worker telemetry contracts are green locally; only remote CI/staging can exercise the packaged service lifecycle and real worker topology. | Keep the exact pins and privacy gates, require refreshed remote CI, then include web startup, sync-route repetition, worker/Beat startup, and secret-free error telemetry in controlled staging smoke. |
| P1 / cost and capability | **Fitted-policy shadow/promotion is dormant while the nightly fitter is scheduled.** Equivalent ordered inputs now reuse the current fingerprinted candidate before expensive search, per-organization work is serialized, and pending output is bounded. Changed evidence can still consume DB/CPU to fit a candidate, but the production engine never loads it and no scheduler opens, records, or concludes durable shadow runs. | Fitted output is currently only a fail-closed safety input to governed rule retunes. Automatically wiring the bookkeeping would still lack durable per-decision shadow identity, realised-outcome linkage, and operator activation; compute deduplication is not feature activation. | Measure the remaining scheduled fit's cost and safety value. Keep it only if that value is justified; otherwise disable the dormant fit schedule without weakening the live rule retuner. Before learned-policy activation, implement the durable shadow lifecycle, bias/outcome gates, observability, and explicit operator promotion. |
| Medium / data policy | **Revision 195 deliberately stops with `workspace_pause_exact_evidence_invalid` if retained revision-182 `exact` evidence does not match the full immutable revision-175 event and compatibility-version contract.** It does not rewrite/delete the append-only audit, silently downgrade evidence quality, or guess whether an earlier compatibility version advance should be reversed. | A noncanonical retained row would require a decision about the truthful additive correction and any operational interpretation; code alone cannot infer that policy from malformed or contradictory history. | A data-policy owner must review the retained source and choose an additive correction/reclassification mechanism before retrying. Do not edit revision 182, delete evidence, restamp past revision 195, or bypass the validator. |
| Medium / scaffold | **`GRAPH_OUTCOME_PRIOR_ENABLED` is not a functional feature.** Bounded shadow math exists, but the fetch returns `None` and configuration rejects enablement. | Outcome-learned graph signals can reproduce historical bias; a numeric nudge without evidence and governance would make matching less trustworthy. | Keep it unavailable in product/configuration. Activate only after graph retrieval is durable, the shadow distribution and predictive value are reviewed, the autoresearch bias gate passes, and rollback/monitoring exist. |
| Medium / security migration | **Legacy integration credentials may still use the unversioned/plaintext read fallback.** | The fallback prevents breaking existing Workable/Fireflies rows during rollout; new encrypted writes alone do not transform old data. | Inventory existing rows, re-encrypt or rotate them with the production integration key, verify previous-key rollback, and remove plaintext reads only after telemetry proves the migration complete. |
| Low / runtime (closed locally) | **Frontend hosting and CI now use the same compatible Node major:** deployment declares `>=22.12.0 <23`, while CI remains reproducibly pinned to Node 22.23.1/npm 10.9.8. | The lower bound enforces Vite 8's minimum and the exclusive upper bound keeps hosting on Node 22 while permitting compatible security releases. | Require the refreshed provider preview and remote CI before deployment; keep the package/CI contract regression test green. |
| Medium / staging | **Broad framework/dependency upgrades have cross-cutting compatibility risk.** | Unit mocks cannot fully exercise server lifecycle, auth/security middleware, provider SDKs, PDF/browser tooling, or deployment packaging under real infrastructure. | Stage the complete upgraded graph on supported runtimes; run migration, auth, provider, file/PDF, worker, and rollback smoke before production. Do not “optimize” by deleting supported behavior to make the upgrade easier. |
| Medium / accessibility | **The website pass did not include physical devices, automated accessibility tooling, or assistive-technology/screen-reader use.** | Source review, desktop/mobile browser smoke, keyboard focus, and reduced-motion checks do not prove semantic announcements, focus order, touch behavior, or real-device rendering. | Run automated accessibility checks plus keyboard and screen-reader smoke on representative public, auth, candidate, assessment, and recruiter flows after the release candidate is deployed to staging. |
| Medium / durability | **Abandoned nonterminal entries in the process-local progress stores can remain indefinitely.** | Terminal entries have bounded retention, but a blind TTL for active-looking work could erase a genuinely long-running operation and make progress less truthful. Process-local state also has no durable owner/heartbeat from which abandonment can be proved. | Move the affected progress identity/state to a durable owner record or add durable owner/heartbeat reconciliation first; only then retire entries proven abandoned. Do not apply an age-only active-state TTL. |
| Low / host security boundary | **Repository path checks cannot eliminate a privileged host-local symlink-swap race between validation and filesystem use.** | Existing validation blocks pre-existing traversal/symlink escapes and re-checks the ordinary write boundary. A malicious process with equivalent or greater host filesystem privilege can still mutate path components between checks; that host is outside the current single-tenant trust model. | If the runtime becomes adversarially multi-tenant, isolate repositories by principal/container or adopt descriptor-relative no-follow/open-at operations and test the complete write sequence. Do not weaken or remove the current compatibility-safe path checks. |
| Medium / graph data migration | **Changing the graph embedding contract to Voyage 3 at 1,024 dimensions is not only a configuration/pricing update.** | Existing vectors were produced under a model/dimension contract; mixing generations can silently damage retrieval quality even when every request succeeds and is metered correctly. | Re-embed and reindex into a separate generation, measure retrieval/parity, cut over atomically only after acceptance, retain a rollback pointer, and retire the old generation only after the observation window. |
| Low / dependency | **The pinned `graphiti-core` emits a Pydantic v1-style `class Config` deprecation warning.** | Upstream release [0.29.2](https://github.com/getzep/graphiti/releases/tag/v0.29.2) remains affected, and upstream [PR #1478](https://github.com/getzep/graphiti/pull/1478) is not yet a released fix. Suppressing the warning or carrying an unreviewed local fork would hide upgrade risk. | Track the upstream fix, then perform a controlled dependency upgrade with the complete Graphiti ingest/search/privacy regression set before removing this documented warning exception. |
| Medium | **A5 input-window divergence:** pre-screen sees untruncated CV/JD while holistic uses 14k/8k windows. | Silently truncating the gate could miss late must-haves; silently expanding holistic may raise token cost and latency. | Choose a canonical evidence-window policy, test long-document must-have placement, and measure accuracy/cost before rollout. |
| Low | **C5 cache/staleness mismatch:** Workable context is in the holistic cache key but not the rerun trigger. | Context churn can pay for a cache miss without a coherent product rescore policy. | Decide which Workable changes are material; coarsen the key or add matching invalidation, then measure hit rate. |
| Low | **S3 pre-screen session overhead:** three to four committed DB sessions surround one fast call. | Some separation is load-bearing for FK visibility and metering durability. | Fold safe hit-count work into an existing transaction and benchmark; move writes only if audit/order guarantees remain. |
| Medium / policy | **F3 protected-characteristic handling:** conversational guidance is stronger than the deterministic reject path. | Free-text CV/ATS context can contain protected/proxy information. | Establish a code-level non-use/redaction invariant, legal review, and shadow/adverse-impact evidence before stronger automation. |
| Medium / policy | **F4 automated-decision notice/explanation/appeal:** internal provenance exists but candidate-facing process is incomplete. | This is product/legal workflow, not a safe backend-only guess. | For opted-in auto-disqualify orgs, design candidate notice, job-relevant explanation, human review/appeal, and jurisdiction policy. |
| Medium / evidence | Adverse-impact monitor can be enabled with insufficient voluntary data. | Code cannot manufacture lawful representative data. | Define voluntary-data process, owner, alert response, retention, and minimum sample policy before enabling. |
| Medium / maintainability | Oversized backend legacy files, the frontend `AppShell`, and oversized frontend pages remain ratcheted; the current backend gate enforces 43 exact legacy baselines plus strict 500-line route/service and 1,000-line general-module ceilings. | Large-scale mechanical splitting inside an already broad behavioral remediation would increase merge and regression risk. | Burn down incrementally behind exact parity tests; lower baselines whenever files shrink and do not add new exemptions. |
| Low / test quality | The frontend warning backlog is closed and the current backend run improved branch coverage to 68.206181%, above the 64.594822% earlier checkpoint, while the combined 74% floor passes at 79.372701%. | Aggregate coverage can still hide weak failure-path coverage even with a green suite. | Raise branch coverage incrementally around payment, authorization, provider failure, worker recovery, and hiring-decision risk; do not delete hard branches or exclude them merely to improve the number. |
| Product validation | Assessment/scoring instrument has limited real-outcome evidence. | Unit tests prove software behavior, not hiring validity or candidate experience. | Resume governed volume, monitor funnel and outcome calibration, validate predictive/fairness claims, and keep irreversible AI reject recommendations human-confirmed by default. |

## Release and production status

### Earlier completed checkpoint (through `b19e087d`)

Complete default non-production backend suite on locked Python 3.11.9 at that
checkpoint:
**5,937 passed, 8 skipped, 16 live production-smoke tests deselected, zero
failures, and zero warnings** in
one uninterrupted checkpoint-tree run (260.61 seconds). Live production
smoke was not run from this unreviewed tree.

Backend coverage: **75.928303% combined line-and-branch** — 70,403/92,723
covered units, comprising 56,581/71,325 lines
(79.328426%) and 13,822/21,398 branches (64.594822%). The enforced combined
floor remains **74%** (raised from 35%) and passed. The ignored originals were
left intact and copies were preserved outside the worktree after measurement;
neither was committed.

Checkpoint PostgreSQL/database-release evidence: **78 passed, 4 skipped** on retained
PostgreSQL 16.14. Fresh `000→179`, existing `178→179`, and `179→178→179`
passed; raw autogenerate parity reported zero operations twice. Required user
boolean nullability and the role-intent self-reference were present. An orphan
preflight failed before writes, left the database at 178, and preserved row
counts. The four skipped fixture cases require create/drop-database authority;
equivalent paths were manually exercised on retained databases instead. No
test database or container was deleted. Migration 176's historical incremental
`175→176` offline SQL result remains valid; no full historical offline-chain
claim is made because legacy migration 015 requires a live connection.

### Current continuation verification status

| Gate | Current local-continuation status |
|---|---|
| Preservation and diff hygiene | Current `origin/main` (`9580f1aa`) is the exact merge-base and remains an ancestor at 0 behind. The 20 intentional source-file deletions are the reachability-proven bloat tranche documented above; no migration, data, route, public API, compatibility root, or test-only/unwired capability is deleted. Published migration invariants remain protected and repeated `git diff --check` passes are clean. |
| Focused migration/runtime checks | Sole migration head `195_compatibility_invariant_hardening`; focused SQLite split-head recovery, exact compatibility-evidence validation, additive related-history guards, migration/bootstrap/immutability contracts, scoring ownership, and recovery-index drift repair passed. |
| Retained PostgreSQL | PostgreSQL 16.14 passed fresh `000→195`, rerun at head, `189→195`, populated `181→195`, and split-head `173+174→195` upgrades through the supported migrator with exact current invariants. No retained database or container was deleted or stopped. |
| Complete backend Python 3.11.9 suite and coverage | **Current full suite passed:** 8,359 passed, 54 skipped, 19 live production-smoke tests deselected, and 0 failed. The most recent coverage run before this bloat-only tranche was also green at 79.372701% combined: 78,591/94,973 lines (82.750887%) and 19,597/28,732 branches (68.206181%). |
| Backend static, dependency, and maintainability gates | **Current and passed:** exact 156-pin CI and 124-pin runtime locks, clean lock installs and `pip check`, Ruff, compileall, sole-head, 43 exact file-size ratchets, clean diff checks, and dead-code reachability across 1,040 modules / 32 roots / 1,035 reachable / 0 candidates. |
| Central Sentry privacy boundary | **Current and passed locally:** Sentry 2.66.0, shared fail-closed web/worker sanitizers, secret-free task/event/transaction envelopes, attachment stripping, no Celery trace propagation, explicit HTTP capture including HEAD/OPTIONS, disabled generative-AI span streaming, clean Jinja-absent runtime startup, and a stable one-wrapper sync-handler regression. The affected strict-warning suite passed 143 tests; refreshed remote CI/staging remains required. |
| Clean Node 22 frontend gates, full Vitest, build, and bundle budget | **Current and passed:** exact architecture/motion/chat/UI/ESLint/TypeScript gates; 198 files/1,482 tests; 3,441-module Vite 8.1.4 production build in 3.80 seconds; bundle budget; 0 dependency vulnerabilities; and the current-build deep-link contract. |
| Current in-app browser website/role-flow verification | **Focused local built-site pass complete:** desktop and 390×844 home, developers, sign-in, demo request, interactive walkthrough, blog, terms, privacy, protected-route redirect, and real 404 passed with no console warnings/errors, broken images, error boundaries, or horizontal overflow. The walkthrough advanced to its next live preview and unauthenticated `/home` preserved its return URL. Authenticated staging flows, real devices, assistive technology, and field metrics remain external. |
| Remote CI and preview for the continuation | **A refreshed run is required for the cleanup tranche.** Draft PR #1043's prior pushed head `84eddae` is merge-clean and all backend/frontend/PostgreSQL/Vercel checks passed. New checks must complete on the cleanup head before approval. |
| Review approval | **Pending.** Draft PR #1043 has no reviews or review threads and must not be treated as approved despite its clean merge state and green earlier-head checks. |

### Required release sequence

1. After final local validation, fetch `origin/main` again, confirm the current
   baseline remains an ancestor, preserve the evidence-backed deletion scope
   and diff-hygiene guarantees, push the continuation to draft PR
   [#1043](https://github.com/sampatel3/tali-platform/pull/1043), and verify the
   resulting diff before requesting review. The current pushed draft is not the
   worktree and is not an approved release artifact.
2. Run the complete backend CI contract on Python 3.11.9: validate and install
   the hashed requirements lock, dependency integrity/audit; compile `alembic`,
   `app`, `scripts`, and `tests`; Ruff E9/F; sole-head, all-module file-size,
   reachability dead-code, PostgreSQL runtime-contract, full pytest, coverage,
   and PR/push-aware diff-whitespace gates.
3. Run `python -m app.scripts.database_migrate` to sole head 189 against a
   recent production-like snapshot/backup in staging. Fresh `000→189`, exact
   invariant/autogenerate, and bounded lock-failure/retry paths are locally green; a
   production-shaped data upgrade remains the final migration rehearsal.
4. Preserve the now-green complete local frontend evidence, then require remote
   CI and the refreshed provider preview to repeat the Node 22.23.1/npm 10.9.8
   exact install, dependency audit,
   architecture/motion/UI lint, ESLint, TypeScript check, full Vitest,
   production build, bundle budget, and built-route smoke.
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
8. Apply migrations and deploy only through the repository-root entrypoint
   `./scripts/deploy_production.sh`. Runtime services must use private
   `DATABASE_URL`; the wrapper may use `DATABASE_PUBLIC_URL` only for the
   external migration step.
9. Require public `/health`, redacted `/ready`, and authenticated
   `/admin/health`; require both general and scoring queue canaries and the
   intended live Anthropic/read-only E2B/Resend/GitHub capabilities.
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

- **Code remediation:** substantial, with additive behavioral fixes plus the evidence-confirmed bloat cleanup documented above; useful and compatibility-sensitive capability is retained. Current-tree validation and refreshed remote CI remain required before release approval.
- **Integrated release candidate:** **not yet**; blocked by supported-runtime CI, production-shaped migration rehearsal, dependency staging, and a reviewed PR.
- **Production release validation:** **not attempted**; it begins only after an approved release candidate, external configuration, and controlled deployment, then requires the post-deploy health/queue/provider/website smoke matrix.
- **Production:** **not changed by this audit**.
- **External follow-up:** coordinated Railway/Vercel rollout, Stripe configuration, provider smoke, adverse-impact governance, GitHub PR triage/review ownership, and live website validation.
