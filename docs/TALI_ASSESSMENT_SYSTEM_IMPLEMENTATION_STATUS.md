# Tali Assessment System — Implementation Status

This document checks the codebase against the goal and requirements in your spec (CURSOR_IMPLEMENTATION_SPEC.md + product decisions).

---

## Summary

| Area | Status | Notes |
|------|--------|--------|
| **A) Task loading + JSON** | ✅ Mostly done | Loader exists; seed uses `tasks/` but not loader; weights validated in loader |
| **B) GitHub repo management** | ⚠️ Partial | Mock mode full; production GitHub API deferred |
| **C) Assessment runtime + auto-submit** | ✅ Done | Timer, clone, git evidence, auto-commit/push, `completed_due_to_timeout` |
| **D) Scoring + evaluation** | ⚠️ Partial | Automated scoring + git artifacts stored; no manual rubric UI; no EvaluationResult model |
| **E) Candidate rubric visibility** | ✅ Done | Categories + weights only; criteria never sent |
| **F) AI-assisted evaluation** | ✅ Scaffold done | Feature-flagged endpoint + stub + UI button |
| **G) Manual evaluator UI** | ❌ Not done | No excellent/good/poor per category; no git diff/commits in UI |
| **H) Tests + dev harness** | ✅ Mostly done | Task loader, repo service, auto-submit, eval score; mock GitHub |

---

## A) Task loading + JSON interpretation

| Requirement | Status | Location |
|-------------|--------|----------|
| Task loader reads from `tasks/` | ✅ | `backend/app/services/task_spec_loader.py`: `load_task_specs(tasks_dir)` |
| Validate weights sum to 1.0 (±tolerance) | ✅ | `validate_rubric_weights()`, `RUBRIC_WEIGHT_TOLERANCE = 1e-3` |
| evaluation_rubric = source of truth (category keys, weights, criteria) | ✅ | Task model + schema; criteria excluded from candidate view |
| Criteria never shown to candidate | ✅ | `candidate_rubric_view()` returns only `{category, weight}`; start payload has `rubric_categories`, `evaluation_rubric: None` |
| expected_insights / expected_fixes / valid_solutions evaluator-only | ✅ | In task JSON / extra_data; not in candidate payload |

**Gap:** `scripts/seed_tasks_db.py` reads `tasks/*.json` directly and does **not** call `load_task_specs()` or `validate_task_spec()`. Rubric weight sum is not validated at seed time. Only one task JSON found in repo: `tasks/data_eng_c_backfill_schema.json` (spec also references ai_eng_a_prompt_cache, ai_eng_b_llm_gateway, etc.—may live elsewhere or be added later).

---

## B) GitHub repository management

| Requirement | Status | Location |
|-------------|--------|----------|
| Per-task private GitHub repo (e.g. tali-assessments) | ⚠️ | `AssessmentRepositoryService`; production path deferred |
| Repo name = task_id (or repo_structure.name) | ✅ | `_repo_name(task)` uses task_key / task_id |
| Repo contents = task.repo_structure.files | ✅ | `_repo_files(task)` |
| createTemplateRepo(task) | ✅ | Creates repo (mock) or returns URL (prod) |
| createAssessmentBranch(taskId, assessmentId) | ✅ | Branch `assessment/{assessment_id}`; returns BranchContext (repo_url, branch_name, clone_command) |
| Branch name = assessment/{assessment_id} | ✅ | |
| Preserve branch after completion | ✅ | No delete in code |
| Clone command pinned to branch | ✅ | In BranchContext and start payload |
| GITHUB_TOKEN, GITHUB_ORG | ✅ | Config / env |
| archiveAssessment(assessmentId) | ✅ | Stub returns `{archived: true}` |
| If branch exists, safe suffix | ✅ | Mock mode: `assessment/{id}-1`, etc. |

**Gap:** Production GitHub API is not implemented. Comment in code: “Production GitHub API path intentionally deferred.” Mock mode (`GITHUB_MOCK_MODE=true`) is full; real repo creation/push is not. Dev harness: `GITHUB_MOCK_MODE` + `GITHUB_MOCK_ROOT` for local/mock repos.

---

## C) Assessment runtime + timer + auto-submit

| Requirement | Status | Location |
|-------------|--------|----------|
| Create assessment record; ensure template repo; create assessment branch | ✅ | `start_or_resume_assessment` in `backend/app/components/assessments/service.py` |
| Clone repo / materialize task repo in sandbox | ✅ | `_clone_assessment_branch_into_workspace` or `_materialize_task_repository` |
| Display scenario, rubric category names + weights only, time remaining | ✅ | Start payload: scenario, role, repo_structure, rubric_categories, clone_command; AssessmentPage: “How you'll be assessed” + countdown |
| Capture all assistant + user messages (chat log) | ✅ | `assessment.ai_prompts` |
| Track elapsed time, prompt count, system events | ✅ | Timer, prompts, git evidence at submit/timeout |
| Timeout: freeze UI, gather git evidence | ✅ | `_collect_git_evidence_from_sandbox`: head_sha, status_porcelain, diff_main, diff_staged, commits |
| Timeout: if uncommitted → git add, commit “auto-submit: time expired”, push | ✅ | `_auto_submit_on_timeout` |
| Save finalRepoState (HEAD SHA), completed_due_to_timeout = true | ✅ | `assessment.final_repo_state`, `assessment.completed_due_to_timeout` |
| Manual submit: same git flow, commit “submit: candidate”, completed_due_to_timeout = false | ✅ | In submit flow; commit message differs |

---

## D) Scoring + evaluation system

| Requirement | Status | Location |
|-------------|--------|----------|
| Store/display PR-style diff, commits, test results, final HEAD | ✅ Backend | `git_evidence` (diff_main, diff_staged, commits, head_sha) and test results stored and returned in assessment detail |
| EvaluationResult model (categoryScores, overallScore, evidence[], etc.) | ❌ | No separate model; scores live in assessment (score_breakdown, etc.) |
| Rubric-driven (excellent/good/poor per category with weights) | ✅ Logic | `evaluation_service.calculate_weighted_rubric_score` (poor=1, good=2, excellent=3) |
| Manual evaluator UI: candidate identity, rubric categories, select excellent/good/poor, evidence notes, chat + git diff/commits | ❌ | Recruiter sees identity and automated categoryScores; no UI to set manual rubric grades or evidence; git_evidence not shown in UI |
| Overall score = weighted average (excellent/good/poor) | ✅ | `calculate_weighted_rubric_score` |
| AI-assisted evaluation (V2) | ✅ Scaffold | See F below |

**Gap:** Manual evaluation is not implemented as specified: no UI to choose excellent/good/poor per rubric category, no required evidence notes, and no display of chat log alongside git diff/commits. Backend returns `git_evidence` but the frontend does not render it.

---

## E) Candidate-facing rubric visibility

| Requirement | Status | Location |
|-------------|--------|----------|
| “How you'll be assessed”: category display name + weight % | ✅ | AssessmentPage.jsx: “How you'll be assessed”, list of category + Math.round(weight*100)% |
| Do NOT show criteria text | ✅ | candidate_rubric_view excludes criteria; API sends only rubric_categories |
| Do NOT show expected_insights/expected_fixes/valid_solutions | ✅ | Not in start payload |

---

## F) AI-assisted evaluation (V2 scaffolding)

| Requirement | Status | Location |
|-------------|--------|----------|
| Feature flag AI_ASSISTED_EVAL_ENABLED | ✅ | `backend/app/platform/config.py` |
| POST /api/v1/assessments/{id}/ai-eval-suggestions | ✅ | `backend/app/api/v1/assessments.py` |
| Stub: input chat + rubric + git + tests → suggested scores + evidence | ✅ | `backend/app/services/ai_assisted_evaluator.py`: `generate_ai_suggestions(payload)` |
| UI: “Generate AI suggestions” when enabled | ✅ | CandidateDetailPage.jsx: `VITE_AI_ASSISTED_EVAL_ENABLED` |
| Message: AI suggests; human final | ✅ | Stub message in response and UI |

---

## G) Manual evaluator UI (spec)

Spec asks for:

- Recruiter sees candidate identity ✅ (current flow is non-blind).
- Shows rubric categories and allows selecting **excellent/good/poor** ❌ (only automated scores shown).
- Requires **evidence notes** (at least one snippet per category) ❌.
- Shows **chat log alongside git diff/commits/tests** for evidence ❌ (chat/timeline exist; git_evidence not displayed).

So the **manual evaluator UI** as described (rubric dropdowns + evidence + git artifacts in UI) is **not implemented**.

---

## H) Testing + acceptance

| Requirement | Status | Location |
|-------------|--------|----------|
| Task loader validates rubric weights sum ~1.0 | ✅ | test_unit_task_spec_loader.py |
| Repo manager creates repo + branch names correctly | ✅ | test_unit_assessment_repository_service.py |
| Auto-submit commits uncommitted changes and pushes | ✅ | test_assessment_actions.py: test_execute_auto_submits_when_time_expires |
| Evaluation score calculation correct | ✅ | test_unit_evaluation_service.py |
| Candidate UI does not leak rubric criteria | ✅ | test_unit_task_spec_loader: test_candidate_rubric_view_excludes_criteria; AssessmentPage.test.jsx: “How you'll be assessed” |
| Dev harness / mock GitHub | ✅ | GITHUB_MOCK_MODE, test_unit_assessment_repository_service |

---

## Recommended next steps (to fully match spec)

1. **Manual evaluator UI**
   - Add a section (e.g. in CandidateDetailPage or a dedicated “Evaluate” view) that:
     - Shows assessment `evaluation_rubric` categories.
     - For each category: dropdown or buttons for excellent/good/poor.
     - Evidence field(s) (e.g. one required note/snippet per category).
     - Renders `git_evidence` (diff_main, commits, head_sha) and chat log (existing prompts/timeline) so evaluator can pick evidence.
   - Add API to persist manual rubric scores and evidence (e.g. PATCH assessment or a small evaluation_results table).

2. **Display git evidence in recruiter UI**
   - In candidate/assessment detail, show `git_evidence.diff_main`, `git_evidence.commits`, `git_evidence.head_sha` (e.g. in a “Code / Git” or “Evidence” tab). Data is already in the API response (`assessment_detail` includes `git_evidence`).

3. **Optional: EvaluationResult model**
   - If you want a first-class evaluation artifact: add model with categoryScores (score, weight, evidence[]), overallScore, strengths[], improvements[], chatLogId, finalRepoState, completed_due_to_timeout, and wire manual evaluator UI to it.

4. **Seed script and task loader**
   - In `scripts/seed_tasks_db.py`, call `load_task_specs(tasks_dir)` (or validate each JSON with `validate_task_spec`) so rubric weights are validated at seed time. Optionally add more example task JSONs under `tasks/` to match spec (ai_eng_*, data_eng_*, etc.).

5. **Production GitHub**
   - When ready: implement real GitHub API in `AssessmentRepositoryService` (create repo, create branch, push) using Octokit or equivalent and GITHUB_TOKEN/GITHUB_ORG.

---

*Generated from codebase review against the Tali Assessment System spec. Last checked: 2026-02-13.*
