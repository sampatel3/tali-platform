# CV Matching v3.0 — Cutover Procedure

> Companion to `docs/cv_matching_audit.md`. Read that first for the legacy
> system map. This doc covers turning the new pipeline on, monitoring it,
> and rolling back.

## TL;DR

The new pipeline ships behind `USE_CV_MATCH_V3` (default **off**). When
the flag is on, every score request inside `cv_score_orchestrator` routes
through `app.cv_matching.runner.run_cv_match` instead of the legacy v3/v4
calls in `fit_matching_service`. Cached results, telemetry, validation,
and aggregation all run inside the new module.

## Pre-flip checklist

1. **Migrations applied.** `alembic upgrade head` runs `043_add_cv_match_overrides`.
2. **Eval harness green** on at least 3 real-hire cases:
   ```
   cd backend && python -m app.cv_matching.evals.run_evals
   ```
   Must exit 0. Add real fixtures per `app/cv_matching/evals/fixtures/README.md`
   before promoting to production.
3. **Smoke test passes** in dev:
   ```
   cd backend && python ../scripts/cv_match_smoke_test.py
   ```
   Requires `ANTHROPIC_API_KEY`. Should print `OK` and a non-zero
   `role_fit_score`.
4. **Admin traces endpoint reachable**:
   ```
   curl -H "Authorization: Bearer $TOKEN" \
     https://<host>/api/v1/admin/cv-match/traces?limit=10
   ```
   The `$TOKEN` user must have `is_superuser=True`. Empty list before any
   v3 calls have run is fine.

## Flipping the flag

Per environment, set:

```
USE_CV_MATCH_V3=true
```

Optional (production): set `CV_MATCH_TRACE_LOG_PATH` to a rotated log file
the platform tails so traces survive restarts. Empty string keeps traces
in an in-process ring buffer (fine for staging).

Restart the API + Celery workers. The flag is read at scoring time, so a
worker restart is required to pick up the change.

### Gradual rollout (recommended)

1. **Dev**: flag on, full team. Monitor for a day.
2. **Staging**: flag on, run the full eval harness daily.
3. **Production canary** (one-org override): no current per-org flag mechanism;
   if needed, add a temporary check in `_execute_scoring` keying off
   `application.organization_id`. Remove once we promote globally.
4. **Production global**: flag on. Existing cached v4 rows are unaffected
   (different `prompt_version`, different `cache_key`). New scores hit
   the v3 path; previously-scored applications continue to display their
   v4 result until they're rescored.

## What to monitor

### Telemetry

`GET /api/v1/admin/cv-match/traces?limit=200` returns the most recent
calls. Watch:

| Metric | Healthy | Concern |
|---|---|---|
| `final_status` | mostly `ok` | any sustained `failed` rate |
| `latency_ms` | typically < 4000 | p95 > 8000 |
| `retry_count` | mostly 0 | sustained > 0.10 average |
| `validation_failures` | mostly 0 | > 0.05 average |
| `cache_hit` | rises after warm-up | always `false` (cache broken) |

### Cost

- Token ceiling: 3500 in / 1500 out, enforced in the runner.
- Cache hits cost zero; first-time scores ~$0.003 each on Haiku 4.5.
- A token-ceiling-exceeded run logs `final_status=failed` and
  `error_reason=input_token_ceiling_exceeded:...`. Investigate if this
  fires more than rarely — it means a CV or JD is unusually long.

### Recruiter UI

The frontend reads `application.cv_match_details` directly. The v3 schema
adds fields (`prompt_version`, `model_version`, `trace_id`, `recommendation`,
`role_fit_score`, `injection_suspected`, `suspicious_score`) without
removing v4 fields, so existing UI continues to render. If a v3 score
displays a markedly different value than the v4 score did, that's
expected — the aggregation weights changed (40/60 vs 50/50) and evidence
discipline is stricter.

## Rollback

Single switch:

```
USE_CV_MATCH_V3=false
```

Restart workers. New score requests immediately route through the legacy
path. Cached v3 results stay in `cv_score_cache` (different prompt_version,
no collision) and become inert until the flag is flipped on again.

`cv_match_overrides` rows are append-only and harmless to leave in place.

If the rollback is permanent, delete migration 043 (`alembic downgrade
042_drop_recruiter_workflow_v2_enabled`) — but only after confirming no
recruiter override rows exist that you'd want to preserve.

## Known limitations

1. **No TTL eviction on `cv_score_cache`.** The handover declares 30 days.
   Existing rows are immutable. Add an LRU sweep when storage pressure
   grows (today's cache is small enough to not matter).
2. **No per-org rollout dial.** The flag is global. If we need per-org
   gating during cutover, add it inline in `_execute_scoring` and remove
   when the rollout is complete.
3. **Eval harness is not in CI.** Per the handover, run on prompt-version
   changes only. Adding it as a nightly job is a future improvement.
4. **Frontend override UI is stub-only.** The endpoint exists; the
   recruiter-facing UI is not built. See
   `frontend/src/features/candidates/api/cvMatchOverride.js`.

## After cutover

When v3 is the steady-state path, plan a separate change to:
- Remove the `disable_claude_scoring` legacy flag if no longer used.
- Delete `fit_matching_service.calculate_cv_job_match_*` and the legacy
  `cv_match_v4` prompt definitions.
- Drop the `_execute_scoring` legacy branch from
  `cv_score_orchestrator.py`.

Do **not** do these things at flip time. Keep the legacy path live for at
least one full release cycle so rollback remains a one-line change.
