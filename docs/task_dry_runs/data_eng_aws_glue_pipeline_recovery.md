# Data Engineer Dry Run

Date: 2026-03-03
Task: `data_eng_aws_glue_pipeline_recovery`

## What I Ran

- Read `README.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, and diagnostics under `diagnostics/`.
- Bootstrapped the repo in a clean temp workspace with the task-declared commands.
- Ran `./.venv/bin/python -m pytest -q --tb=no`.

## Runtime Result

- Bootstrap: passed
- Test collection: passed
- Baseline result: 0 passed, 7 failed
- Missing dependency failures: none
- Import-time cloud failures: none

## Candidate Friction Review

- The incident framing is clear and correctly pushes the candidate toward correctness over performance.
- The updated Bronze-layer and load-method framing now matches the live `AWS Glue Data Engineer` role more closely.
- Adding the explicit note that no AWS credentials or cloud access are required removed the main avoidable ambiguity.
- The failure count is higher than the AI task, but the work still collapses into a small set of core behaviors rather than seven unrelated bugs.

## Failure Shape

The failing tests are meaningful and aligned to the intended Glue-recovery surface:

- Source type mapping does not cover `double` and `integer`.
- Duplicate detection and the quality gate are stubbed.
- Schema planning does not emit missing-column changes.
- Retry deduplication does not keep the latest record.
- Bookmark advancement is still allowed when quality should fail.

## Timebox Assessment

30 minutes is still reasonable because the edits are concentrated in a few small functions. This is at the upper edge of the timebox, but still suitable for a senior data engineer incident-recovery exercise.

## Role Alignment Status

- Live role spec exported from the Deeplight org on 2026-03-03 for `AWS Glue Data Engineer` (`workable:CE038C5C15`).
- Task rubric and alignment metadata were updated against that export.

## Production Smoke

- Production demo start succeeded on 2026-03-03 after workspace permission repair was added to sandbox repo setup.
- Production workspace bootstrap succeeded with all three declared steps.
- Production submit executed the task-specific test runner and recorded the expected baseline result: 0 passed, 7 failed.
- Candidate payload still hid evaluator-only fields (`evaluation_rubric` and `extra_data` were `null` in the start response).
- `.gitignore` was added and reseeded so `.venv` no longer pollutes candidate branch evidence.
