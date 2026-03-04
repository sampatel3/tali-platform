# Two-Task Human Pilot Runbook

Date: 2026-03-03

This runbook is for the first human pilot of the two canonical assessment tasks:

- `ai_eng_genai_production_readiness`
- `data_eng_aws_glue_pipeline_recovery`

## Goal

Confirm that real candidate sessions behave correctly in production:

- start succeeds
- workspace bootstrap succeeds
- candidate repo opens with the expected context
- submit runs the task-specific test runner
- evaluator-visible results match the intended failure shape

## Preflight

Confirm the catalog is still exactly the two canonical tasks:

```bash
PUBLIC_DB_URL=$(railway variables --service Postgres --json | /Users/sampatel/tali-platform/backend/.venv/bin/python -c "import json,sys; print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])")
DATABASE_PUBLIC_URL="$PUBLIC_DB_URL" /Users/sampatel/tali-platform/backend/.venv/bin/python /Users/sampatel/tali-platform/scripts/check_two_task_rollout.py --since 2026-03-03T07:09:00Z
```

Expected preflight state:

- `active_template_count` is `2`
- `active_task_keys` are the two canonical task keys above
- `alerts` is empty

Use a later `--since` timestamp once the pilot starts if you want the report to show only pilot-era sessions.

## Expected Runtime Shape

For the AI task:

- start returns `200`
- bootstrap succeeds
- baseline submit shape is `5 passed / 8 total`

For the data task:

- start returns `200`
- bootstrap succeeds
- baseline submit shape is `0 passed / 7 total`

These are the untouched baseline expectations, not the target candidate outcome.

## Pilot Execution

Use a small first batch:

1. Run 1 human session on the AI task.
2. Run 1 human session on the data task.
3. Review outcomes before expanding volume.

During the session, verify manually:

- candidate instructions are clear without recruiter intervention
- no missing dependency or environment errors appear
- the repo contains the expected task files
- the candidate can edit files and run tests normally

## Live Monitoring

Check the rollout health summary:

```bash
PUBLIC_DB_URL=$(railway variables --service Postgres --json | /Users/sampatel/tali-platform/backend/.venv/bin/python -c "import json,sys; print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])")
DATABASE_PUBLIC_URL="$PUBLIC_DB_URL" /Users/sampatel/tali-platform/backend/.venv/bin/python /Users/sampatel/tali-platform/scripts/check_two_task_rollout.py --since 2026-03-03T07:09:00Z
```

Pull backend logs if a session looks wrong:

```bash
railway logs --service resourceful-adaptation --environment production --lines 200
```

## Stop Conditions

Pause the pilot immediately if any of these appear:

- active template count is not `2`
- any bootstrap failure is recorded
- any completed assessment has `tests_total = 0`
- candidate start fails for either task
- evaluator reports rubric mismatch with the role
- candidates hit missing dependency, missing file, or permission errors

## Notes

- Automated smoke submissions can trigger `suspiciously_fast`; ignore that for scripted checks.
- The task repos now include `.gitignore` entries for `.venv`, `.pytest_cache`, and `__pycache__` so bootstrap artifacts do not pollute candidate branch evidence.
- Dry-run evidence is recorded in:
  - [ai_eng_genai_production_readiness.md](/Users/sampatel/tali-platform/docs/task_dry_runs/ai_eng_genai_production_readiness.md)
  - [data_eng_aws_glue_pipeline_recovery.md](/Users/sampatel/tali-platform/docs/task_dry_runs/data_eng_aws_glue_pipeline_recovery.md)
