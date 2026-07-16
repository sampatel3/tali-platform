# Taali Assessment System — Implementation Status

Last verified against the codebase: 2026-07-15.

This document describes the current implementation. It replaces the February
2026 checklist, which still described production GitHub support and manual
evaluation as missing after both had shipped.

## Current status

| Area | Status | Implementation |
|---|---|---|
| Task loading and validation | Implemented | `task_spec_loader.py` validates task specs and rubric weights. `scripts/seed_tasks_db.py` uses the same loader and canonical catalog sync. |
| GitHub repository management | Implemented | `AssessmentRepositoryService` creates and synchronizes private template repositories, creates collision-safe assessment branches through the GitHub API, and retains a local mock harness for tests. |
| Assessment runtime | Implemented | Start/resume, timer handling, sandbox materialization, prompt capture, manual submission, timeout submission, git evidence capture, and final repository state are persisted. |
| Automated scoring | Implemented | The canonical five-dimension scorecard and rubric evidence are generated and stored by the assessment scoring pipeline. |
| Manual evaluation | Implemented | Recruiters can record excellent/good/poor grades, required per-category evidence, strengths, improvements, decision, rationale, confidence, and next steps. Draft/submitted lifecycle, optimistic locking, authorship, and bounded history are persisted. |
| Recruiter evidence views | Implemented | The assessment detail UI exposes prompt evidence, timeline, tests, final commit, commits, working-tree state, and diffs in dedicated panels. |
| Candidate rubric privacy | Implemented | Candidate payloads show category names and weights while evaluator criteria and expected solutions remain private. |
| Tests and development harness | Implemented | Unit/API/UI coverage includes task validation, repository creation and branch collisions, submission behavior, rubric privacy, manual evaluation, and recruiter evidence views. |

## Operational requirements

- Production repository operations require `GITHUB_MOCK_MODE=false`, a usable
  `GITHUB_TOKEN`, and the intended `GITHUB_ORG`. Activation/readiness checks
  reject an unusable production repository configuration.
- Mock mode is for local development and tests only. It materializes repositories
  under `GITHUB_MOCK_ROOT` and must not be enabled in production.
- Repository failures fail closed: an assessment is not represented as ready
  when its template or assessment branch could not be created.
- Assessment-specific git artifacts are persisted in `git_evidence` and
  `final_repo_state`; the unused `archive_assessment` helper can remove a remote
  branch only when a caller explicitly supplies that branch.

## Canonical scorecard

Candidate reports use one scorecard: Delegation, Description, Discernment,
Diligence, and Deliverable. Per-task rubric dimensions and heuristic category
scores are supporting evidence under those axes, not competing scorecards.
