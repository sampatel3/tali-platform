# Human Pilot Runbook

Date: 2026-03-03 (canonical set refreshed 2026-07-10; earlier revisions said 2, then 5 tasks)

This runbook is for human pilots of the canonical assessment tasks. The
canonical set is exactly the specs in `backend/tasks/*.json` (10 as of
2026-07-10) — that directory is the source of truth, enforced by
`backend/tests/test_task_spec_contract.py`. Role-specific generated drafts
(org-owned, `extra_data.generated`) are additional to this set and carry
their own automated battle-test report (`scripts/battle_test_drafts.py`).

## Goal

Confirm that real candidate sessions behave correctly in production:

- start succeeds
- workspace bootstrap succeeds
- candidate repo opens with the expected context
- submit runs the task-specific test runner
- evaluator-visible results match the intended failure shape

## Preflight

Confirm every catalog spec still satisfies the design contract, and that
prod's active templates match the catalog (`check_two_task_rollout.py` was
removed in the 2026-07 backend de-bloat; use these instead):

```bash
# Contract: every backend/tasks/*.json validates (rubric sums to 1.0,
# interrogation dim ↔ decision_points, jd_to_signal_map coverage, ...)
cd backend && .venv/bin/python -m pytest tests/test_task_spec_contract.py -q

# Prod: active org-less templates == catalog task_keys
psql "$DATABASE_PUBLIC_URL" -c "SELECT task_key FROM tasks WHERE organization_id IS NULL AND is_active ORDER BY task_key;"
```

Expected preflight state: the SQL list equals the catalog filenames; legacy
templates (pre-catalog ids) stay `is_active = false` — they anchor historical
assessments and must not be deleted.

## Expected Runtime Shape

For the AI task (`ai_eng_genai_production_readiness`):

- start returns `200`
- bootstrap succeeds
- baseline submit shape is `5 passed / 8 total`

For the data task (`data_eng_aws_glue_pipeline_recovery`):

- start returns `200`
- bootstrap succeeds
- baseline submit shape is `0 passed / 7 total`

For the platform-eng AWS, platform-eng Azure, and scrum-master tasks: baseline shapes still need to be captured during their first dry-run — record them here when known.

These are the untouched baseline expectations, not the target candidate outcome.

## Pilot Execution

Use a small first batch:

1. Run one human session per canonical task (5 sessions total).
2. Review outcomes before expanding volume on any one task.

During the session, verify manually:

- candidate instructions are clear without recruiter intervention
- no missing dependency or environment errors appear
- the repo contains the expected task files
- the candidate can edit files and run tests normally

## Live Monitoring

Check funnel health (per-status counts + the first-minutes events shipped
2026-07-10: `preview_viewed`, `runtime_loaded`, `file_opened`, `first_prompt`):

```bash
psql "$DATABASE_PUBLIC_URL" -c "SELECT status, count(*) FROM assessments WHERE created_at > now() - interval '7 days' AND is_voided IS NOT TRUE GROUP BY 1;"
curl -s https://resourceful-adaptation-production.up.railway.app/healthz/github   # {ok:true} or provisioning is down
```

Pull backend logs if a session looks wrong:

```bash
railway logs --service resourceful-adaptation --environment production --lines 200
```

## Stop Conditions

Pause the pilot immediately if any of these appear:

- prod's active org-less templates stop matching `backend/tasks/*.json` (drop OR unexplained growth)
- any bootstrap failure is recorded
- any completed assessment has `tests_total = 0`
- candidate start fails for any canonical task
- evaluator reports rubric mismatch with the role
- candidates hit missing dependency, missing file, or permission errors

## Notes

- Automated smoke submissions can trigger `suspiciously_fast`; ignore that for scripted checks.
- The task repos now include `.gitignore` entries for `.venv`, `.pytest_cache`, and `__pycache__` so bootstrap artifacts do not pollute candidate branch evidence.
- Dry-run evidence is recorded in:
  - [ai_eng_genai_production_readiness.md](/Users/sampatel/tali-platform/docs/task_dry_runs/ai_eng_genai_production_readiness.md)
  - [data_eng_aws_glue_pipeline_recovery.md](/Users/sampatel/tali-platform/docs/task_dry_runs/data_eng_aws_glue_pipeline_recovery.md)
