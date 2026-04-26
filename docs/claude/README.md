# Claude API Integrations

This is the source-of-truth map for every Claude touchpoint in TAALI. Keep this file updated whenever a new generated surface is added, especially when a frontend button or automated backend job causes model output to be shown to candidates, recruiters, or interviewers.

## Canonical Owners

| Area | Owner file | Responsibility |
| --- | --- | --- |
| Claude API wrapper | `backend/app/components/integrations/claude/service.py` | Chat, code-quality analysis, prompt-session analysis, model fallback wrapper. |
| Claude model fallback | `backend/app/components/integrations/claude/model_fallback.py` | Deterministic Haiku fallback chain and model-not-found detection. |
| Claude budget accounting | `backend/app/components/assessments/claude_budget.py` | Token/cost estimates and candidate-safe budget payloads. |
| Claude CLI terminal runtime | `backend/app/components/assessments/terminal_runtime.py` | Candidate shell environment, API-key resolution, CLI command wrapper. |
| Candidate REST Claude route | `backend/app/domains/assessments_runtime/candidate_claude_routes.py` | Token-authenticated fallback chat route, repo context packing, response sanitation. |
| Candidate terminal websocket | `backend/app/domains/assessments_runtime/candidate_terminal_routes.py` | Websocket protocol for Claude CLI prompts and terminal usage events. |
| CV/job fit scoring | `backend/app/services/fit_matching_service.py` | Single Claude call for CV vs role fit, followed by deterministic normalization. |
| Interview focus generation | `backend/app/services/interview_focus_service.py` | Claude-generated recruiter screening prompts from role job specs. |

## Generated Surface Registry

| Generated surface | Claude API used | Trigger | Backend entrypoint | Frontend entrypoint | Stored/generated fields | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Candidate "Ask Claude" chat | Yes, REST fallback | Candidate sends a prompt when terminal mode is unavailable or disabled | `POST /api/v1/assessments/{assessment_id}/claude` in `candidate_claude_routes.py` | `assessments.claude()` in `frontend/src/shared/api/assessmentsClient.js`; workspace handling in `frontend/src/features/assessment_runtime/AssessmentPageContent.jsx` | `Assessment.ai_prompts`, `Assessment.total_input_tokens`, `Assessment.total_output_tokens`, timeline events `ai_prompt` and `first_prompt`, `claude_budget` response | Response is sanitized to remove internal tool/XML-style tags before display. |
| Candidate Claude CLI terminal | Yes, through Claude CLI | Candidate sends `claude_prompt` over assessment websocket or uses terminal wrapper | `candidate_terminal_routes.py`; runtime setup in `terminal_runtime.py` | `assessments.terminalWsUrl()` and terminal/chat UI in `AssessmentPageContent.jsx`, `AssessmentTerminal.jsx`, `AssessmentWorkspace.jsx` | Prompt records, terminal transcript events, token usage, `claude_budget` websocket payloads | The candidate sees a guarded shell. API key is resolved from organization custom key first, then environment key. |
| Code quality analysis on submission | Yes when `MVP_DISABLE_CLAUDE_SCORING=false` | Candidate submits assessment | `run_submission_scoring()` in `backend/app/components/assessments/submission_runtime.py` via `ClaudeService.analyze_code_quality()` | Submission UI in `AssessmentPageContent.jsx` | Assessment result scores and prompt analytics | If MVP Claude scoring is disabled, heuristic scoring is used instead. |
| Prompt-session analysis on submission | Yes when `MVP_DISABLE_CLAUDE_SCORING=false` | Candidate submits assessment | `run_submission_scoring()` via `ClaudeService.analyze_prompt_session()` | Submission UI in `AssessmentPageContent.jsx` | Dimension scores, per-prompt scores, fraud flags | Called once per submission, not once per prompt. |
| CV/job fit for completed assessment | Yes | Candidate submits assessment with CV text and role/job spec present | `run_submission_scoring()` calls `calculate_cv_job_match_sync()` | Candidate report views consume the resulting assessment/application model | `Assessment.cv_job_match_score`, `Assessment.cv_job_match_details`, prompt analytics CV match fields | Fit scoring uses the scoring model and includes `_claude_usage` in details when available. |
| CV/job fit for applications | Yes | Recruiter uploads CV, clicks "Generate TAALI CV AI", or batch scoring runs | `_compute_cv_match_for_application()` in `applications_routes.py`; batch role scoring in `applications_routes.py`; Workable sync helper in `sync_service.py` | `roles.generateTaaliCvAi()` in `frontend/src/shared/api/rolesClient.js`; Candidates and Jobs pages | `CandidateApplication.cv_match_score`, `cv_match_details`, `cv_match_scored_at`, score cache fields | Deterministic normalizers convert model output into a stable 0-100 role-fit signal. |
| Role interview focus | Yes | Job spec upload or recruiter clicks regenerate | `upload_role_job_spec()` and `regenerate_interview_focus()` in `roles_management_routes.py` | `roles.regenerateInterviewFocus()` and `RoleSummaryHeader` / Candidates role UI | `Role.interview_focus`, `Role.interview_focus_generated_at`, `screening_pack_template`, `tech_interview_pack_template` | Generated from job spec. If generation fails, the route returns `interview_focus_error` and does not silently invent Claude output. |
| Candidate feedback report | No direct Claude call currently | Recruiter finalizes candidate feedback | `finalize_candidate_feedback()` in `recruiter_reporting_routes.py`; builder in `candidate_feedback_engine.py` | `assessments.finalizeCandidateFeedback()` and `CandidateEvaluateTab.jsx`; candidate feedback route | `Assessment.candidate_feedback_json`, `candidate_feedback_generated_at`, `candidate_feedback_ready`, email sent timestamp | Automatically generated from stored scores/evidence using deterministic rules. It should be labelled TAALI-generated, not Claude-generated. |
| Interview debrief / prep pack | No direct Claude call currently | Recruiter generates candidate or application interview guide | `generate_interview_debrief()` and `generate_application_interview_debrief()`; builder in `candidate_feedback_engine.py` | `assessments.generateInterviewDebrief()`, `roles.generateApplicationInterviewDebrief()`, `CandidateDetailPageContent.jsx` | `Assessment.interview_debrief_json`, `interview_debrief_generated_at`; application route returns generated payload without persisting | Deterministic assembly from role fit, assessment evidence, role focus, and Fireflies/interview context. |
| AI evaluation suggestions | No active provider | Feature flag `AI_ASSISTED_EVAL_ENABLED=true` and recruiter requests suggestions | `ai_eval_suggestions()` calls `generate_ai_suggestions()` | `assessments.aiEvalSuggestions()` and candidate evaluate UI | None unless a provider is implemented | Hard-disabled by `backend/app/services/ai_assisted_evaluator.py`; placeholder output is intentionally forbidden. |

## Frontend Display Rules

- Candidate-facing Claude output must come from the assessment runtime only: `AssessmentPageContent.jsx`, `AssessmentWorkspace.jsx`, `AssessmentTerminal.jsx`, and `ClaudeChat.jsx`.
- Recruiter-facing generated content must show whether it is generated from Claude output, deterministic TAALI rules, Workable data, Fireflies data, or recruiter notes.
- Interviewer share mode must not expose internal-only Claude usage metadata, prompt logs, CV text, or notes. Use existing `data-internal-only` guards on canonical candidate report surfaces.
- Demo/showcase routes may use deterministic fixtures, but should not call live Claude provisioning or live demo start APIs.

## Backend Rules For New Claude Touchpoints

1. Add the touchpoint to the registry above before shipping.
2. Use `candidate_models_for()` for model fallback unless a product requirement explicitly needs a non-Haiku model.
3. Include token usage where Anthropic returns it. Use `build_claude_budget_snapshot()` for candidate-safe budget payloads.
4. Store raw model output only when a reviewer needs auditability. Prefer normalized, bounded fields for product UI.
5. Put generated payload timestamps in `*_generated_at` fields or payload keys.
6. Keep model failures non-destructive: preserve existing recruiter/candidate data and return a clear error or skip reason.
7. Never send secrets, JWTs, assessment tokens, or Workable credentials into model prompts.

## Data Sent To Claude

| Flow | Data sent | Data deliberately not sent |
| --- | --- | --- |
| Candidate REST chat | Task scenario/description, repo tree, selected file, bounded repo excerpts, editor snapshot, recent conversation history | JWTs, assessment token, organization secrets, full unbounded repo, Workable credentials |
| Claude CLI terminal | Candidate repo workspace and candidate prompt through CLI environment | Recruiter-only report fields, Workable credentials, JWTs |
| Submission analysis | Final code, bounded prompt/session summaries, task description | Full browser history, recruiter notes, interviewer notes |
| CV/job fit | Truncated CV text, truncated job spec, optional recruiter requirements | Workable access token, recruiter notes, unrelated candidate applications |
| Interview focus | Truncated job spec | Candidate CVs, application notes, Workable credentials |

## Test Map

Run these when changing Claude-adjacent code:

```bash
cd frontend
npm test -- --run src/components/assessment/AssessmentPage.test.jsx src/test/CandidateDetail.test.jsx src/features/jobs/JobPipelinePage.test.jsx
npm run typecheck
npm run lint:ui
```

```bash
cd backend
pytest backend/tests/components/assessments/test_assessment_terminal.py backend/tests/components/assessments/test_terminal_chat_bridge.py backend/tests/test_api_assessments.py backend/tests/test_api_roles.py backend/tests/test_unit_claude_model_fallback.py
```

For production smoke checks, use the dedicated scripts/docs in `docs/DEPLOYMENT.md`; do not mix production smoke tests into the default local suite.
