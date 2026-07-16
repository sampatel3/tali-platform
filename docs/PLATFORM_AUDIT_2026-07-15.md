# Taali platform audit and remediation report

- **Audit period:** 2026-07-15–2026-07-16
- **Repository:** `sampatel3/tali-platform`
- **Integrated baseline:** `8f7b47e96d236d694997f97460a55712a0d4d7c4` (merged PR #1042, after PRs #1040 and #1034) plus the final `codex/platform-audit-remediation` branch
- **Release status:** **published as draft PR [#1043](https://github.com/sampatel3/tali-platform/pull/1043); not released and not deployed**

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
current `main` at merged PR #1042 and published as draft PR #1043; it has not
been reviewed, approved, or deployed. The complete default non-production suites,
separate retained-PostgreSQL contracts, and measured coverage are green
locally. Supported-runtime CI, a production-shaped migration rehearsal, and
review approval remain release-candidate gates. External configuration,
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
that had been removed; the final integration preserves every one as a bounded
compatibility artifact. When upstream work later removed two Sister Role dialog
paths that remained in the original audit branch, those were restored unchanged
as well. A later upstream rename of `jobPipelinePageUtils.js` was restored as a
forwarding compatibility facade with contract tests. The branch has zero
deleted paths under strict rename-disabled comparison versus both current
`main` and the original audit branch; no tracked/source file was deleted to
improve a metric.
That breadth is one reason the supported-runtime and reviewable-PR release gates
remain mandatory.

The audit began at `0e562f2f` (`Redesign agent prompts and unblock task setup
(#1026)`, 2026-07-15), moved onto `codex/platform-audit-remediation`, and was
reconciled through current `main` at `8f7b47e9` / merged PR #1042, after the
related-role integration in PR #1040 and decision-presentation integration in
PR #1034. The branch is published as draft PR #1043 and remains undeployed.

The authoritative final backend run used the exact locked Python 3.11.9
environment with all 157 hashed development pins. The final frontend run used
a clean detached worktree with the CI runtime, Node 22.23.1 and npm 10.9.8;
exact install, audit, all gates, production build, bundle budget, and built-route
smoke passed. The backend deployment image also pins Python 3.11.9; the
frontend deployment contract currently declares Node `>=22.12.0`, not an exact
patch. Local Compose, deployment documentation, and migration verification
agree on PostgreSQL 16. Remote supported-runtime CI and the provider preview
remain release gates even after the matching local runtime is green.

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
attached, so no unrelated browser workaround was substituted; the full
frontend suite, route tests, build, and bundle gates are the fresh evidence for
that merge. Navigation to the legacy `api.taali.ai/health` endpoint did not
complete, so backend readiness and queue heartbeats were not verified. Nothing
was deployed by this audit. No physical-device,
assistive-technology/screen-reader, automated accessibility, or field/Core Web
Vitals run was performed.

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
| New decision-explanation summaries bypassed the existing legacy-reasoning humanizer. | Recruiters could see internal application IDs or raw `workable_stage`/`pipeline_stage`/scorer keys on older queued decisions. | The complete humanizer now lives in a shared service, the old domain import remains as a compatibility facade, and decision explanations reuse it before presentation. Four-digit IDs, quoted multi-word stages, and clean-text pass-through are regression-tested. | **Fixed locally; the corresponding PR #1041 review remains unresolved on its source branch.** |
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
| Requisition chat/page growth was being handled inside oversized modules. | Higher change risk and repeated merge conflicts. | Attachment, grounding, capture-support, source, and upload responsibilities were extracted behind import-compatible re-exports. Service is 500 lines, capture 472, prompt 298, route 474, attachment service 300, and capture support 209; the page remains 1,185 lines after PR #1040 and is unchanged through PR #1042, below its 1,201 cap. | **Improved locally; size/architecture gates pass.** |
| Related-role drafts could show a blank/stale header and cramped relationship card. | Sidebar and main panel could disagree or hide context. | PR #1040's title/status fallback and responsive header behavior were preserved in the modular architecture; grounded chat, related-role hydration, intent-aware specification updates, Jobs catalogue, and release safeguards from PRs #1027–#1040 were reconciled before the later PR #1034 decision-presentation and PR #1042 scoring-state integrations. | **Fixed locally; focused and full frontend suites pass.** |
| Related-role scoring treated waiting/retrying as a thin running-state variant and refreshed only after a visible running transition. | The roster and score totals could remain stale when polling observed waiting/retrying directly before completion, while the action label invited duplicate work. | Waiting/retrying are first-class polled states with reasoned notices, accurate scoreable totals and disabled progress actions. The roster refreshes when any active state becomes terminal, including a skipped `waiting→completed` transition, while preserving the governed Process Candidates header extraction. An undefined upstream UI token was also replaced with the established semantic muted token. | **Fixed locally; PR #1042 regression, token, architecture, and full-suite gates pass.** |

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
  modular page is 2,609 lines against its unchanged 2,620-line ratchet;
- concise decision reasons and candidate summaries that retain true factor
  totals, display the fired rule's actual comparison operator, preserve policy
  rationale on approved/overridden read-only cards, and expose unknown factors
  as accessible “unverified” state rather than falsely marking them missing;
- a 24-line candidate-summary fallback extraction that preserves behavior while
  keeping `CandidateStandingReportPage` exactly at its unchanged 2,262-line
  architecture ratchet;
- scoped semantic graph/design tokens rather than hard-coded canvas colors;
- assessment timer and hook-order corrections;
- legal/privacy pages, developer API tests, marketing/showcase route tests, and
  dead-link prevention on public candidate snapshots.

The machine UI guard reports zero unresolved token or component-policy
violations. Frontend architecture and motion-system gates pass. The final
156-file/1,093-test gated run is warning-free: React scheduling warnings fell
from 58 to zero, Router future-flag warnings from 30 to zero, and Motion
diagnostics from two to zero. A fail-closed setup guard now blocks and reports
unexpected API XHR/fetch calls even when application error handling catches the
rejection. No console suppression was added, and CI rejects these warning and
network-leak classes so the clean signal cannot silently regress.

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
| Large route ownership | Process logic moved into a 279-line domain route and dedicated dispatch service; collection/analytics/query responsibilities were also extracted. Exact file-size ratchets prevent re-growth. | **Improved locally.** |
| Architecture enforcement | AST gates detect all supported decorator and imperative route-registration forms, require real admin-guard calls, flatten the assembled FastAPI route table (including lazy included routers), and compare actual authentication/agent-action calls. Exact fail-closed inventories cover public/token ingress and one intentional generated-user-route collision; comments, strings, filename changes, mounts, and include prefixes cannot bypass the checks. | **Fixed locally; 19 architecture-gate tests passed.** |
| Remaining file bloat | Forty-three backend files remain on exact legacy baselines. The gate enforces 500 physical lines for route/service modules and 1,000 for every other `app` module; it rejects growth above every exact baseline, so moving or renaming an oversized file cannot evade policy. This is maintainability debt, not an excuse for a risky blind rewrite in an already large patch. | **Non-release refactor debt; ratchet and bypass regressions pass.** |
| Worker placement | Paid/long-running scoring, processing, delivery, recovery, and reconciliation are off request/web-process lifetime. | **Fixed locally.** |
| Database connection | Runtime web/workers use only Railway's private `DATABASE_URL`; `DATABASE_PUBLIC_URL` is deploy-tool-only for migrations outside Railway. | **Fixed locally; 25 database/deployment contract tests passed earlier in this audit.** |
| Fresh database and PostgreSQL semantics | A canonical `000_initial_schema` reconstructs the pre-Alembic base so a genuinely empty PostgreSQL database can traverse the full chain. The supported wrapper rejects unversioned partial schemas, takes a bounded advisory lock, applies every revision, and validates model/invariant parity. Migration 176 historically restored application timestamp defaults; 177 persists chat-turn role versions, 178 adds CV-score dispatch approval, and 179 restores required user-boolean nullability plus the role-intent self-reference metadata. Runtime contracts also exercise real JSONB/JSON-array search, event idempotency-key uniqueness, immutable audit updates, advisory-lock serialization/release, and disjoint `FOR UPDATE SKIP LOCKED` claims. | **Fixed locally; fresh `000→179`, existing `178→179`, `179→178→179`, zero-op autogenerate checks, and orphan fail-before-write behavior passed on retained PostgreSQL 16.14.** |
| Test isolation | Backend tests select shared in-memory SQLite before app import, avoid disk `test.db`, and have a dedicated real-Postgres CI contract. The only `sqlite:///./test.db` occurrences now assert production rejection behavior. | **Fixed locally.** |

The backend size gate now covers every Python module under `app`: route/service
modules have a strict 500-line ceiling, all other modules have a 1,000-line
ceiling, and 43 oversized legacy files have exact ratcheted baselines. Baselines
were lowered when files shrank, including the process-dispatch extraction;
renaming or moving a hotspot cannot create a blanket exemption. Synthetic
regressions prove that an oversized renamed module and an imperative
`add_api_route` route cannot evade the gate.

The online canonical migration path is the supported contract. Retained
PostgreSQL 16.14 databases completed fresh `000→179`, existing `178→179`, and
`179→178→179`; raw `alembic check` twice reported zero new operations. Migration
179 restored `users.is_active`/`users.is_superuser` non-nullability and the
`role_intents.superseded_id` self-reference. An orphan preflight exited before
writes, left the database at 178, and preserved row counts. Migration 176's
historical incremental `175→176` offline SQL result remains valid, but this
report does not claim that the entire historical chain supports Alembic's
offline/mock connection mode because legacy migration 015 assumes a live
connection.

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
| Requisition model routing | Ordinary chat remains on the configured chat/Haiku model; only current-role/spec/document-sensitive intent escalates to the primary model. | Keeps normal conversational quality and preserves stronger reasoning where evidence-sensitive updates require it, without paying primary-model cost every second turn. |
| Requisition source tokens and corrupt uploads | A newly uploaded source is included once, then hydrated once as durable context on later turns; unreadable attachment-only turns return deterministically before provider construction. | Preserves all usable evidence while removing duplicated tokens and paid no-op calls. |
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

### Superseded paths preserved safely

| Item | Resolution |
|---|---|
| Historical `intent_parser` sub-agent and its obsolete test | Durable `RoleIntent` is canonical. The old path is a provider-free, unregistered facade with tests proving it cannot become a sixth sub-agent or issue model calls. |
| Duplicate scoring schema surface | Retained only as safe Pydantic payload views with isolated default factories; scoring logic remains canonical in the active service. |
| Four stale static preview HTML pages | Replaced with tiny noindex redirect/fallback documents to the React preview routes; the Jobs fallback preserves only `agent=paused|loading`. |
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
- the reachability scanner currently reports 740 modules, 29 explicit runtime
  roots, 735 reachable modules, and zero candidates. Roots are `app.main`,
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
  unknown paths run both.

The exact workflow supply-chain pins are
`actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5`,
`actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065`,
`actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020`, and
`actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02`.
PostgreSQL is
`postgres:16.14@sha256:17e67d7b9890c99b055ba1e0d5c5be4ec27c9d3a72bda32db24a5e5d8a85af0c`.
The 157-pin/3,237-hash development-inclusive lock records input digest
`4b686ff622e8415dc009908a9e7318b0f359303eb9e21b1d233e7c341ff05c09`.
The 125-pin/2,959-hash production runtime lock records input digest
`64f9f4dd6b03651f423123b28f2549f51b10e147f8e9f6858789a954c61ddfef`.
Both verifiers recompute their own inputs before installation; runtime import,
integrity, and vulnerability-audit checks passed. All 34 tracked shell scripts
passed `bash -n`, both workflow YAML files parsed, and all 12 third-party action
references were pinned to full SHAs.

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
| Database/release contract set | 59 release/workflow/lock tests passed; combined database/release evidence passed 78 with 4 fixture-only skips |
| SEO/static-route set | 46 passed |
| Stripe replay/non-grant | 2/2 passed |
| Developer API reference | 1/1 passed |
| Backend file-size ratchet | Passed: route/service modules ≤500 lines, every other `app` module ≤1000, and 43 exact legacy baselines; two bypass regressions passed |
| One-off duplicate implementation scan | Zero AST/hash duplicates at the integration snapshot; not a maintained gate |
| Dead-code reachability graph | 740 modules / 29 explicit roots / 735 reachable / zero candidates; 15 focused scanner regressions and fail-on-candidates gate passed |
| Backend architecture gates | 19/19 passed across route ownership, assembled collisions/authentication, admin-call AST checks, ingress inventories, and agent/action parity |
| Frontend architecture + motion | Passed |
| Frontend UI token/component policy | Passed with zero violations |
| Frontend ESLint + TypeScript contract | Passed |
| Full frontend gated Vitest | Clean Node 22.23.1/npm 10.9.8 install: 156 files / 1,093 tests passed in 36.83 seconds; zero warning, unhandled-error, or unexpected-network diagnostics. The six added tests cover feedback ordering/submission/version conflicts and recruiter Q&A success/empty/error states. CI preserves Vitest failures and independently fails on warning and network-leak classes. |
| Frontend production build + bundle budget | Clean Node 22.23.1/npm 10.9.8 install: 3,410 modules built in 2.06 seconds; 208 files, 5,734,419 bytes raw (5.4688 MiB), 2,652,075 bytes gzip level 9 (2.5292 MiB), and 6,373,376 allocated bytes (6.0781 MiB on the retained temporary worktree filesystem). Raw/gzip bytes: main JS 71,502/19,428, CSS 231,546/39,670, graph 434,159/135,770, charts 412,998/105,280, Job Pipeline 174,018/49,134, Requisitions 45,601/13,040, Client Intake 12,641/4,298, and Candidate Standing Report 80,693/22,992. Bundle budgets and all 14 built-route HTTP smokes passed. |
| Frontend dependency audit | 0 vulnerabilities |
| Complete default non-production backend pytest selection | Locked CPython 3.11.9: 5,932 passed / 8 skipped / 16 live production-smoke tests deselected; zero failures and zero warnings in one uninterrupted 246.65-second final-integration-tree run. PostgreSQL behavior is covered separately below. |
| Backend coverage | 75.927783% combined line-and-branch coverage (70,401/92,721 covered units): 56,579/71,323 lines (79.327847%) and 13,822/21,398 branches (64.594822%). The enforced combined floor remains 74%; the ignored originals were left intact and copies were preserved outside the worktree after measurement, with neither committed. |
| Backend dependency integrity/audit | Both exact locks passed integrity/parity; runtime import and `pip-audit` verification found zero known vulnerabilities |
| Static/syntax/diff checks | Full backend `compileall` and Ruff scopes passed; both workflow YAML files, all 34 tracked shell scripts, and 12 action pins passed their checks; tracked and staged diff checks were clean |
| PostgreSQL migration/invariants | Fresh `000→179`, existing `178→179`, and `179→178→179` passed on retained PostgreSQL 16.14; raw autogenerate parity was zero twice, schema invariants passed, and orphan preflight failed before writes with revision/data unchanged |
| Database/release evidence | 78 passed / 4 skipped. Skipped fixtures require creating and dropping databases; equivalent migration/invariant/fail-closed paths were exercised manually on retained databases, with no database or container deleted. |
| CI/lock contracts | Hash-lock validation, exact runtime/action/image pins, concurrency, path-scope, production-target, and warning-gate workflow tests passed |

### Insufficient-test and governance gaps

1. The final backend run measured 75.927783% combined line-and-branch coverage:
   79.327847% line coverage and 64.594822% branch coverage. The combined ratchet
   enforces 74%, up from 35%. Aggregate coverage is not sufficient assurance for
   every payment, authorization, provider-failure, worker, and hiring-decision
   branch; raise it incrementally with risk-focused tests without deleting hard
   branches or marking them `no cover` merely to improve the number.
2. The application currently contains 221 `pragma: no cover` annotations; the
   majority sit on broad exception handlers. Burn down the legacy exception
   exclusions behind fault-injection tests; the measured percentages exclude
   those clauses and must not be read as proof that every defensive branch was
   exercised.
3. The normal backend suite intentionally uses isolated in-memory SQLite, while
   PostgreSQL-only modules require `TEST_POSTGRES_URL`. CI provisions
   PostgreSQL. This worktree's retained-PostgreSQL 16.14 migration, invariant,
   JSONB, advisory-lock, and `SKIP LOCKED` contracts are green through sole head
   `179_restore_schema_metadata_invariants`. A recent production-shaped
   snapshot rehearsal remains the data-upgrade release gate; the
   already-completed empty-database run should not be described as outstanding.
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
7. The dead-code gate proves reachability from 29 exact reviewed roots, not
   symbol-level usage. Dynamic imports remain the principal blind spot, and a
   zero module-candidate result is not proof that every retained function/class
   is live. Scanner-root or AST-policy changes require review; arbitrary main
   guards, prefixes, type-only imports, dead branches, and non-empty package
   initializers are already covered by focused regressions.
8. The final backend suite passed in a locked Python 3.11.9 environment. The
   final frontend suite also passed from a clean exact Node 22.23.1/npm 10.9.8
   install. Remote Ubuntu CI and the Vercel preview still remain independent
   gates for host-specific behavior after broad framework/dependency upgrades.

## Outstanding Codex/GitHub review work

The pre-publication 2026-07-16 snapshot contained 26 open PRs: 11 drafts and 15
non-drafts. Those open PRs contain 17 unresolved review threads: 12 on current
diffs and 5 outdated. Ten remain actionable on their source branches (5 P1 and
5 P2); seven are fixed or superseded but still unresolved (all five outdated
threads plus both #852 threads). PR #1041's current P2 is also fixed in this
audit but remains actionable on its source branch. Merged PRs #1034 and #1042
have three additional current P2 threads, all fixed in this reconciliation
without changing their source-thread status. Across all eight PRs listed below,
that is 20 unresolved threads: 15 current and 5 outdated. Publishing draft PR
#1043 made 27 open PRs: 12 drafts and 15 non-drafts; that mechanical change does
not alter the pre-publication thread snapshot below.

| PR | Unresolved threads and current assessment |
|---|---|
| [#1042](https://github.com/sampatel3/tali-platform/pull/1042) | Merged; 1 current P2: refresh the roster when waiting related-role scoring finishes without an observed running state. Fixed with active-to-terminal transition coverage in this reconciliation. |
| [#1041](https://github.com/sampatel3/tali-platform/pull/1041) | Open; 1 current P2: align the new explanation summary with the complete existing reasoning humanizer. Fixed here through one shared implementation and a retained compatibility import; the source thread remains unresolved. |
| [#1034](https://github.com/sampatel3/tali-platform/pull/1034) | Merged; 2 current P2 threads: the fired-rule max/min operator direction and omitted policy rationale on approved/overridden read-only cards. Both are fixed and regression-tested in this reconciliation; the source threads remain unresolved pending branch-specific review authority. |
| [#876](https://github.com/sampatel3/tali-platform/pull/876) | 2 current: reject-sweep validation P1 and sweep-offer reuse P2. |
| [#855](https://github.com/sampatel3/tali-platform/pull/855) | 1 current P2: spec-derived criteria drag gating. |
| [#852](https://github.com/sampatel3/tali-platform/pull/852) | 2 unresolved but superseded: current intent-aware rehydration/rescreen behavior covers specification edits, and this tree has no tracked `node_modules` symlink. |
| [#638](https://github.com/sampatel3/tali-platform/pull/638) | 5 total: 3 current P1, 1 current P2, and 1 outdated workflow/vendor-drift thread. |
| [#557](https://github.com/sampatel3/tali-platform/pull/557) | 6 total: 1 current P1, 1 current P2, and 4 outdated migration/cutover threads. |

Five open-PR threads reference four paths also modified here. PR #1041's
humanizer mismatch is fixed locally; the other branch-only features are absent
or superseded: PR #876's pending-reject sweep does not exist in this tree, PR
#855's `CriteriaEditor` has no drag implementation, and PR #852's
specification-edit behavior is superseded. No reviewed thread exposes an
unfixed current-tree behavior. The three merged-#1034/#1042 threads intersect
current files and are fixed locally here. That does not resolve feedback on the
affected PR branches. Do not bulk-comment,
resolve, or merge those threads without branch-specific review authority. For
each branch, assign an owner and update it, extract a still-needed small change,
or close it explicitly as superseded with replacement evidence. This audit's
draft PR must likewise not be treated as reviewed or approved merely because it
is mergeable or has no comments.

## Honest residual risk register

These items are not hidden under “fixed.” Some require a deliberate decision
because the wrong optimization would make results less accurate or the product
less useful.

| Priority | Residual | Why it remains | Required next decision/action |
|---|---|---|---|
| P0 / deployment | **Production backend readiness remains unverified:** the final legacy API-health navigation did not complete, while only the public homepage rendering basics were observed. | Local remediation cannot prove the running Railway/Vercel release, worker topology, or queue heartbeats, and no deployment was authorized. | Complete the release-candidate gates and controlled rollout, then require `/health`, redacted `/ready`, authenticated `/admin/health`, and both queue canaries before declaring production healthy. |
| P1 / cost and capability | **Fitted-policy shadow/promotion is dormant while the nightly fitter is scheduled.** Equivalent ordered inputs now reuse the current fingerprinted candidate before expensive search, per-organization work is serialized, and pending output is bounded. Changed evidence can still consume DB/CPU to fit a candidate, but the production engine never loads it and no scheduler opens, records, or concludes durable shadow runs. | Fitted output is currently only a fail-closed safety input to governed rule retunes. Automatically wiring the bookkeeping would still lack durable per-decision shadow identity, realised-outcome linkage, and operator activation; compute deduplication is not feature activation. | Measure the remaining scheduled fit's cost and safety value. Keep it only if that value is justified; otherwise disable the dormant fit schedule without weakening the live rule retuner. Before learned-policy activation, implement the durable shadow lifecycle, bias/outcome gates, observability, and explicit operator promotion. |
| Medium / scaffold | **`GRAPH_OUTCOME_PRIOR_ENABLED` is not a functional feature.** Bounded shadow math exists, but the fetch returns `None` and configuration rejects enablement. | Outcome-learned graph signals can reproduce historical bias; a numeric nudge without evidence and governance would make matching less trustworthy. | Keep it unavailable in product/configuration. Activate only after graph retrieval is durable, the shadow distribution and predictive value are reviewed, the autoresearch bias gate passes, and rollback/monitoring exist. |
| Medium / security migration | **Legacy integration credentials may still use the unversioned/plaintext read fallback.** | The fallback prevents breaking existing Workable/Fireflies rows during rollout; new encrypted writes alone do not transform old data. | Inventory existing rows, re-encrypt or rotate them with the production integration key, verify previous-key rollback, and remove plaintext reads only after telemetry proves the migration complete. |
| Low / runtime | **The frontend host range remains broader than the CI pin:** the clean local frontend contract passed on exact Node 22.23.1/npm 10.9.8, while deployment declares Node `>=22.12.0`. | The frontend host can select a different Node 22 patch even though the npm 10 lock and Node 22 dispatcher regressions are now fixed and locally reproduced. | Require remote CI and the provider preview before the release candidate, and align the hosting runtime as tightly as the provider supports. |
| Medium / staging | **Broad framework/dependency upgrades have cross-cutting compatibility risk.** | Unit mocks cannot fully exercise server lifecycle, auth/security middleware, provider SDKs, PDF/browser tooling, or deployment packaging under real infrastructure. | Stage the complete upgraded graph on supported runtimes; run migration, auth, provider, file/PDF, worker, and rollback smoke before production. Do not “optimize” by deleting supported behavior to make the upgrade easier. |
| Medium / accessibility | **The website pass did not include physical devices, automated accessibility tooling, or assistive-technology/screen-reader use.** | Source review, desktop/mobile browser smoke, keyboard focus, and reduced-motion checks do not prove semantic announcements, focus order, touch behavior, or real-device rendering. | Run automated accessibility checks plus keyboard and screen-reader smoke on representative public, auth, candidate, assessment, and recruiter flows after the release candidate is deployed to staging. |
| Medium | **A5 input-window divergence:** pre-screen sees untruncated CV/JD while holistic uses 14k/8k windows. | Silently truncating the gate could miss late must-haves; silently expanding holistic may raise token cost and latency. | Choose a canonical evidence-window policy, test long-document must-have placement, and measure accuracy/cost before rollout. |
| Medium | **R6 transient error delay:** all recent pre-screen errors back off up to six hours. | Backoff fixed a severe 7,668-repeat cost incident, but treats rate limits/timeouts like deterministic failures. | Classify errors; use a short bounded retry for transient 429/5xx/timeout and long backoff for deterministic failures. Align the docstring. |
| Low | **C5 cache/staleness mismatch:** Workable context is in the holistic cache key but not the rerun trigger. | Context churn can pay for a cache miss without a coherent product rescore policy. | Decide which Workable changes are material; coarsen the key or add matching invalidation, then measure hit rate. |
| Low | **S3 pre-screen session overhead:** three to four committed DB sessions surround one fast call. | Some separation is load-bearing for FK visibility and metering durability. | Fold safe hit-count work into an existing transaction and benchmark; move writes only if audit/order guarantees remain. |
| Medium / policy | **F3 protected-characteristic handling:** conversational guidance is stronger than the deterministic reject path. | Free-text CV/ATS context can contain protected/proxy information. | Establish a code-level non-use/redaction invariant, legal review, and shadow/adverse-impact evidence before stronger automation. |
| Medium / policy | **F4 automated-decision notice/explanation/appeal:** internal provenance exists but candidate-facing process is incomplete. | This is product/legal workflow, not a safe backend-only guess. | For opted-in auto-disqualify orgs, design candidate notice, job-relevant explanation, human review/appeal, and jurisdiction policy. |
| Medium / evidence | Adverse-impact monitor can be enabled with insufficient voluntary data. | Code cannot manufacture lawful representative data. | Define voluntary-data process, owner, alert response, retention, and minimum sample policy before enabling. |
| Medium / maintainability | 43 backend oversized legacy files, the frontend `AppShell`, and 11 oversized frontend pages remain ratcheted. Backend policy is strict at 500 lines for route/service modules and 1,000 for all others. | Large-scale mechanical splitting inside an already broad behavioral remediation would increase merge and regression risk. | Burn down incrementally behind exact parity tests; lower baselines whenever files shrink and do not add new exemptions. |
| Low / test quality | The frontend warning backlog is closed and the combined backend floor is 74%, but backend branch coverage is still 64.594822%. | Aggregate coverage can hide weak failure-path coverage even with a green suite. | Raise branch coverage incrementally around payment, authorization, provider failure, worker recovery, and hiring-decision risk; do not delete hard branches or exclude them merely to improve the number. |
| Product validation | Assessment/scoring instrument has limited real-outcome evidence. | Unit tests prove software behavior, not hiring validity or candidate experience. | Resume governed volume, monitor funnel and outcome calibration, validate predictive/fairness claims, and keep irreversible AI reject recommendations human-confirmed by default. |

## Release and production status

### Local verification results

Complete default non-production backend suite on locked Python 3.11.9:
**5,932 passed, 8 skipped, 16 live production-smoke tests deselected, zero
failures, and zero warnings** in
one uninterrupted final-integration-tree run (246.65 seconds). Live production
smoke was not run from this unreviewed tree.

Backend coverage: **75.927783% combined line-and-branch** — 70,401/92,721
covered units, comprising 56,579/71,323 lines
(79.327847%) and 13,822/21,398 branches (64.594822%). The enforced combined
floor remains **74%** (raised from 35%) and passed. The ignored originals were
left intact and copies were preserved outside the worktree after measurement;
neither was committed.

PostgreSQL/database-release evidence: **78 passed, 4 skipped** on retained
PostgreSQL 16.14. Fresh `000→179`, existing `178→179`, and `179→178→179`
passed; raw autogenerate parity reported zero operations twice. Required user
boolean nullability and the role-intent self-reference were present. An orphan
preflight failed before writes, left the database at 178, and preserved row
counts. The four skipped fixture cases require create/drop-database authority;
equivalent paths were manually exercised on retained databases instead. No
test database or container was deleted. Migration 176's historical incremental
`175→176` offline SQL result remains valid; no full historical offline-chain
claim is made because legacy migration 015 requires a live connection.

### Required release sequence

1. Review draft PR [#1043](https://github.com/sampatel3/tali-platform/pull/1043)
   against current `main` and retain the strict zero-deletion and clean-tree
   guarantees. The pushed draft is reviewable but is not an approved release
   artifact.
2. Run the complete backend CI contract on Python 3.11.9: validate and install
   the hashed requirements lock, dependency integrity/audit; compile `alembic`,
   `app`, `scripts`, and `tests`; Ruff E9/F; sole-head, all-module file-size,
   reachability dead-code, PostgreSQL runtime-contract, full pytest, coverage,
   and PR/push-aware diff-whitespace gates.
3. Run `python -m app.scripts.database_migrate` against a recent
   production-like snapshot/backup in staging. The genuinely empty and
   fail-closed PostgreSQL 16 paths are already green; a production-shaped data
   upgrade remains the final migration rehearsal.
4. Require the remote frontend CI contract and provider preview to repeat the
   locally-green Node 22.23.1/npm 10.9.8 exact install, dependency audit,
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

- **Code remediation:** substantial and locally complete for the safely actionable findings listed as fixed, with zero source-path deletions.
- **Integrated release candidate:** **not yet**; blocked by supported-runtime CI, production-shaped migration rehearsal, dependency staging, and a reviewed PR.
- **Production release validation:** **not attempted**; it begins only after an approved release candidate, external configuration, and controlled deployment, then requires the post-deploy health/queue/provider/website smoke matrix.
- **Production:** **not changed by this audit**.
- **External follow-up:** coordinated Railway/Vercel rollout, Stripe configuration, provider smoke, adverse-impact governance, GitHub PR triage/review ownership, and live website validation.
