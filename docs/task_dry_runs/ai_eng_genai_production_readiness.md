# AI Engineer Dry Run

Date: 2026-03-03
Task: `ai_eng_genai_production_readiness`

## What I Ran

- Read `README.md`, `RISKS.md`, and `docs/launch_checklist.md`.
- Bootstrapped the repo in a clean temp workspace with the task-declared commands.
- Ran `./.venv/bin/python -m pytest -q --tb=no`.

## Runtime Result

- Bootstrap: passed
- Test collection: passed
- Baseline result: 5 passed, 3 failed
- Missing dependency failures: none
- Import-time provider failures: none

## Candidate Friction Review

- Instructions are clear about launch pressure, safety concerns, and the expected tradeoff between shipping and blocking risk.
- The updated framing now makes grounded customer context an explicit part of the task, which better matches the live `GenAI Engineer` role.
- The repo points the candidate at the right artifacts before code changes.
- Adding the explicit note that no OpenAI credentials or network access are required removed the main avoidable ambiguity.

## Failure Shape

The failing tests are meaningful and cluster around the intended production-readiness work:

- PII is not redacted before prompt construction.
- LLM failures do not fall back to degraded mode.
- High-risk churn actions are not forced into review when human review is enabled.

## Timebox Assessment

30 minutes looks realistic. The work surface is narrow, the docs are specific, and the failures collapse into a few coherent fixes instead of scattered cleanup.

## Role Alignment Status

- Live role spec exported from the Deeplight org on 2026-03-03 for `GenAI Engineer` (`workable:120884740D`).
- Task rubric and alignment metadata were updated against that export.

## Production Smoke

- Production demo start succeeded on 2026-03-03 after workspace permission repair was added to sandbox repo setup.
- Production workspace bootstrap succeeded with all three declared steps.
- Production submit executed the task-specific test runner and recorded the expected baseline result: 5 passed, 3 failed.
- Candidate payload still hid evaluator-only fields (`evaluation_rubric` and `extra_data` were `null` in the start response).
- `.gitignore` was added and reseeded so `.venv` no longer pollutes candidate branch evidence.
