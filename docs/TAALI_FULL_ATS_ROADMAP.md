# Taali → Full Standalone ATS: Phased Execution Roadmap

> _Generated 2026-06-25 via a deep multi-agent research workflow (8 internal codebase auditors + 8 external ATS / Workable-API researchers → synthesis → adversarial critique → finalize; 19 agents, ~1.7M tokens). Load-bearing facts spot-verified against the current codebase: `MVP_DISABLE_WORKABLE: bool = True` default at `backend/app/platform/config.py:435` (gated in ~10 sites); `PIPELINE_STAGES` hard-coded 5-tuple at `pipeline_service.py:44`; `User` extends fastapi-users with no role column; inbound Workable webhook raises 503 when disabled (`webhook_routes.py:164`). Status: SCOPING — not for production until fully tested._


## 1. Executive Summary

**What "full ATS" means for Taali.** A full ATS owns the complete hiring lifecycle end-to-end: publish a requisition → distribute it → receive applications directly → resolve candidate identity → move candidates through a configurable pipeline → schedule and score interviews → extend a structured offer → hand the hire off to HRIS — all under an audit trail and a defensible compliance posture. Today Taali is a best-in-class **AI scoring + technical-assessment layer**. It owns the hard, differentiated middle (CV scoring `cv_matching/holistic.py`, agentic decisions `decision_policy/engine.py` + `domains/agentic/`, real-work assessments `components/assessments/`) and is missing the candidate-acquisition **front** (no careers page, no application form, no job distribution), the hire-completion **back** (no offers, no scheduling, no scorecards, no HRIS handoff), and the **cross-cutting spine** every ATS needs (configurable stages, RBAC, identity resolution, audit immutability, deletion/erasure, anti-abuse).

**Reconcile the current state first (critical).** The draft premise that orgs run on `workable_primary` "today; default" is **contradicted by the shipped config**: `MVP_DISABLE_WORKABLE = True` is the default (`config.py:435`), and the inbound Workable webhook **raises 503 when disabled** (it is not merely a verify-then-discard stub). So the real MVP posture is closer to *Taali-as-primary-already, with Workable off* — not a live Workable system-of-record. **This must be resolved before P0 sequencing is finalized** (see §2 and the P-1 gate), because it decides whether the dual-run / shadow-reconcile machinery is on the critical path or demoted to an optional import track.

**Headline strategic recommendation.** **Default to STANDALONE; keep Workable interop as a migration-import + optional write-back tool — gated by per-org reality.** Build standalone ATS capabilities behind flags. Where a live org *is* on Workable, offer a staged `sync_mode` cutover; where none is, treat Workable as a one-time importer and drop dual-run from the critical path. **Align every new data model and API surface to the Workable SPI v3 object shapes** regardless — this is the single biggest de-risking decision: it makes the build mirror a proven model, preserves interop/import fidelity, and gives any existing Workable customer a clean migration path.

**Rough shape (rebaselined).** Six phases plus a pre-phase reconciliation gate; **~16–20 months** for a credible mid-market ATS. **MVP bar (P0–P2) is ~7–9 months**, not 4–5 — the draft's MVP estimate was the least realistic claim once RBAC, identity resolution, anti-abuse, and the offer/HRIS surface are budgeted honestly.

| Phase | Theme | Duration (rebaselined) | Bar |
|---|---|---|---|
| **P-1** | Current-state reconciliation: Workable posture, `sync_mode` semantics, PII inventory | 1–2 wks | gate |
| **P0** | Foundations: configurable stages, source, dispositions, audit immutability, `sync_mode` | 4–6 wks | pre-req |
| **P0.5** | RBAC + hiring team (split out as its own bounded workstream) | 3–4 wks | pre-req |
| **P1** | Intake: identity resolution, anti-abuse gate, careers page + apply + JobPosting JSON-LD + comms unlock | 9–11 wks | **MVP** |
| **P2** | Offers + e-signature + approvals + HRIS handoff + core hiring analytics | 12–16 wks | **MVP** (credible ATS) |
| **P3** | Scheduling + scorecards + hiring teams + human-eval fairness | 14–18 wks | mid-market |
| **P4** | Distribution (Google/LinkedIn/Indeed), outbound webhooks, public write API, custom domains | 6–8 wks | growth |
| **P5** | Compliance depth (EEO/OFCCP, GDPR DSAR UI), requisitions, CRM/talent pool | 8–12 wks | enterprise |

---

## 2. Strategic Recommendation: REPLACE vs AUGMENT

**Decision: build STANDALONE by default; offer AUGMENT→REPLACE only for orgs actually on Workable, via staged flag flip. Never big-bang.**

**Why not pure replace-now (ignoring interop).** Workable owns two assets Taali cannot rebuild quickly: the **~400M passive-profile sourcing database** and the **200+ job-board distribution network**. The internal verdict (memory: *Taali funnel / ATS intake*) already concluded: **build intake (~80% there), borrow distribution** (ride Google for Jobs + LinkedIn re-route). Aligning to Workable shapes also preserves a credible migration story for any future Workable-based customer.

**Why not pure augment-forever.** The product ceiling and margin are capped by a dependency that, per the shipped config, is *already disabled by default*. Every Workable read is gated on a per-org OAuth token (`Organization.workable_access_token`); a `workable_refresh_token` column **does exist** (`organization.py:15`), so the real gap is **a missing proactive refresh/expiry-monitoring job**, not a missing token. Polling cadence (5–15 min) is the latency floor for status changes when sync is on.

**The mechanism — `sync_mode` enum on `Organization`, default reconciled with `MVP_DISABLE_WORKABLE`:**

| Mode | Funnel owner | Workable role | Used when |
|---|---|---|---|
| `standalone` | Taali | disconnected (import-only tool available) | **default** when `MVP_DISABLE_WORKABLE=True` (today's posture) |
| `workable_primary` | Workable | source of truth; Taali reads + writes back | only orgs with Workable enabled + populated tokens + active polling |
| `taali_primary` | Taali | optional write-back mirror | during a per-org cutover from `workable_primary` |

> **P-1 deliverable:** a one-page census of prod orgs — how many have `MVP_DISABLE_WORKABLE` off **and** populated tokens **and** active polling. If the answer is ~none, the shadow-reconcile / dual-run steps in §9 are **demoted to an optional migration-import track**, not critical path. `sync_mode`'s default must be set explicitly and consistently with `MVP_DISABLE_WORKABLE`.

**How Workable-API alignment de-risks everything (unchanged and still correct):**
- New models mirror Workable object shapes (jobs, candidates, stages, offers, activities, disqualify reasons), so import/reconciliation is field-for-field.
- `workable_op_runner.py` (5 typed ops: approve/override/move/outcome/note, per-org Redis mutex) becomes the **write-back bridge** in `taali_primary` mode and the **canonical importer** in `standalone` cutover.
- The `workable_candidate_id` durable link stays populated for imported candidates; pure-native intake leaves it null — code paths guarding on `workable_candidate_id.isnot(None)` (freeze/terminal logic) must be audited (P0).
- The existing **Assessments Provider** path (`domains/workable_provider/`, flag-gated off) is a *parallel* growth channel — keep shipping it; not on the critical path.

---

## 3. Current-State Assessment

| ATS dimension | Readiness | Evidence |
|---|---|---|
| Candidate profile entity | **Full** | `models/candidate.py` — rich profile, social/skills/education/experience JSON, soft-delete, dedup keys |
| Application / candidacy | **Full** | `models/candidate_application.py` — 2-axis (`pipeline_stage` × `application_outcome`), unique `(candidate_id, role_id)` |
| Application event log | **Full (append-only by convention, not enforced)** | `models/candidate_application_event.py` — append-only, `actor_type`, idempotency key; **no DB-level UPDATE/DELETE protection or tamper-evidence** |
| Role / job entity | **Partial** | `models/role.py` — scoring config, not a requisition; dept/location/salary/employment_type live in `workable_job_data` JSON blob, not columns |
| Scoring rubric / criteria | **Full** | `models/role_criterion.py` (`must/preferred/constraint`) + `org_criterion.py` workspace layer |
| Pipeline / stages | **Partial** | `pipeline_service.py:44` `PIPELINE_STAGES = ("applied","invited","in_assessment","review","advanced")` — **hard-coded tuple**, not configurable; fine stage = synced `workable_stage` string |
| Identity resolution / dedup | **Partial/None** | dedup keys + unique `(candidate_id, role_id)` exist, but **no cross-source merge operation**; a native apply colliding with a Workable-synced candidate has no defined behavior |
| Decisioning | **Full** | `decision_policy/engine.py` (pure-Python rules+threshold), `bias_audit.py`, `domains/agentic/` hub |
| AI scoring | **Full** | `cv_matching/holistic.py` (holistic_v2, sonnet, 2-call, per-req evidence + citations) |
| Technical assessments | **Full** | `components/assessments/` — E2B sandbox, rubric scoring, integrity, A/B |
| Workable read-sync | **Full (gated off by default)** | `components/integrations/workable/sync_service.py`; gated behind `MVP_DISABLE_WORKABLE=True` |
| Workable write-back | **Full** | `services/workable_op_runner.py`, `workable_actions_service.py` |
| Inbound Workable webhooks | **Gated 503** | handler raises 503 when `MVP_DISABLE_WORKABLE` set (`webhook_routes.py:161-179`); does not process payload |
| Careers page / public job pages | **None** | no `/careers` route; `AppShell.jsx` recruiter-authed only |
| Application intake form | **None** | no public apply endpoint; `actions/create_application.py` is recruiter-authed |
| Screening questions | **None** | answers parsed from `workable_data` blob; no Q/A model |
| Job distribution / JobPosting JSON-LD | **None** | only Organization JSON-LD in `index.html`; no JobPosting |
| Interview scheduling | **None** | `models/application_interview.py` is **post-hoc only** (transcript/`provider_meeting_id`/`meeting_date`); no slots/calendar/RSVP |
| Scorecards / structured eval | **None** | no scorecard model; only AI scores + free-text notes |
| Offer management | **None** | offer/hired only exist as `workable_stage` strings + `application_outcome='hired'` |
| RBAC / hiring team | **None** | `User` extends `SQLAlchemyBaseUserTable` (fastapi-users), **no role/permission column at all**; everyone is effectively admin; `components/team/__init__.py` empty |
| Candidate job comms | **None (by locked policy)** | `EmailService` = assessment invite/expiry/results only; rejection email removed PR #637 |
| Email suppression / unsubscribe | **None** | no suppression list exists (the `suppression` matches in code are *Workable-action* suppression, not email) |
| Anti-abuse rate limiting | **Per-process only** | `RateLimitMiddleware` uses in-process `defaultdict(list)` (`middleware.py:15`) — **not shared across replicas**; trivially bypassed in multi-replica prod |
| Reporting / analytics | **Partial** | `analytics_routes.py` rich but JWT-only; **no time-to-hire, time-in-stage, source-effectiveness, or export anywhere in backend** |
| Outbound webhooks | **None** | inbound-only outbox; `PUBLIC_API_BUILD_PLAN.md` Phase 2 unbuilt |
| EEO / GDPR tooling | **None** | EEO actively stripped (`graph_writeback/sensitivity.py`); soft-delete only, no DSAR/retention; PII already accumulates in `claude_call_log`, Neo4j/Voyage graph, `cv_match_details` JSON, email logs |
| Public API | **Partial** | `domains/public_api/router.py` — 8 read endpoints + share-link write; no apply/create |
| Talent pool / CRM | **None** | candidates only exist per-application; `candidate_search/` is internal-only |

---

## 4. Gap Matrix

| Dimension | Taali today | Full-ATS expectation | Gap | B/Bw/I | Pri | Effort | Workable-API alignment |
|---|---|---|---|---|---|---|---|
| Workable-state reconciliation | `MVP_DISABLE_WORKABLE=True` default, ambiguous premise | explicit per-org posture + `sync_mode` default | High | **Build** | **P-1** | S | set default = `standalone` |
| PII inventory + erasure map | none | every PII store registers a deletion hook | High | **Build** | **P-1/P0** | M | Workable GDPR pack |
| Pipeline stages | hard-coded 5-tuple | per-org configurable stages w/ `kind` enum | High | **Build** | P0 | L | mirror `stage_kind`; seed from `workable_stage` |
| Source attribution | `source` String (default `'manual'`) | 2-level strategy+name + `credited_to` | Med | Build | P0 | S | Greenhouse source object; Lever Sources |
| Disposition codes | `auto_reject_reason` free-text | structured enum + actor + stage | Med | Build | P0 | S | `GET /disqualification_reasons` + `disqualify_reason_id` |
| Audit immutability | append-only by convention | DB-enforced no-UPDATE/DELETE + tamper-evidence, all mutating entities | Med | **Build** | P0 | M | `GET /candidates/:id/activities` vocab |
| RBAC / hiring team | `is_superuser` only (no role column) | admin/recruiter/HM/interviewer + per-job | High | **Build** | **P0.5** | **L** | Workable `members` roles |
| Identity resolution / merge | dedup keys, no merge op | deterministic + fuzzy match, merge, conflict policy | High | **Build** | **P1** | L | candidate vs application split |
| Anti-abuse (shared) | in-process limiter only | Redis limiter + CAPTCHA + cost circuit-breaker | High | **Build** | **P1 (gate)** | M | — |
| Email suppression/caps | none | suppression list + daily cap + bounce breaker + verified domain | High | **Build** | **P1 (gate)** | M | Workable email automation |
| Careers page + public job pages | none | branded, crawlable, SEO, per-org subdomain | High | **Build** | P1 | M | Workable/Greenhouse public job board |
| Application form + screening Q | none | configurable Q/A per role | High | Build | P1 | M | `GET /jobs/:shortcode/application_form` + `questions` |
| Resume parse on intake | exists (recruiter-authed) | exists | None | **Reuse** | P1 | S | — |
| Candidate job comms | assessment-only (locked) | confirm/reject/invite/offer + templates | High | **Build** | P1 | M | Workable email automation; **unlock locked policy** |
| Role structural fields | in JSON blob | dept/location/salary/employment_type columns | Med | Build | P1 | S | promote `workable_job_data` fields to columns |
| Hiring analytics | none | time-to-hire, time-in-stage, conversion, source ROI, export | Med | **Build** | **P2** | M | Workable/Greenhouse reporting |
| Offer object + lifecycle | none | versioned, status machine, comp fields | High | **Build** | P2 | L | `GET /candidates/:id/offer`, `/offers/:id` |
| Offer template + merge vars | none | .docx/HTML template engine | High | Build | P2 | M | Workable variable set |
| Offer approval chain | none | sequential groups w/ quorum | Med | Build | P2 | M | Greenhouse approvals schema |
| E-signature | none | Dropbox Sign / DocuSign | High | **Integrate** | P2 | M | Workable Dropbox Sign native |
| HRIS handoff | none | `candidate.hired` webhook + connector | High | Build+Integrate | P2 | M | Workable→BambooHR field set |
| Interview scheduling | post-hoc record only | calendar OAuth + slots + self-schedule | High | **Build+Integrate** | P3 | L | Workable `/events`; Greenhouse `scheduled_interviews` |
| Scorecards | none | per-attribute ratings + blinding | High | Build | P3 | L | Greenhouse Scorecard schema; ratings API |
| Human-eval fairness | AI-path only (`bias_audit.py`) | scorecards/offers in adverse-impact scope | Med | **Build** | **P3** | M | extend bias_audit |
| Job distribution | none | LinkedIn XML + Indeed + Google Jobs | High | **Integrate** | P4 | M | LinkedIn feed; Indeed Job Sync API |
| Outbound webhooks | inbound only | subscription + signed delivery | Med | Build | P4 | M | generalize `workable_webhook_outbox` |
| Public write API | read-only | apply/create/move | Med | Build | P4 | M | extend `public_api/router.py` |
| Custom careers domain | none | per-org subdomain (P1) + CNAME (P4) | Med | Build | P1/P4 | M | Workable careers domain |
| EEO/OFCCP | stripped | firewalled survey + 2yr retention | Med | Build | P5 | M | separate compliance module |
| GDPR DSAR UI/retention | soft-delete only | consent + export + cascade-erase + purge | High | **Build** | P5 (design P-1) | L | Workable GDPR pack |
| Requisitions/headcount | none | req→opening, approval, fill-on-hire | Low | Build | P5 | M | `GET/POST /requisitions` |
| Talent pool / CRM | search only | pools + nurture sequences | Low | Build | P5 | L | `/talent_pool`; Lever Contacts |

---

## 5. Phased Roadmap

> **MVP bar = P0 + P0.5 + P1 + P2** (with the P-1 gate resolved first). That is the minimum to credibly say "Taali is an ATS": post a job, receive an application directly, resolve identity, move it through a real funnel under RBAC + audit, score/assess it, report time-to-hire, and extend a signed offer — no Workable required. P3+ raise it to mid-market/enterprise.

### P-1 — Current-State Reconciliation (1–2 wks) · *gate, blocks P0 sequencing*

**Goal.** Replace the contradicted "workable_primary today" premise with the real shipped posture so the cutover plan is right-sized.

**Workstreams**
1. **Workable census.** Count prod orgs with `MVP_DISABLE_WORKABLE` off + populated tokens + active polling. Output decides whether dual-run is critical path (§9) or an optional import track.
2. **`sync_mode` default decision.** Set default consistent with `MVP_DISABLE_WORKABLE` (almost certainly `standalone`); document the three modes and which orgs (if any) start in `workable_primary`.
3. **PII inventory + erasure map (design artifact).** Enumerate every store holding candidate PII — `candidate`, `candidate_application`, `cv_match_details` JSON, `claude_call_log` prompts, Neo4j/Voyage graph, email bodies/logs, S3 CVs — and the purge/anonymize path for each. This artifact governs P1–P4 (every new PII-writing store must register an erasure hook at build time).

**Exit criteria.** Written census + `sync_mode` default merged into config; erasure-map artifact reviewed; decision on dual-run-as-critical-path vs import-track recorded.

---

### P0 — Foundations (4–6 wks) · *prerequisite*

**Goal.** Make the spine data-driven, auditable, and tamper-evident so all later features hang off clean primitives.

**Workstreams**
1. **Configurable pipeline stages (effort: L, highest-risk single change).** Replace the `PIPELINE_STAGES` tuple with a `pipeline_stage` table (`org_id`, `slug`, `name`, `kind` enum `sourced|applied|screening|assessment|interview|offer|hired|rejected`, `position`, `is_default`). Add `stage_kind` to `candidate_application`. Keep the coarse internal gate but make the fine stage first-class. **Mirror:** Workable stage object. *Risk: 20+ backend callsites (role_support/mcp/scripts/migration) + FE `JobPipelinePage.jsx`/`PIPELINE_STAGE_ORDER` + public schemas + the deeply-baked `advanced = Taali hand-back terminal` semantics (scoring freeze, outcome-learning fires on advance). Extending past `advanced` to Offer/Hired stages means reconsidering terminal semantics + migrating existing rows. Seed from existing `workable_stage` strings.*
2. **Source attribution.** Add `source_strategy` + `source_name` + `credited_to_user_id` to `candidate_application` (note: the existing column is `source` String default `'manual'`, not `lead_source`). **Mirror:** Greenhouse 2-level source.
3. **Disposition catalog.** New `disqualification_reason` table (`org_id`, `label`, `category` `we_rejected|they_withdrew|other`); add `disposition_code` + `rejected_by_actor_id` to `candidate_application`. **Mirror:** `GET /disqualification_reasons`.
4. **Audit immutability (upgraded from "event-vocabulary alignment").** Enforce append-only at the DB layer (DB triggers or a dedicated role that cannot UPDATE/DELETE the event table) with an **acceptance test** (attempt UPDATE/DELETE as the app role → must fail). Confirm `actor_id` on every transition; align `event_type` strings to Workable activity actions. Extend append-only coverage design to the mutating entities arriving in P2/P3 (offer status, scorecard submit, disposition) — record actor + before/after. Consider a periodic hash-chain for tamper-evidence.
5. **`sync_mode` flag wiring.** Add to `organization.py` (default from P-1); audit `workable_candidate_id.isnot(None)` guards.

**Deliverables.** Migrations + backfill; stage CRUD API; updated `pipeline_service.transition_stage()`; DB-enforced audit log + failing-on-mutation test.
**Dependencies.** P-1.
**Exit criteria.** Existing orgs run unchanged with data-driven stages; arch gate + full pytest green (run in isolation — see §9); a manual role can define custom stages; audit-immutability test passes.

---

### P0.5 — RBAC + Hiring Team (3–4 wks) · *prerequisite, own bounded workstream*

**Goal.** A real authz model — its hardest consumers are downstream (P2 approvers, P3 interviewers/blinding), so build it fully now to avoid a second pass.

**Why split out.** `User` extends `SQLAlchemyBaseUserTable` (fastapi-users) with **no role column today**; everyone is effectively admin. Bolting an authz matrix onto fastapi-users deps, threading it through every write endpoint, and back-defining existing users is a cross-cutting **L**, not an **M** line item.

**Workstreams**
1. **Role model + migration.** Add `User.role` enum (`admin|recruiter|hiring_manager|interviewer|viewer`) + `job_hiring_team` join. **Default all existing users to `admin`** (preserve current behavior) via explicit migration.
2. **Permission-check dependency layer** in `identity_access/`. **Enumerate every write endpoint** it must wrap; add an arch/test gate that **fails if a new write route lacks an authz dependency**.
3. **Gate writes before reads.** Roll out write-gating first; reads follow.

**Deliverables.** Role enum + join + migration; authz dependency; write-route coverage test.
**Dependencies.** P0.
**Exit criteria.** Existing orgs behave identically (all admin); a non-admin role is correctly denied a gated write; coverage test green. *If RBAC ships thin here, schedule an explicit hardening step at the start of P2.*

---

### P1 — Candidate Intake (9–11 wks) · **MVP**

**Goal.** Taali can receive an application directly, with no Workable, safely and without duplicates.

> **P1 gate (blocks the public apply endpoint and ALL job comms — ships first):**
> - **Shared anti-abuse:** Redis-backed rate limiter keyed on IP + role + fingerprint (the in-process `RateLimitMiddleware` is useless across replicas), layered behind **Turnstile/CAPTCHA** that fires *before* `create_application`, plus per-role/day application cap and a **cost circuit-breaker** on async scoring.
> - **Email safety:** suppression/unsubscribe list (none exists today), per-org daily send cap + queue spreading, bounce-rate circuit breaker, verified send domain. **Integration test: a send to a suppressed/previously-bounced address is blocked.**

**Workstreams**
1. **Identity resolution / merge.** Deterministic match on email + fuzzy on name+phone; a candidate-**merge** operation (event-logged, dedups applications); **defined conflict behavior** when a public applicant matches an existing `workable_candidate_id` record and against the unique `(candidate_id, role_id)` constraint (upsert vs reject vs merge). *Must precede or accompany the public apply endpoint.*
2. **Promote Role structural fields.** Add `employment_type`, `location_city/country`, `workplace_type`, `department`, `salary_min/max/currency/period`, `status` (`draft|published|closed|archived`), `slug`. Backfill from `workable_job_data` via `sync_service._format_job_spec_from_api()` / FE `jobSpecFormatting.parseJobSpec()`. **Mirror:** Workable job object. *Risk: blob shape varies by account — validate against prod payloads.*
3. **Screening questions.** New `screening_question` + `candidate_answer` (JSONB). **Mirror:** `GET /jobs/:shortcode/application_form`.
4. **Public careers site.** Server-rendered or crawlable static-fallback (reuse `public/*.html` + Vercel-rewrite pattern from SEO PRs #555/#556) `/careers/:org_slug` index + `/careers/:org_slug/:role_slug` detail emitting **JobPosting JSON-LD**. **Decide per-org subdomain (`org_slug.careers.taali…`) now** so JSON-LD canonical URLs/sitemaps/Indexing API (P4) are designed against the final URL scheme (custom CNAME can be P4).
5. **Public apply endpoint.** New `careers/v1` router (separate from API-key `public/v1`), no-auth `POST /careers/v1/roles/:id/apply`: gate (above) → `process_document_upload()` (reuse) → `actions/create_application.py` (reuse, add `Actor.candidate()`) → async `parse_cv` (reuse).
6. **Candidate communications (unlock).** **Revisit the locked policy (§8).** Add `email_template` model + `application_received`, `stage_moved`, `rejection`, `interview_invite` templates on existing Resend infra. Wire the dead `Organization.invite_email_template` field. **All sends pass the P1-gate email-safety layer.**

**Deliverables.** Careers pages live behind flag; apply→identity-resolve→candidate→application→score works natively; confirmation email sends through suppression/caps.
**Dependencies.** P0, P0.5, P1-gate.
**Exit criteria.** A test org publishes a role; an external applicant applies (no Workable), is correctly deduped/merged against any synced record, lands in `applied` with parsed CV + source, gets a confirmation email, and is scorable by `holistic.py`. Google Rich Results Test passes; a suppressed-address send is blocked in test.

---

### P2 — Offers + Hire Completion + Core Analytics (12–16 wks) · **MVP (credible ATS)**

**Goal.** Close the hire loop natively and give recruiters the baseline metrics a "credible ATS" implies.

**Workstreams** *(largest single capability area — 4–5 distinct stateful/integration surfaces)*
1. **Offer model.** `offer` (`application_id`, `version`, `status` `draft|pending_approval|approved|sent|accepted|declined|expired|deprecated`, timestamps, `starts_at`, `expires_at`) + `offer_compensation` (typed: `base_salary_amount`, `currency`, `pay_frequency`, `signing_bonus`, `equity_units`) + JSONB custom fields. **Mirror:** Greenhouse offers + BI Connector. *Typed comp fixes the Workable→BambooHR gap where frequency/currency are lost.*
2. **Template engine.** `offer_template` with Workable's canonical merge vars (`[candidate]`,`[salary]`,`[start_date]`,`[candidate_signature]`…). Reuse the candidate-feedback PDF generation.
3. **Approval chain.** `offer_approval` (`offer_id`, `group_order`, `group_quorum`, `approver_id`, `status`, timestamps). Sequential for MVP. **Consumes P0.5 RBAC** (approver role).
4. **E-signature.** Integrate **Dropbox Sign** (Workable-native, cheaper/simpler than DocuSign). Lock all fields except `[candidate_signature]`; store signed PDF to S3 + consent audit (`timestamp`, `ip`, `signer_email`). *Budget for legal/consent edge cases.*
5. **HRIS handoff.** On `offer.status→accepted`, enqueue `candidate.hired` via a **generalized outbox** (extend `workable_webhook_outbox`). First connector **BambooHR** (`POST /employees`) incl. comp frequency + currency. *Field mapping routinely eats weeks.*
6. **Core hiring analytics (new — was an implicit "Partial" gap).** Time-to-hire, time-in-stage, stage-conversion, source-effectiveness, offer accept rate + CSV export. These fall out cheaply once P0 stages + dispositions + source attribution exist; expose beyond the JWT-only `analytics_routes.py`.
7. **Offer comms** via Resend (requires P1 unlock + email-safety gate).

**Deliverables.** Recruiter creates offer → approval → e-sign → accept → hired → BambooHR record + signed PDF; recruiters see time-to-hire/conversion + export.
**Dependencies.** P0 (stages incl. offer/hired kinds + audit), P0.5 (approver RBAC), P1 (comms unlock + gate).
**Exit criteria.** Full offer lifecycle in a test org with no Workable; signed PDF retrievable; BambooHR sandbox receives the hire with comp frequency + currency; time-to-hire + source report render and export.

---

### P3 — Collaborative Hiring: Scheduling + Scorecards + Human-Eval Fairness (14–18 wks) · *mid-market*

**Goal.** Structured, fair human evaluation alongside Taali's AI evaluation. (Most under-budgeted phase in the draft; tz/DST + double-booking are long-tail.)

**Workstreams**
1. **Calendar tokens.** `calendar_token` (`user_id`, `provider`, encrypted tokens, scopes) + OAuth connect.
2. **ScheduledInterview.** Extend post-hoc `application_interview.py`: `external_event_id`, `start_at/end_at` + IANA tz, `video_conferencing_url`/`video_provider`, `status` enum, `organizer_user_id`; new `scheduled_interview_interviewer` join (`response_status`, `scorecard_id`). **Build vs buy: buy** — **Nylas v3** (or Cronofy) for free/busy + event create + video-link autocreate rather than maintaining Google Calendar + MS Graph separately. **Mirror:** Workable `/events`; Greenhouse `scheduled_interviews`.
3. **Self-scheduling links.** Reuse share-link token-signing (`models/share_link.py`); new `scheduling_link` config; unauth `GET slots` / `POST book` (behind the P1 shared rate limiter).
4. **Interview kits.** `interview_kit_template` (sections→questions) + per-stage assignment, drawn from `role_criterion`.
5. **Scorecards.** `scorecard` (`application_id`, `scheduled_interview_id`, `interviewer_id`, `overall_recommendation` `definitely_not|no|no_decision|yes|strong_yes`) + `score_attribute` + `score_question`. **Enforce bias-blind submission at the API layer** (`submitted_at IS NULL` gate, not just UI). **Mirror:** Greenhouse Scorecard; map Taali's 6 scoring axes.
6. **Human-eval fairness (new — pulled forward from P5).** Route scorecard outcomes + offer/reject decisions through an extension of `decision_policy/bias_audit.py`; define cohort / 4-5ths or group analysis for human ratings; ensure scorecard-driven dispositions are captured for the P5 EEO firewall + audit log. *Adding subjective human ratings without folding them into the existing fairness audit reintroduces exactly the bias risk the AI layer is governed for.*
7. **Interview/reminder comms** via Resend (email-safety gate).

**Deliverables.** Recruiter schedules a panel, candidate self-books, interviewers submit blinded scorecards, aggregate debrief; human decisions appear in the fairness audit.
**Dependencies.** P0.5 (RBAC/teams), P1 (comms + gate).
**Exit criteria.** End-to-end scheduling + blinded scorecard collection in a test org; blinding enforced server-side; adverse-impact report includes human ratings. *(AI scorecard synthesis is a fast-follow.)*

---

### P4 — Distribution + Integration Surface (6–8 wks) · *growth*

**Goal.** Replace Workable's distribution moat via borrow-not-build; open Taali's data to partners.

**Workstreams**
1. **Google for Jobs.** JSON-LD shipped P1 → add Google **Indexing API** (`URL_UPDATED`/`URL_DELETED`, service-account OAuth; apply for quota) + jobs XML sitemap.
2. **LinkedIn XML feed.** Add `linkedin_company_id` to `organization.py`; `GET /feed/linkedin.xml`, 12h cache; submit via LinkedIn ATS-partner ticket.
3. **Indeed.** Start organic (crawler picks up JSON-LD). Defer the ~6-wk Indeed Job Sync API partnership until >50 active roles (note: Indeed's free organic single-source feed ended 2026-03-31).
4. **Outbound webhooks.** `webhook_subscription` (`org_id`, `url`, `events[]`, `secret`) + delivery via generalized outbox; HMAC-SHA256 (reuse Svix/Resend signing). Events: `application.scored`, `decision.made`, `assessment.completed`, `candidate.hired`, `application.stage_changed`.
5. **Public write API.** Extend `public_api/router.py` with `POST /applications`, `POST /applications/:id/move`, mirroring Workable field names for migration tooling.
6. **Custom careers domain (CNAME).** Per-org CNAME on the subdomain scheme decided in P1.

**Deliverables.** Roles indexable in Google for Jobs + LinkedIn; partners subscribe to signed events; API-key holders create/move applications.
**Dependencies.** P1 (careers/JSON-LD + URL scheme), P0 (stages).
**Exit criteria.** A published role appears in Google for Jobs; a test subscription receives a signed `candidate.hired`.

---

### P5 — Compliance + Enterprise Depth (8–12 wks) · *enterprise/regulated*

**Goal.** Pass EEO/OFCCP + GDPR scrutiny; support headcount-controlled orgs. (Erasure *design* already landed in P-1/P0; this phase ships the UI + remaining depth.)

**Workstreams**
1. **EEO/OFCCP.** Firewalled `eeo_submission` table (not joined to the hiring query path; row-level security blocks HM roles), post-submit voluntary survey, aggregate-only export, 2yr retention. Pull scorecard-driven dispositions (P3) into scope.
2. **GDPR.** `application_consent` (timestamp, policy version, purpose, IP); DSAR export endpoint; **cascade hard-delete/anonymize** executing the P-1 erasure map across `candidate`, `candidate_application`, `cv_match_details`, `assessment`, Neo4j/Voyage graph, `claude_call_log` prompts, email logs, `usage_event` (anonymize, keep for billing); nightly retention-purge Celery task; candidate self-service data-rights page (reuse share-link token).
3. **Requisitions.** `requisition` + `job_opening` (`req_id`, headcount, `reason_for_hire`, approval chain); gate hire on an open opening. **Mirror:** Workable `/requisitions`, Greenhouse req/opening split.
4. **CRM/talent pool.** Promote `candidate_search/` (Graphiti/Neo4j/Voyage) into named pools + `nurture_sequence`/`sequence_step`. *Lowest priority — Workable's sourcing DB is unmatched; lead with inbound + agent engagement.*

**Deliverables.** Compliance module + reports; DSAR self-service; requisition gating.
**Dependencies.** P-1 erasure map, P0–P3.
**Exit criteria.** EEO firewall verified (HM role gets 403 on `eeo_submission`); a deletion request genuinely erases across **every** store in the P-1 map (incl. `claude_call_log` prompts + graph); hire blocked when no open opening.

---

## 6. Reuse of Taali's Strengths (accelerators)

| Asset | Accelerates | How |
|---|---|---|
| **AI scoring** `cv_matching/holistic.py` | P1 | Native intake gets scoring for free; per-req evidence + citations = the differentiator vs commodity ATS screening |
| **Assessments** `components/assessments/` | P3 | Technical screen as a pipeline stage; outclasses HackerRank/Codility bolt-ons |
| **Agentic decisions** `decision_policy/engine.py`, `domains/agentic/` | P0/P3 | Pure-Python engine extends to a `move_to_stage` action; agent next-best-action on the Application object |
| **Bias audit** `decision_policy/bias_audit.py` | P3 | Extend to human scorecards/offers rather than rebuild a fairness layer |
| **Candidate graph/search** `candidate_search/` | P5 | Talent-pool/CRM backbone (no rebuild) |
| **Public API** `domains/public_api/` + `api_key.py` scopes | P4 | Write endpoints extend the frozen-schema contract |
| **Share-link pages** `models/share_link.py` + signing | P1/P3/P5 | Token pattern reused for apply confirmation, self-schedule links, DSAR data-rights page |
| **S3 / `document_service.py`** | P1/P2 | Intake CV upload, multi-attachment, signed offer PDF storage |
| **Resend** `EmailService` + `templates.py` + delivery tracking | P1/P2/P3 | Backs all new comms once the email-safety gate ships; `invite_email_id` tracking generalizes per email type |
| **Outbox** `workable_webhook_outbox`, `brain_feed_outbox` | P2/P4 | Proven durable delivery (dedup, 8-retry) → generalize to outbound webhooks + HRIS handoff |
| **Stripe** | all | Metering/billing water-tight; ATS seats/usage bill through the existing ledger |
| **Workable client** `service.py` + `workable_op_runner.py` | dual-run / import | Write-back bridge in `taali_primary`; canonical importer in `standalone` cutover |

---

## 7. Workable-API Alignment & Data-Model Map

| Workable object / endpoint | Taali model (extended) | Status | Note |
|---|---|---|---|
| `GET/POST /jobs` (state, dept, location, salary, employment_type) | `role.py` + promoted columns + `status`/`slug` | P1 | promote JSON-blob fields to columns |
| `GET /jobs/:shortcode/application_form` + `questions` | `screening_question` + `candidate_answer` | P1 | primary intake gap |
| `GET /candidates/:id` | `candidate.py` | done | add `cover_letter`, `address`, `custom_attributes`; merge op in P1 |
| Candidate vs Application split | `candidate` + `candidate_application` | done | **existing alignment advantage** |
| `GET /stages`, `/jobs/:id/stages`, `POST /move` | `pipeline_stage` + `stage_kind` | P0 | replace hard-coded tuple |
| `GET /candidates/:id/activities` | `candidate_application_event` | P0 | align vocabulary; DB-enforced append-only |
| `GET /members`, `/jobs/:id/members` | `User.role` + `job_hiring_team` | P0.5 | no role column exists today |
| `POST /candidates/:id/ratings` (scorecard) | `scorecard` + `score_attribute` | P3 | map 6 scoring axes; route through bias_audit |
| `POST /candidates/:id/comments` | timeline event + write-back | done | `workable_op_runner` OP_POST_NOTE |
| `GET /candidates/:id/offer`, `/offers/:id`, approve/reject | `offer` + `offer_compensation` + `offer_approval` | P2 | typed comp fixes BambooHR gap |
| `GET /disqualification_reasons` + disqualify | `disqualification_reason` + `disposition_code` | P0 | native catalog, default to Workable vocab |
| `GET /custom_attributes` + update | `organization_field` + `candidate_field_value` | P5 | extensibility without migrations |
| `POST /subscriptions` (webhooks) | `webhook_subscription` + outbox | P4 | Taali becomes emitter |
| `GET/POST /requisitions` | `requisition` + `job_opening` | P5 | req→opening split |
| `/talent_pool` | pools over `candidate_search/` | P5 | lowest priority |
| Public careers API (no-auth) | `careers/v1` router | P1 | separate from API-key `public/v1`; per-org subdomain scheme |
| Webhook ingest (`/webhooks/workable`) | gated 503 under `MVP_DISABLE_WORKABLE` | n/a | not a live source today — reconcile in P-1 |
| Assessments Provider (`/tests`,`/assessments`,callback) | `domains/workable_provider/` | shipped (flag off) | **parallel channel, not on critical path** |

---

## 8. Candidate Communications — first-class workstream

**The LOCKED prior decision must be revisited.** Memory (*Taali never emails candidates re the job*, LOCKED 2026-06-14): "the ATS owns ALL candidate job comms; Taali emails candidates ONLY re the assessment." **That was correct while Workable owned the funnel. Taali-as-ATS now owns the funnel — so it owns the job comms.** This is a P1-blocking policy decision for Sam (§10), and a formal LOCKED-decision reversal.

**Scope (P1 onward):** application-received confirmation, stage-moved notification, rejection (with reason), interview invite/reminder, offer delivery, two-way threads (P3+).

**Build on existing Resend infra:** `EmailService`, shared `_render_taali_email` shell in `templates.py`, durable Celery send (`components/notifications/tasks.py`), per-email delivery tracking (generalize `invite_email_id` → per-email-type), co-branding (`_resolve_candidate_facing_brand`), reply-to routing. Wire the dead `Organization.invite_email_template` field. Route results to the per-role hiring manager (today: first superuser by `created_at` — wrong for multi-recruiter orgs; fixed once P0.5 RBAC lands).

**Deliverability is a hard prerequisite sub-phase, not a feature line item.** The suppression list the draft relied on **does not exist** (code `suppression` matches are *Workable-action* suppression). The ~597-send / 28%-bounce incident (PR #637 removed the rejection email; an archived-req reject fallback fired branded "Update on your application…" to stale addresses) is real, and auto-reject volume is high (role-53 rejected 1,398). **Ship BEFORE any automated job comm, as the P1 gate:** (1) suppression/unsubscribe list, (2) per-org daily send cap + queue spreading, (3) bounce-rate circuit breaker, (4) verified send domain — with an **integration test** that a send to a suppressed/previously-bounced address is blocked. **Never gate an irreversible bulk send behind tool-rejection** (memory: *railway ssh survives rejection* — a rejected send script still emailed 8 candidates). Keep rejection emails behind BOTH the policy unlock AND this gate.

---

## 9. Testing & Rollout Strategy

**Non-negotiable: nothing hits production until fully tested. Every new capability ships behind a flag, default off.**

**Per-phase testing**

| Phase | Unit | Integration | E2E |
|---|---|---|---|
| P-1 | — | census query against prod read replica | — |
| P0 | stage transition graph, disposition enum, **audit-immutability (UPDATE/DELETE as app role must fail)** | migration + backfill on throwaway Postgres container (memory: *local postgres migration testing* — not localhost:5432) | stage move via API with all actor types |
| P0.5 | RBAC matrix, default-to-admin migration | authz dependency on every write route (coverage test) | non-admin denied a gated write |
| P1 | identity-match scoring, JSON-LD shape, form validation, CAPTCHA, **suppression block** | apply→identity-resolve→create_application→parse_cv→score; **shared (Redis) rate limit across simulated replicas**; suppressed-address send blocked | careers page render + apply + dedup-vs-synced-candidate + Google Rich Results Test |
| P2 | offer state machine, approval quorum, merge-var render, time-to-hire calc | Dropbox Sign sandbox, BambooHR sandbox | offer→approve→sign→accept→hired→HRIS; analytics report + export |
| P3 | blinding gate, slot intersection, **tz/DST correctness**, fairness cohort calc | Nylas sandbox calendar | schedule→self-book→blinded scorecard→debrief; human ratings in adverse-impact report |
| P4 | feed XML schema, HMAC signing | Indexing API, webhook delivery retries | publish→index→subscriber receives signed event |
| P5 | EEO firewall (HM 403), **cascade-delete completeness across the P-1 map** | retention-purge job on seeded stale data | DSAR export + genuine erasure across **all** stores incl. `claude_call_log` + graph |

**Test infra cautions (from memory):** backend suite is flaky in batch (shared in-memory SQLite leaks state) — **run suites in isolation to judge real pass/fail**; worktrees lack deps — symlink main's FE `node_modules`, run pytest via main `.venv` from worktree backend dir; **restore real CI** (currently build/syntax gates only — *pre-pilot minimal CI*) before the pilot; check alembic heads before/after fleet merges (multi-head = boot fail; GitHub "CLEAN" misses it).

**Seed data.** Build an ATS demo org (extend the `taali-demo` slug pattern) with published roles, custom stages, synthetic candidates (including a deliberate Workable-synced-vs-native dedup collision), mock offers — for E2E + investor/sales demos.

**Feature flags & rollout.** Per-org `sync_mode` (default `standalone`, reconciled with `MVP_DISABLE_WORKABLE`) + per-capability flags (careers, offers, scheduling, comms).

**Cutover — two tracks, decided by P-1:**
- **If no live org is on Workable (expected):** dual-run / shadow-reconcile is **not** on the critical path. Workable is an **import tool** only: one-time canonical import via `workable_op_runner`, then the org runs `standalone`. New native orgs never touch Workable.
- **If some orgs are live on Workable:** staged per-org cutover — (1) `workable_primary` + native features flagged on internally (no candidate impact); (2) internal dogfood org → `taali_primary` shadow (Taali owns funnel, mirrors to Workable, reconcile diffs nightly via the cost-reconciliation reporting pattern); (3) one friendly pilot org → `taali_primary`, candidate comms on, monitor bounce/deliverability; (4) validated → `standalone`. **No fleet-wide flip.**

**Dual-run caveats (only if track 2 applies).** `workable_stage` is a free-form per-org string — the canonical stage-kind mapping must be decided per org before shadow-run is trustworthy. Token risk: a `workable_refresh_token` exists but there is **no proactive refresh/expiry-monitoring job** — a silent 401 during dual-run would go unnoticed; build the monitor before relying on the bridge.

**Preview constraint (memory):** Vercel Preview has no `VITE_API_URL`; backend-coupled features can't get a shareable preview — verify on real authed pages, not the showcase (which renders different components). Careers pages (public, crawlable) are the exception and *can* be previewed visually.

---

## 10. Risks, Open Questions & Decisions for Sam

**Decisions (genuine forks — pick one):**

1. **Resolve the Workable-state premise (P-1, do first).** Confirm how many prod orgs are actually live on Workable. **Recommend:** assume `standalone` default (matches `MVP_DISABLE_WORKABLE=True`) and treat Workable as import-only unless the census says otherwise. This removes dual-run from the critical path.
2. **SMB-first or mid-market-first?** **Recommend SMB-first** (memory: Sam picks simpler models) — requisitions/legal-entity depth deferred to P5; but RBAC is still built fully in P0.5 because P2/P3 depend on it.
3. **Scheduling: build or buy?** **Recommend buy (Nylas/Cronofy)** — per-provider Google Calendar + MS Graph maintenance is high-cost, undifferentiated. Confirm budget.
4. **E-signature: Dropbox Sign or DocuSign?** **Recommend Dropbox Sign** (Workable-native, cheaper, simpler). Confirm — and confirm e-sign is legally sufficient in UAE/KSA or whether wet-sign is required for some entities.
5. **CRM/sourcing — how far?** **Recommend minimal** — do NOT rebuild Workable's ~400M profile DB. Lead with inbound (careers + Google/LinkedIn) + agent engagement; talent pool = thin layer over `candidate_search/` in P5.
6. **Distribution — Indeed partnership now or later?** **Recommend defer** until >50 active roles; rely on Google for Jobs + LinkedIn first.
7. **Unlock the candidate-comms policy?** **Required for MVP** (§8) — a formal LOCKED-decision reversal needing explicit sign-off, *plus* approval to ship suppression/caps/circuit-breaker FIRST.
8. **Custom careers domain at MVP?** Decide the **per-org subdomain scheme in P1** (so JSON-LD/sitemap/Indexing API are designed against the final URLs); custom CNAME can land in P4.

**Risks:**

- **`PIPELINE_STAGES` refactor surface (P0)** — 20+ callsites incl. FE `JobPipelinePage.jsx` + public schemas + the `advanced = Taali hand-back terminal` semantics (scoring freeze, outcome-learning on advance). Extending past `advanced` means reconsidering terminal semantics + migrating rows. Highest-risk single change.
- **RBAC is cross-cutting and gates downstream (P0.5)** — no role column exists; threading authz through every write endpoint and back-defining existing users is L, not M. If it ships thin, P2 approvers + P3 interviewers force a second pass — schedule an explicit RBAC-hardening step at the start of P2 if so.
- **Identity-resolution collisions (P1)** — a native apply colliding with a Workable-synced candidate breaks the unique `(candidate_id, role_id)` constraint or creates duplicates; the merge op + conflict policy must precede the public apply endpoint.
- **New unauth write surface (P1)** — the in-process `RateLimitMiddleware` is useless across replicas; a shared Redis limiter + CAPTCHA-before-`create_application` + cost circuit-breaker are mandatory or bots inflate DB + LLM cost.
- **Deliverability re-incident (P1/P2)** — see §8; suppression list doesn't exist; auto-reject volume is high; the bounce history is real.
- **Human-eval bias (P3)** — subjective scorecards reintroduce the exact risk the AI layer is governed for unless folded into `bias_audit.py` at introduction.
- **GDPR cascade-erase completeness (P5, designed P-1)** — `deleted_at` soft-delete is insufficient; missing a store (Neo4j graph, `claude_call_log` prompts, `cv_match_details` JSON, email logs) is a compliance failure. PII accumulates from P1, so every new store registers an erasure hook at build time.
- **Migration-window token staleness** — `workable_refresh_token` exists but no refresh/monitor job; a silent 401 during any dual-run goes unnoticed.

**Open questions:** Which HRIS targets matter for the MENA/UAE base (BambooHR + HiBob likely)? Is offer e-sign legally sufficient in UAE/KSA, or is wet-sign still required for some entities? What is the genuine prod Workable footprint (P-1 census)?

---

**Estimate rebaseline summary.** P0 4–6 wks (stage refactor + audit; RBAC split out). P0.5 RBAC 3–4 wks (was understated as part of an M line). P1 9–11 wks (adds identity resolution + anti-abuse + email-safety gate). P2 12–16 wks (offers + e-sign + approvals + HRIS + analytics is 4–5 stateful/integration surfaces). P3 14–18 wks (calendar/tz/blinding/fairness — two large features stacked). P4 6–8 wks. P5 8–12 wks. **MVP (P-1→P2): ~7–9 months. Credible mid-market ATS: ~16–20 months.**

**Key files this roadmap touches (all absolute under repo root):** `backend/app/domains/assessments_runtime/pipeline_service.py`, `backend/app/models/{candidate_application,candidate,role,application_interview,candidate_application_event,organization,user,share_link,workable_webhook_outbox}.py`, `backend/app/platform/config.py`, `backend/app/core/middleware.py`, `backend/app/domains/public_api/router.py`, `backend/app/domains/billing_webhooks/webhook_routes.py`, `backend/app/actions/create_application.py`, `backend/app/services/document_service.py`, `backend/app/cv_parsing/runner.py`, `backend/app/components/notifications/{email_client,templates,tasks}.py`, `backend/app/services/workable_op_runner.py`, `backend/app/cv_matching/holistic.py`, `backend/app/decision_policy/{engine,bias_audit}.py`, `backend/app/domains/identity_access/organization_routes.py`, `frontend/src/app/routing.js`, `frontend/src/features/jobs/{jobSpecFormatting.jsx,JobPipelinePage.jsx}`.
