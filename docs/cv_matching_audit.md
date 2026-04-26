# CV Matching — Current State Audit

> Phase 1 deliverable for the production-grade CV matching upgrade. See
> `cv_matching_handover.zip` (CLAUDE_CODE_TASK.md) for the upgrade plan and
> `docs/cv_matching_cutover.md` for the cutover procedure.

This document captures the system as it exists today, before the
`cv_match_v3.0` upgrade lands. It exists so future readers (or rollback
operators) can reason about the legacy path without re-tracing the code.

---

## 1. Entry points and call paths

### Trigger sites

| File | Line | Trigger |
|---|---|---|
| `backend/app/domains/assessments_runtime/applications_routes.py` | 725 | Application created from Workable webhook → `enqueue_score(force=True)` |
| `backend/app/domains/assessments_runtime/applications_routes.py` | 1579 | Recruiter uploads CV → `enqueue_score(force=True)` |
| `backend/app/domains/assessments_runtime/applications_routes.py` | 1648 | Recruiter clicks "Generate TAALI CV-AI" → `enqueue_score(force=True)` |

### Orchestration

| File | Function | Purpose |
|---|---|---|
| `backend/app/services/cv_score_orchestrator.py` | `enqueue_score(db, app, force)` | Gateway. Creates `CvScoreJob` row, dispatches Celery task or runs inline. |
| `backend/app/services/cv_score_orchestrator.py` | `compute_cache_key(...)` | SHA256 over (cv_text, spec_description, spec_requirements, criteria, prompt_version, model). |
| `backend/app/tasks/scoring_tasks.py` | `score_application_job` | Celery task; calls `calculate_cv_job_match_v4_sync` (or v3 fallback). |
| `backend/app/tasks/scoring_tasks.py` | `batch_score_role` | Fan-out for re-scoring all applications on a role. |

### LLM calls

| File | Function | Prompt version |
|---|---|---|
| `backend/app/services/fit_matching_service.py` | `calculate_cv_job_match` (async) | `cv_fit_v3_evidence_enriched` (free-text JD; legacy) |
| `backend/app/services/fit_matching_service.py` | `calculate_cv_job_match_sync` | wrapper over the v3 async fn |
| `backend/app/services/fit_matching_service.py` | `calculate_cv_job_match_v4_sync` | `cv_match_v4` (structured criteria; current default) |

> The new module's prompt registers as **`cv_match_v3.0`** — a third, stricter
> version distinct from the two above. Naming is unfortunate (the legacy v3 is
> `cv_fit_v3_evidence_enriched`); the cache key embeds the version string so
> there is no collision in storage.

### ASCII sequence diagram (current default flow, v4)

```
┌──────────────┐   POST /candidates/.../cv-upload
│  Recruiter   │ ──────────────────────────────────────────┐
└──────────────┘                                           │
                                                           ▼
                                    ┌──────────────────────────────────────┐
                                    │ applications_routes.py               │
                                    │   _trigger_cv_match_for_application  │
                                    └──────────────────────────────────────┘
                                                           │ enqueue_score
                                                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ cv_score_orchestrator.enqueue_score                                      │
│  • create CvScoreJob (status=pending)                                    │
│  • if MVP_DISABLE_CELERY: run inline                                     │
│  • else: dispatch Celery task                                            │
└──────────────────────────────────────────────────────────────────────────┘
                                                           │
                                                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ score_application_job (Celery task or inline)                            │
│  1. Load application + role                                              │
│  2. compute_cache_key(cv, spec, criteria, prompt_version, model)         │
│  3. Lookup cv_score_cache by key                                         │
│     ├─ HIT  → copy result, mark job.cache_hit=hit                        │
│     └─ MISS → call calculate_cv_job_match_v4_sync (or v3 fallback)       │
│                  ├─ Anthropic.messages.create(model, prompt)             │
│                  ├─ JSON parse                                           │
│                  ├─ post-validate quotes (substring + 200ch trim)        │
│                  └─ store_cached_result()                                │
│  4. Persist to candidate_applications.cv_match_{score,details,scored_at} │
│  5. Recompute role_fit_score / TAALI                                     │
│  6. Mark CvScoreJob done (or error)                                      │
└──────────────────────────────────────────────────────────────────────────┘
                                                           │
                                                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Frontend candidatesUiUtils.getPrimaryScorePayload                        │
│   priority: pre_screen > taali > cv_match                                │
│   cv_match_details surfaces summary, requirements_assessment, concerns   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Input contract

| Input | Type | Source |
|---|---|---|
| `cv_text` | str | `candidate_applications.cv_text` (per-application; survives Workable refetch) |
| `spec.description` | str | `roles.job_spec_description` |
| `spec.requirements` | str | `roles.job_spec_requirements` |
| `criteria` | list[`RoleCriterion`] | `role_criteria` table; recruiter-managed |
| `additional_requirements` | str (free-text) | `roles.additional_requirements` (legacy v3 only; v4 uses structured `criteria`) |

`RoleCriterion` shape (`backend/app/models/role_criterion.py`):

| Field | Type | Notes |
|---|---|---|
| id | int | PK |
| role_id | int | FK |
| text | str | requirement text |
| must_have | bool | priority flag |
| source | str | `recruiter | derived_from_spec | recruiter_constraint` |
| ordering | int | display order |
| weight | float | aggregation weight (legacy; not used by v4 today) |

Criteria are serialized to a stable list of dicts via
`cv_score_orchestrator._criteria_payload()` for both prompt rendering and
cache-key hashing.

---

## 3. Output contract

The LLM returns JSON parsed into a plain Python dict (no Pydantic). The dict
is augmented with derived scores and persisted as `cv_match_details` (JSON
column) plus a top-level `cv_match_score` (float).

### v4 (`cv_match_v4`) — current default

```jsonc
{
  "overall_match_score":          0-100,    // LLM-produced
  "skills_match_score":           0-100,    // LLM-produced
  "experience_relevance_score":   0-100,    // LLM-produced
  "requirements_match_score":     0-100,    // LLM-produced
  "recommendation":               "strong_yes|yes|lean_no|no",  // LLM-produced
  "summary":                      "...",
  "matching_skills":              ["..."],
  "missing_skills":               ["..."],
  "experience_highlights":        ["..."],
  "concerns":                     ["..."],
  "requirements_assessment": [
    {
      "criterion_id":            <int>,
      "status":                  "met|partially_met|missing|unknown",
      "confidence":              0.0-1.0,
      "cv_quote":                "<verbatim ≤200 chars or null>",
      "evidence_type":           "explicit|implied|absent|contradicted",
      "blocker":                 false,
      "risk_level":              "low|med|high",
      "screening_recommendation": "advance|borderline|reject",
      "interview_probe":         "..."
    }
  ],
  "scoring_version": "cv_match_v4",
  "model": "claude-3-5-haiku-latest"
}
```

### v3 (`cv_fit_v3_evidence_enriched`) — legacy fallback

Same shape as v4 but `requirements_assessment` entries use `requirement` (str)
rather than `criterion_id` (int), and lack `confidence`, `evidence_type`,
`blocker`, `risk_level`, `screening_recommendation`, `interview_probe`.

### Persistence

- `candidate_applications.cv_match_score` (float, 0-100)
- `candidate_applications.cv_match_details` (JSON; full blob)
- `candidate_applications.cv_match_scored_at` (datetime)
- `candidate_applications.role_fit_score_cache_100` (float; computed via `taali_scoring.compute_role_fit_score`)
- `cv_score_cache.{cache_key, prompt_version, model, score_100, result, hit_count, last_hit_at}` (cache row, immutable once written)
- `cv_score_jobs.{status, cache_key, prompt_version, model, cache_hit, error_message, ...}` (job audit log)

---

## 4. Downstream consumers

### Recruiter UI

| File | Behavior |
|---|---|
| `frontend/src/features/candidates/candidatesUiUtils.js:80-131` | `toCvScore100`, `getPrimaryScorePayload`, `renderPrimaryScoreCell` — score precedence: `pre_screen_score > taali_score > cv_match_score` |
| `frontend/src/scoring/scoringDimensions.ts:43-47` | Metadata for sub-dimensions: `cv_job_match_score`, `skills_match`, `experience_relevance` |

The frontend reads from `application.cv_match_details` directly. The new
v3.0 schema adds fields (`evidence_start_char`, `evidence_end_char`,
`injection_suspected`, `suspicious_score`, `scoring_status`,
`error_reason`, `prompt_version`, `model_version`, `trace_id`) but does not
remove any v4 fields, so the UI continues to work without modification
during cutover.

### TAALI / role_fit aggregation

| File | Function | Formula |
|---|---|---|
| `backend/app/services/taali_scoring.py:36-47` | `compute_role_fit_score(cv_fit, requirements_fit)` | **legacy: `0.50 * cv_fit + 0.50 * requirements_fit`** |
| `backend/app/services/taali_scoring.py` | `compute_taali_score(assessment, role_fit)` | `0.50 * assessment + 0.50 * role_fit` |

The new `cv_match_v3.0` module owns its own role_fit math at **`0.40 *
cv_fit + 0.60 * requirements_fit`** (per `calibration.md`) and does not
disturb the legacy 50/50 path. After Phase 10 cutover, the `taali_scoring`
function will continue to govern legacy paths; the v3.0 runner emits its
own `role_fit_score` directly into `cv_match_details`.

### Feature flags affecting CV matching today

| Flag | Source | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` (empty string) | `backend/app/platform/config.py:37` | `enqueue_score` returns None — no scoring |
| `MVP_DISABLE_CLAUDE_SCORING` | env / `MvpFeatureFlags` | gates Claude calls in legacy paths |
| `MVP_DISABLE_CELERY` | env / `MvpFeatureFlags` | runs scoring inline (used by tests/dev) |
| `CLAUDE_SCORING_BATCH_MODEL` | env | overrides default model for scoring |
| (new) `USE_CV_MATCH_V3` | env (Phase 10) | routes through new module when True |

---

## 5. Existing infrastructure (reused, not rebuilt)

### LLM client

- `backend/app/components/integrations/claude/model_fallback.py` — `candidate_models_for(resolved_model)` returns a primary + Opus/Sonnet fallbacks. **The new module disables fallback** (cost discipline: Haiku-only) and uses `candidate_models_for` only for the resolved model lookup.
- API key: `settings.ANTHROPIC_API_KEY` (singleton; per-org keys explicitly rejected by design — see `memory/anthropic_key_routing.md`).

### Cache

- DB-backed cache: `cv_score_cache` table (`backend/alembic/versions/041_add_cv_score_cache_and_jobs.py`). The new `cache.py` adapter calls the existing `get_cached_result` / `store_cached_result` helpers. Cache key shape differs from v4 (handover specifies `sha256(cv_text + jd_text + json(requirements) + prompt_version + model_version)`); collision-free by content hashing.
- TTL: not implemented; rows are immutable. The handover declares a 30-day target — left as a future cleanup sweep job.

### Logging

- `backend/app/platform/logging.py` — JsonFormatter routed to stdout. The new `telemetry.py` uses logger name `taali.cv_match.trace` so infra can route the trace stream separately if needed (e.g., to a file the admin route reads).

### Database

- SQLAlchemy 2 (`backend/app/platform/database.py`), Alembic migrations (`backend/alembic/versions/`). Latest is `041_add_cv_score_cache_and_jobs.py`. Phase 9's override-table migration becomes `042_*`.

### Auth

- `backend/app/deps.py:get_current_user` — JWT-based, returns user with `organization_id`. Admin gating in the new `/admin/cv-match/traces` route uses an `is_admin` check on the resolved user.

### Tests

- `backend/pytest.ini` — testpaths `tests/`, default `-m "not production"`.
- Existing CV match tests: `test_cv_match_v4.py`, `test_cv_match_v4_golden.py`, `test_cv_score_orchestrator.py`. These remain green throughout the upgrade — the new module ships beside them.

---

## 6. What already exists vs. what's net-new

| Concern | Today | After cv_match_v3.0 lands |
|---|---|---|
| Single LLM call per match | ✅ (v4) | ✅ |
| Caching | ✅ (`cv_score_cache`) | ✅ (adapter, same table) |
| Async orchestration | ✅ (Celery + inline fallback) | ✅ (reused unchanged) |
| Model fallback chain | ✅ (Opus/Sonnet) | ❌ — disabled (Haiku-only by cost discipline) |
| Pydantic schema on LLM output | ❌ | ✅ (`schemas.py`, `extra='forbid'`) |
| Verbatim evidence verification | ⚠️ partial (200-char quote trim, no offset check) | ✅ (substring + offset re-resolution) |
| Retry on validation failure | ❌ | ✅ (1 retry, error fed back into prompt) |
| Token-ceiling enforcement | ❌ | ✅ (3500 in / 1500 out, fail loud) |
| Deterministic aggregation | ⚠️ partial (`taali_scoring`) | ✅ (`aggregation.py`, LLM only emits 2 scores) |
| Structured per-call telemetry | ⚠️ ad-hoc logs | ✅ (`telemetry.py` with PII rules) |
| Admin traces endpoint | ❌ | ✅ (`GET /admin/cv-match/traces`) |
| Prompt-injection scanner | ❌ | ✅ (heuristic + sanity check) |
| Eval harness with golden hires | ❌ | ✅ (`evals/run_evals.py` + placeholder fixture) |
| Recruiter override capture | ❌ | ✅ (`cv_match_overrides` table + endpoint) |
| Feature-flag cutover | ❌ | ✅ (`USE_CV_MATCH_V3`, default off) |

---

## 7. Risks identified during audit

1. **Two existing prompt versions named "v3"-something.** The handover's
   `cv_match_v3.0` is the third. Mitigation: cache key embeds the exact
   version string; the new module's `prompt_version` is `cv_match_v3.0`
   verbatim, never abbreviated.
2. **Legacy 50/50 role_fit weighting** vs. handover 40/60. Resolved by
   isolating the new module's aggregation; legacy `taali_scoring.py` is
   not modified.
3. **`cv_score_cache.result` is JSON, not a typed column.** The new module
   round-trips through Pydantic (`CVMatchOutput.model_dump()` /
   `CVMatchOutput.model_validate(json)`) on every cache read, so a
   schema drift between cache writers gets caught at validation time
   rather than being silently consumed.
4. **No TTL eviction.** Existing cache rows accumulate. The handover
   declares 30-day TTL — deferred to a future sweep job.
5. **Celery-disabled in dev.** Tests use `MVP_DISABLE_CELERY=True` to run
   scoring inline. The new runner is sync; orchestrator integration tests
   must keep this contract.
