# Taali as a Workable Assessments Provider — Technical Spec

**Status:** Proposal / design doc · **Date:** 2026-06-04 · **Owner:** Sam

## What this is

The path to making Taali **selectable from inside Workable** as an assessment you attach to a pipeline stage. The recruiter never leaves Workable: they click "send assessment," Workable POSTs the candidate to Taali, Taali runs its real assessment (E2B sandbox + AI scoring), and Taali pushes the result — score, grade, summary, report PDF — back onto the candidate's Workable timeline.

This is distinct from Taali's **current** Workable integration (OAuth pull of jobs/candidates + decision write-back, where Taali is the surface). The Assessments Provider model is the low-friction, marketplace-native front door. Both can coexist; this doc covers the provider model.

> **Key finding:** Taali already has *every functional piece* this contract needs. The only net-new infrastructure is the inbound machine-to-machine auth — which is the Phase 1 substrate in `PUBLIC_API_BUILD_PLAN.md`. This spec is a Workable-shaped adapter over that substrate.

## The Workable contract (from Workable's partner docs)

Workable's Assessments Provider integration requires the provider (Taali) to expose:

- `GET /tests` → list available assessments.
- `POST /assessments` → create an assessment invitation for a candidate; Workable supplies a `callback_url`.
- *(optional)* `GET /assessments/:id/shared-link` → a short-lived candidate link.

And to push results back by `PUT`-ing to the `callback_url` as status changes: `pending → completed | rejected | expired`. On `completed`, the payload carries `results_url` (mandatory), an `assessment` block (`score`, `grade`, `summary`, `details`, `duration`), and optional `attachments[]`.

Auth is bearer-token based (simpler than the OAuth auth-code flow Taali already does):
- **Workable → Taali:** `Authorization: Bearer <inbound token>` — authenticates Workable's calls to Taali's endpoints.
- **Taali → Workable callback:** a token Workable issues at integration creation, sent on the callback `PUT`.

---

## Mapping Workable ⇄ Taali (it all already exists)

| Workable concept | Taali mechanism | Reference |
|---|---|---|
| `GET /tests` | Task catalog (the canonical assessment tasks) | `backend/app/models/task.py`, `backend/app/services/task_catalog.py`, `GET /api/v1/tasks` |
| `POST /assessments` (candidate name/email/job) | Create Candidate (if new) + Application + Assessment from minimal input | `AssessmentCreate` schema `backend/app/schemas/assessment.py`; `create_assessment()` in `backend/app/domains/assessments_runtime/recruiter_management_routes.py` |
| Candidate link | Token link + invite email; preview/start flow | `secrets.token_urlsafe(32)` on Assessment; `backend/app/domains/integrations_notifications/invite_flow.py`; `/assessments/token/{token}/preview` + `/start` |
| `callback_url` to store | `Assessment.workable_candidate_id`, `workable_job_id` already exist; add `callback_url` | `backend/app/models/assessment.py` |
| `status` pending/completed/expired | `AssessmentStatus` enum (PENDING, IN_PROGRESS, COMPLETED, COMPLETED_DUE_TO_TIMEOUT, EXPIRED) | `backend/app/models/assessment.py` |
| `assessment.score` | `Assessment.final_score` / `taali_score` (0–100) | `backend/app/models/assessment.py` |
| `assessment.grade` (failed/passed/excelled) | Map from Taali verdict/recommendation | see grade map below |
| `assessment.summary` | Recommendation label + evidence summary | `pre_screening_snapshot.py`; serialized in `role_support.py` |
| `assessment.details` | `score_breakdown` / `cv_match_details` dimensions | `backend/app/models/assessment.py`, `backend/app/cv_matching/schemas.py` |
| `results_url` | Share link (`shr_…`, modes, expiry) | `backend/app/domains/share_links/routes.py`, `backend/app/models/share_link.py` |
| `attachments[].url` | **Existing** PDF report (S3-cached) | `GET /applications/{id}/report.pdf`; `build_client_assessment_summary_pdf()` in `backend/app/services/candidate_feedback_engine.py` |
| Result delivery (PUT callback) | New: durable webhook outbox + Workable callback client | mirror `backend/app/brain_feed/outbox.py` |

---

## Auth & onboarding

Each Taali org that turns on the Workable assessment add-on gets:

1. **An inbound Taali API key** (the `PUBLIC_API_BUILD_PLAN.md` `ApiKey`, scoped `assessments:write`, capability-tagged `workable_provider`). Workable presents this as `Authorization: Bearer` when calling Taali's `/tests` and `/assessments`. The key → org resolution gives correct tenant isolation for free.
2. **A stored Workable callback token** — issued by Workable at integration creation, stored per-org alongside the existing `workable_*` columns on `Organization`, used on the callback `PUT`.

Onboarding flow: org enables "Taali assessments" in Workable → token exchange (Taali issues/accepts the inbound key, stores the Workable callback token) → the org's tasks now appear via `GET /tests`.

**Billing is Taali's, not Workable's.** The marketplace is distribution only; the org pays Taali via the existing credits/Stripe metering. (Confirm no rev-share clause in the partner agreement.)

---

## Endpoint specs

### `GET /tests`

Return the org's available assessment tasks (active templates + org-owned tasks).

```json
{ "tests": [ { "id": "ai_eng_genai_production_readiness", "name": "GenAI Production Readiness" } ] }
```

Source: task catalog, filtered to `is_active`. Use `task_key` (stable string) as the `id` so Workable test ids survive numeric-id churn.

### `POST /assessments`

Workable body:

```json
{
  "test_id": "ai_eng_genai_production_readiness",
  "job_shortcode": "GROOV005",
  "job_title": "AI Engineer",
  "callback_url": "https://acme.workable.com/assessments/8823119",
  "candidate": { "first_name": "Lakita", "last_name": "Marrero", "email": "lakita@example.com", "phone": "…" }
}
```

Taali handling:
1. Resolve **org** from the inbound bearer key.
2. Resolve **task** from `test_id` (catalog lookup).
3. Resolve/auto-provision **role** from `job_shortcode` — `Role` already has a `workable_job_id` field; create a lightweight role keyed to the Workable job on first use, or map to an existing one.
4. Create **Candidate** (if `(email, org)` absent) + **CandidateApplication** + **Assessment** via the existing `create_assessment()` path. Persist `callback_url`, `workable_candidate_id`, `workable_job_id` on the Assessment.
5. Send the candidate their link (reuse the invite flow) and/or expose it via the shared-link endpoint below.
6. Immediately `PUT` the callback with `status: "pending"`.

Response: `{ "assessment_id": "<taali_assessment_id>" }`.

### `GET /assessments/:id/shared-link` (optional)

Return a short-lived candidate URL (reuse the assessment token link or a single-view share link):

```json
{ "url": "https://app.taali.ai/assessment/123?token=…", "ttl": "120", "ttl_units": "minutes" }
```

---

## Callback (Taali → Workable) — result push

`PUT {callback_url}` on each state change. Delivered through a **durable webhook outbox** (mirror `brain_feed_outbox`: status/attempts/dedup_key/payload + Celery drain with backoff) so a transient Workable outage never loses a result. Hook the emit where `scored_at` is set in `submission_runtime.py`.

**Completed payload:**

```json
{
  "status": "completed",
  "results_url": "https://taali.ai/share/shr_…",
  "assessment": {
    "score": "82",
    "grade": "excelled",
    "summary": "Strong match. Shipped a working pipeline; clear reasoning, efficient tool use.",
    "details": { "skills_coverage": 88, "skills_depth": 79, "problem_solving": 84 },
    "duration": "00:41:17"
  },
  "attachments": [
    { "description": "Taali Assessment Report", "url": "https://…s3-presigned…/report.pdf" }
  ]
}
```

- `score` ← `Assessment.final_score` (or `taali_score` if exposing the role-blended number — **decision below**).
- `summary` ← live recommendation label + evidence summary.
- `details` ← the canonical scorecard — the 5 dimensions (the 4 Ds + Deliverable; see [`SCORING_SCORECARD.md`](./SCORING_SCORECARD.md)) — plus its evidence from `score_breakdown` / `cv_match_details`.
- `results_url` ← a `client`-mode share link (scrubbed of internal notes).
- `attachments[0].url` ← presigned URL of the existing report PDF.

### Grade mapping (Taali verdict → Workable grade)

Workable grades display as: `failed`→"no", `passed`→"yes", `excelled`→"definitely yes".

| Taali verdict | Workable `grade` |
|---|---|
| STRONG_YES / "Strong match" | `excelled` |
| YES / "Proceed to screening" | `passed` |
| LEAN_NO / "Manual review" | `failed` *(or `passed` — policy choice, see below)* |
| NO / "Below threshold" | `failed` |

### State machine

| Taali state | Workable callback `status` |
|---|---|
| PENDING (created) | `pending` |
| IN_PROGRESS | *(no callback; optional progress note)* |
| COMPLETED / COMPLETED_DUE_TO_TIMEOUT + scored | `completed` |
| EXPIRED (passed `expires_at`, never finished) | `expired` |
| Manually voided / withdrawn | `rejected` |

Emit `expired` from the existing assessment-expiry sweep.

---

## Inbound error handling (Taali's `/tests`, `/assessments`)

Per Workable's spec, return `{ "status": <code>, "message": "<text>" }`:
- `401` missing/invalid token · `400` invalid JSON/field · `422` missing required field · `409` conflict (e.g. duplicate assessment for the same Workable candidate).

---

## What's reused vs. net-new

**Reused (already in production):** task catalog, minimal-input candidate/application/assessment creation, token link + invite, E2B capture→grade, scoring + recommendation + evidence, share links, **PDF report**, metering/billing.

**Net-new (all small, all on the Phase-1 substrate):**
1. Inbound API-key auth scoped to `workable_provider` (= `PUBLIC_API_BUILD_PLAN.md` Phase 1).
2. Three Workable-shaped endpoints (`/tests`, `/assessments`, `/assessments/:id/shared-link`) — thin adapters over existing services.
3. `callback_url` column on Assessment + per-org Workable callback token storage.
4. Grade-mapping function (verdict → failed/passed/excelled).
5. Webhook outbox + Workable callback client (mirror `brain_feed/outbox.py`); register the drain task in `app/tasks/__init__.py`.

---

## Decisions to settle before build

1. **Which number is `score`?** The pure assessment score (`final_score`) or the role-blended `taali_score`? Workable shows one number — pick the one that best represents "how did they do."
2. **LEAN_NO → `passed` or `failed`?** Affects how aggressively Taali's "maybe" reads inside Workable. Recommend `failed` (conservative) but it's a product call.
3. **Role mapping:** auto-provision a Taali role per Workable job, or require the org to map Workable jobs → Taali roles in settings? Auto-provision is lower friction; mapping is more controlled.
4. **Candidate experience:** Taali-hosted assessment link (recommended; reuses the real runtime) vs. any embedded option.
5. **Coexistence with the current OAuth integration** for orgs that run both — make sure a candidate isn't double-handled (provider-created vs. pulled).

## Build order

Ride the `PUBLIC_API_BUILD_PLAN.md` phases: Phase 1 (auth + create-assessment + share-link) unlocks `/tests` + `/assessments`; the webhook outbox (Phase 2) powers the callback push. Then it's the grade map + Workable callback client + partner QA. Estimated net-new work beyond the substrate is small (days, not weeks) because the assessment engine, reporting, and billing already exist.
