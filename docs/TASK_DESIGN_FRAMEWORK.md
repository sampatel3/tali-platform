# Task Design Framework

## Purpose

TAALI assessment tasks are product assets. They must be complete, executable in a clean workspace, traceable to a real hiring signal, and stable enough for repeated human evaluation.

## Canonical Catalog

- Canonical task directory: `backend/tasks`
- Active catalog size: exactly 2 backend-authored template tasks
- Current canonical tasks:
  - `ai_eng_genai_production_readiness`
  - `data_eng_aws_glue_pipeline_recovery`

Top-level `tasks/` is retired and must not be used as a second source of truth.

## Design Principles

- Single source of truth: one catalog directory, one loader, one seed path, one runtime interpretation.
- Role traceability: every rubric dimension must map to an explicit requirement from the live job spec.
- Runtime completeness: each task must bootstrap and collect tests in a clean workspace without ambient packages, credentials, or hidden setup.
- Candidate fairness: all success-critical context must be visible in the scenario, repo files, and candidate-visible task payload.
- Evaluator completeness: each task must include role-specific rubric criteria, strong-positive signals, red flags, and scoring hints.
- Deliberate failure shape: baseline repos may fail tests, but only through meaningful logic failures, never missing dependencies or import-time crashes.
- Backend-managed authoring: product tasks are authored in JSON under `backend/tasks`, not through the CRUD UI.

## Required Spec Contract

Every canonical task JSON must include:

- `task_id`
- `name`
- `role`
- `duration_minutes`
- `calibration_prompt`
- `scenario`
- `repo_structure`
- `evaluation_rubric`
- `expected_candidate_journey`
- `interviewer_signals`
- `scoring_hints`
- `test_runner`
- `workspace_bootstrap`
- `role_alignment`
- `human_testing_checklist`

## Runtime Contract

- `workspace_bootstrap` runs before a candidate session starts.
- Bootstrap failure blocks the assessment when `must_succeed=true`.
- Candidate payloads must exclude evaluator-only rubric detail.
- Recruiter/task-management payloads must retain the full rubric.

## Validation Gates

Use `python3 scripts/validate_task_specs.py`.

A task is not ready unless all of the following are true:

- schema validation passes
- catalog count is exactly 2
- bootstrap succeeds
- tests collect successfully
- no missing dependency or import-time failures occur
- baseline failures are meaningful

## Role Alignment Workflow

- Export the live role spec for the target org/user.
- Commit only sanitized requirement mappings, not raw private job-spec text.
- Update `role_alignment` with the exact source role name and identifier.
- Mark `human_testing_checklist.rubric_matches_role=true` only after live alignment is complete.

## Readiness For Human Testing

A task is ready for human pilot testing when:

- automated validation passes
- canonical seeding produces exactly the two intended template tasks
- local dry-run notes exist
- baseline failure shape is intentional
- role alignment is confirmed against the live target role
