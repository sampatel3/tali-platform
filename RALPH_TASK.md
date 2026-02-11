# RALPH_TASK.md - TALI Platform Production Launch

> **STATUS: ARCHIVED** — This plan has been fully reviewed and closed on 2026-02-11.
> All items were marked complete. See `MVP_PLAN.md` for the current active plan.

---
task: TALI Platform - Production Launch & Prompt Scoring Engine
test_command: "cd backend && python3 -m pytest tests/ -v"
---

**Project**: TALI - AI-Augmented Technical Assessment Platform
**Objective**: Ship a production-grade platform with a multi-signal prompt analytics and scoring engine that measures how engineers work WITH AI tools.
**Repository**: Monorepo -- backend (FastAPI/Railway), frontend (Vite+React/Vercel)

## Tech Stack

- **Backend**: FastAPI, PostgreSQL 15 + SQLAlchemy 2.0 + Alembic, Redis + Celery, JWT (PyJWT), Pydantic
- **Frontend**: Vite 5 + React 18 (JSX), Tailwind CSS (black/white/purple #9D00FF), Hash routing in single App.jsx, Monaco Editor, Axios (src/lib/api.js), Recharts
- **Integrations**: E2B Code Interpreter SDK, Anthropic Claude Sonnet 4, Stripe API, Workable API (OAuth + webhooks), Resend API
- **Deployment**: Railway (backend + Postgres/Redis), Vercel (frontend)

---

## Phase 1 - Strip Demo Mode

Remove all demo scaffolding so the app is production-safe for real users.

### Frontend Demo Removal (frontend/src/App.jsx)

- [x] DemoBanner component and all its usages removed (Dashboard, Candidate Detail, Tasks, Analytics, Settings).
- [x] Pre-filled login credentials (sam@deeplight.ai / demo1234) removed from login page.
- [x] "DEMO MODE" banner removed from login page.
- [x] Demo-mode fallback that navigates to dashboard when API is unreachable removed.
- [x] Mock candidates array fallback removed; proper empty state shown instead ("No assessments yet. Create your first assessment to get started.").
- [x] Hardcoded org name defaults (DeepLight AI, sam@deeplight.ai) replaced with '--' or actual user data from API.

### Backend Configuration (backend/app/core/config.py)

- [x] ASSESSMENT_PRICE_PENCE setting added (default 2500); used in billing.py and stripe_service.py instead of hardcoded 2500.
- [x] ASSESSMENT_EXPIRY_DAYS setting added (default 7); used in assessments.py create_assessment instead of hardcoded timedelta(days=7).
- [x] EMAIL_FROM setting added; used in email_service.py instead of hardcoded sender address.

---

## Phase 2 - Prompt Analytics & Scoring Engine

The core differentiation. Replace naive prompt counting (<=5 prompts = 8/10) with a multi-signal scoring system using 13 AI-evaluated signals + 10 heuristic signals.

### 2A. Expanded Prompt Data Capture (backend/app/api/v1/assessments.py)

- [x] chat_with_claude stores full Claude response text in prompt record (currently discarded).
- [x] chat_with_claude stores input_tokens and output_tokens from Claude usage metadata.
- [x] chat_with_claude stores response_latency_ms (time.time() before/after Claude call).
- [x] chat_with_claude stores code_before snapshot from frontend-provided code_context.
- [x] chat_with_claude stores code_after by backfilling previous prompt's code_after with current code_context.
- [x] chat_with_claude stores word_count and char_count for each prompt.
- [x] chat_with_claude stores time_since_last_prompt_ms (computed from previous prompt timestamp).
- [x] chat_with_claude stores paste_detected flag from frontend metadata.
- [x] chat_with_claude stores browser_focused flag from frontend metadata.
- [x] Prompt record schema documented: {message, response, timestamp, input_tokens, output_tokens, response_latency_ms, code_before, code_after, word_count, char_count, time_since_last_prompt_ms, paste_detected, browser_focused}.

### 2B. AI-Evaluated Scoring Signals (backend/app/services/claude_service.py)

IMPORTANT: All analysis batched into ONE Claude call at submit time. Do NOT call per-prompt.

- [x] ClaudeService.analyze_prompt_session(prompts, task_description) method added.
- [x] analyze_prompt_session accepts full conversation history (all prompts with code snapshots).
- [x] analyze_prompt_session calls Claude once with structured prompt requesting JSON scores.
- [x] Signal: Prompt Clarity (0-10) -- Is each prompt specific, well-structured, and unambiguous?
- [x] Signal: Prompt Specificity (0-10) -- Does it reference specific code lines, error messages, or requirements?
- [x] Signal: Prompt Efficiency (0-10) -- Does it ask for the right level of help (not too broad, not too narrow)?
- [x] Signal: Design Thinking (0-10) -- Does it show understanding of system design, tradeoffs, or architecture?
- [x] Signal: Debugging Strategy (0-10) -- Does it demonstrate systematic debugging (hypothesis, isolation, verification)?
- [x] Signal: Prompt Progression (0-10) -- Do prompts build on each other logically (vs. random/scattered)?
- [x] Signal: Independence (0-10) -- Does candidate attempt before asking? (measured by code changes between prompts)
- [x] Signal: Written Communication (0-10) -- Grammar, structure, professional tone appropriate for client-facing roles.
- [x] Signal: Context Utilization (0-10) -- Does candidate actually USE the AI response in subsequent code/prompts?
- [x] Signal: Error Recovery (0-10) -- When Claude gives wrong/unhelpful answer, does candidate course-correct or blindly follow?
- [x] Signal: Requirement Comprehension (0-10) -- Did they understand the task before prompting? (initial prompt quality vs task brief)
- [x] Signal: Learning Velocity (0-10) -- Do prompts improve during session? (compare first 3 vs last 3 prompt quality)
- [x] Signal: Prompt Fraud Detection -- Detects copy-pasted external text, full solution dumps, or prompt injection attempts (flag + confidence score).
- [x] analyze_prompt_session returns structured dict with all signal scores and per-prompt breakdown.
- [x] Error handling: if Claude call fails, store null scores and log error; do not block submission.

### 2C. Heuristic (Non-AI) Signals (backend/app/services/prompt_analytics.py)

New module. Computed directly in Python, no Claude call needed.

- [x] compute_time_to_first_prompt(assessment) -- seconds from started_at to first prompt timestamp.
- [x] compute_prompt_speed(prompts) -- average time between consecutive prompts in ms.
- [x] compute_prompt_frequency(prompts, assessment_duration) -- total count, prompts per 10-minute window array.
- [x] compute_prompt_length_stats(prompts) -- avg/min/max word count; flags very short (<10 words) or very long (>500 words).
- [x] detect_copy_paste(prompts) -- compare prompt text against common StackOverflow/ChatGPT patterns; combine with paste_detected flags from frontend.
- [x] compute_code_delta(prompts) -- diff size between code_before and code_after per prompt; measures if candidate actually used response.
- [x] compute_self_correction_rate(prompts) -- how often candidate modifies code after prompt vs. using response verbatim.
- [x] compute_token_efficiency(prompts, tests_passed, tests_total) -- total tokens consumed vs. problems solved.
- [x] compute_browser_focus_ratio(assessment) -- % of assessment time browser was in focus (from prompt-level browser_focused flags).
- [x] compute_tab_switch_count(assessment) -- total tab switches recorded.
- [x] All heuristic functions return dict with signal name, value, and optional flag/warning.

### 2D. Calibration Prompt System

- [x] Task model gains calibration_prompt field (Text, nullable) -- the standardized prompt to present first.
- [x] Default calibration prompt: "Ask Claude to help you write a function that reverses a string" (or per-task custom).
- [x] Assessment start flow presents calibration task before main task.
- [x] Calibration interaction stored separately: assessment.calibration_score (Float).
- [x] Calibration score computed via Claude analysis of the single calibration interaction.
- [x] Recruiter dashboard shows calibration score alongside main score for cross-candidate comparison.

### 2E. Composite Scoring Formula (backend/app/api/v1/assessments.py)

- [x] SCORE_WEIGHTS dict defined in config.py with defaults: tests=0.30, code_quality=0.15, prompt_quality=0.15, prompt_efficiency=0.10, independence=0.10, context_utilization=0.05, design_thinking=0.05, debugging_strategy=0.05, written_communication=0.05.
- [x] Task model gains score_weights field (JSON, nullable) to allow per-task override of default weights.
- [x] Task model gains recruiter_weight_preset field (String, nullable) -- options: "solution_focused" (50% tests), "prompt_focused" (50% prompting), "balanced" (default).
- [x] submit_assessment computes final_score as weighted sum of all component scores using task-specific or default weights.
- [x] submit_assessment calls ClaudeService.analyze_prompt_session (batched, single call).
- [x] submit_assessment calls all heuristic signal functions from prompt_analytics.py.
- [x] submit_assessment stores all individual component scores on the Assessment model.
- [x] submit_assessment stores prompt_analytics JSON with full per-prompt scoring breakdown.
- [x] Existing naive AI usage scoring (<=5 prompts = 8/10) replaced entirely.

### 2F. New Database Fields (backend/app/models/assessment.py)

- [x] prompt_quality_score column added (Float, nullable).
- [x] prompt_efficiency_score column added (Float, nullable).
- [x] independence_score column added (Float, nullable).
- [x] context_utilization_score column added (Float, nullable).
- [x] design_thinking_score column added (Float, nullable).
- [x] debugging_strategy_score column added (Float, nullable).
- [x] written_communication_score column added (Float, nullable).
- [x] learning_velocity_score column added (Float, nullable).
- [x] error_recovery_score column added (Float, nullable).
- [x] requirement_comprehension_score column added (Float, nullable).
- [x] calibration_score column added (Float, nullable).
- [x] prompt_fraud_flags column added (JSON, nullable) -- array of {type, confidence, evidence}.
- [x] prompt_analytics column added (JSON, nullable) -- full per-prompt scoring breakdown.
- [x] browser_focus_ratio column added (Float, nullable).
- [x] tab_switch_count column added (Integer, default 0).
- [x] time_to_first_prompt_seconds column added (Integer, nullable).
- [x] code_snapshots column updated to store per-prompt snapshots (array of {prompt_index, code_before, code_after}).
- [x] Alembic migration 003_add_prompt_scoring_fields.py created and reversible.
- [x] Task model: calibration_prompt (Text), score_weights (JSON), recruiter_weight_preset (String) columns added.
- [x] Task model migration included in same or separate Alembic migration.

### 2G. Frontend Prompt Capture (frontend/src/App.jsx -- AssessmentPage)

- [x] Claude message request includes code_context field (current Monaco editor content).
- [x] Paste events tracked on prompt input textarea; paste_detected flag sent with each message.
- [x] Time since last prompt tracked client-side; time_since_last_prompt_ms sent with each message.
- [x] Browser focus/blur events tracked via window focus/blur listeners; browser_focused sent with each message.
- [x] Tab visibility changes tracked using document.visibilityState API with fallback for unsupported browsers.
- [x] Tab switch count maintained in component state; incremented on each visibilitychange to "hidden".
- [x] tab_switch_count sent with submit request.
- [x] All tracking state reset on assessment start.

### 2H. Frontend Score Display (frontend/src/App.jsx -- CandidateDetailPage)

- [x] recharts package added to frontend dependencies.
- [x] AI Usage tab: Radar/spider chart showing all scoring dimensions (clarity, specificity, efficiency, design thinking, debugging, progression, independence, communication, context utilization, error recovery, comprehension, learning velocity).
- [x] AI Usage tab: Per-prompt quality scores shown inline next to each prompt in conversation view.
- [x] AI Usage tab: Fraud flags displayed in red with explanation text if any detected.
- [x] AI Usage tab: Prompt progression timeline as line chart showing quality evolution during assessment.
- [x] AI Usage tab: Summary stats card showing avg prompt quality, total tokens, efficiency rating, time to first prompt.
- [x] AI Usage tab: Calibration score shown with comparison to average across all candidates (requires API endpoint).
- [x] AI Usage tab: Browser focus indicator -- warning banner if <80% focus time.
- [x] AI Usage tab: Learning velocity indicator (improving / declining / stable badge).
- [x] Score breakdown section shows all component scores with their weights and contribution to final score.

### 2I. Proctoring Mode (Optional)

- [x] Task model gains proctoring_enabled field (Boolean, default false).
- [x] When proctoring_enabled=true, candidate sees "This assessment is proctored" notice on welcome page.
- [x] When proctoring_enabled=true, warning toast shown to candidate on each tab switch: "You have left the assessment tab. This has been recorded."
- [x] All tab switches recorded with timestamps in assessment timeline.
- [x] Assessments with >5 tab switches automatically flagged for recruiter review (flag in prompt_fraud_flags).
- [x] Recruiter can see proctoring summary: total tab switches, timestamps, focus percentage.

---

## Phase 3 - Production Hardening

Remaining QA and stability items from previous sprint.

### CORS and Origins

- [x] CORS_EXTRA_ORIGINS config verified working with production Vercel domain.
- [x] CORS middleware includes both FRONTEND_URL and CORS_EXTRA_ORIGINS in allowed_origins.

### E2B Sandbox Reliability

- [x] E2B get_sandbox_id method handles id, sandbox_id, and sandboxId attributes (SDK version compatibility).
- [x] Start assessment returns clear 503 with detail message when E2B_API_KEY missing.
- [x] Start assessment returns clear 503 with detail message when sandbox creation fails.
- [x] Sandbox cleanup on DB commit failure prevents leaked sandbox resources.

### Transaction Safety

- [x] All critical write endpoints use try/except + db.rollback() on failures.
- [x] Assessment submission enforces valid state transition (in_progress -> completed only).
- [x] Start assessment uses with_for_update() for concurrency safety.

### Auth and Security

- [x] Password reset token uses secure comparison (secrets.compare_digest).
- [x] JWT creation uses timezone-aware expiry (datetime.now(timezone.utc)).
- [x] All datetime operations use timezone-aware timestamps consistently.

### Database Performance

- [x] Foreign key indexes exist on assessments.organization_id, assessments.candidate_id, assessments.task_id.
- [x] Foreign key indexes exist on users.organization_id, tasks.organization_id, candidates.organization_id.
- [x] Index migration (002) is present and reversible.

### Input Validation

- [x] Pydantic schemas use Field(min_length, max_length) for string inputs.
- [x] Pydantic schemas use Field(ge, le) for numeric inputs (limit, offset, duration_minutes).
- [x] EmailStr used for all email input fields.

---

## Phase 4 - Integration Completion

### Stripe Webhooks

- [x] Stripe webhook refuses processing when STRIPE_WEBHOOK_SECRET is missing (returns 400).
- [x] Stripe webhook handles payment_intent.succeeded -- updates org billing.
- [x] Stripe webhook handles payment_intent.payment_failed -- logs failure.
- [x] Stripe webhook handles customer.subscription.updated -- updates org plan.
- [x] Stripe webhook handles customer.subscription.deleted -- downgrades org plan.
- [x] Organization billing fields (plan, assessments_used, assessments_limit) update correctly from events.

### Workable Integration

- [x] Workable webhook refuses processing when WORKABLE_WEBHOOK_SECRET is missing.
- [x] Workable candidate_stage_changed webhook auto-creates assessment (config-driven).
- [x] Assessment completion enqueues post_to_workable Celery task when linked candidate has Workable ID.
- [x] Manual POST /assessments/{id}/post-to-workable marks posted_to_workable=true with timestamp.
- [x] WorkableService.post_assessment_result() formats score and link as Workable comment.

### PDF Report Generation

- [x] GET /api/v1/assessments/{id}/report.pdf generates downloadable PDF.
- [x] PDF includes: candidate info, overall score, component score breakdown, prompt analytics summary, test results.
- [x] PDF uses consistent branding (TALI header, black/white/purple theme).

### Email Notifications

- [x] Assessment invitation email sends with correct FROM address (from EMAIL_FROM config).
- [x] Assessment completion notification sent to recruiter with score and dashboard link.
- [x] Resend invite (POST /assessments/{id}/resend) re-sends invitation email.

---

## Phase 5 - Frontend Polish

### Dashboard Enhancements

- [x] CSV export button downloads visible assessments as CSV.
- [x] JSON export button downloads visible assessments as JSON.
- [x] Lightweight in-app notification area for recently completed assessments.
- [x] Side-by-side comparison view for two selected assessments (checkbox select + compare button).

### Candidate Detail Actions

- [x] Download PDF button calls GET /assessments/{id}/report.pdf and triggers browser download.
- [x] Post to Workable button calls POST /assessments/{id}/post-to-workable; disabled if already posted.
- [x] Delete assessment button with confirmation modal; calls DELETE /assessments/{id}.
- [x] Recruiter notes input; calls POST /assessments/{id}/notes and displays in timeline.

### Settings Page

- [x] Team tab shows org member list from GET /api/v1/users.
- [x] Team tab has invite form (email, full_name) calling POST /api/v1/users/invite.
- [x] Preferences tab with dark mode toggle (persisted in localStorage).

### Candidate Management Page

- [x] Candidates page at #/candidates listing all org candidates with search.
- [x] Candidate detail view showing all assessments for that candidate.
- [x] Create/edit candidate form.

---

## Phase 6 - Testing & Monitoring

### Backend Tests

- [x] test_auth.py: register, login success, login invalid credentials, get current user.
- [x] test_assessments.py: create, list (paginated), start, execute, submit.
- [x] test_candidates.py: full CRUD flow (create, list, get, update, delete).
- [x] test_assessment_actions.py: delete, resend, post-to-workable, report PDF.
- [x] test_prompt_analytics.py: all heuristic signal functions with edge cases.
- [x] test_scoring.py: composite scoring with default and custom weights.
- [x] All tests use pytest fixtures for db session, test client, authenticated user.
- [x] Tests mock E2B, Claude, Stripe, Workable, and Resend external calls.
- [x] Tests run with: cd backend && python3 -m pytest tests/ -v
- [x] Test coverage >70% for core modules.

### Frontend Tests

- [x] npm test script configured (Vitest + React Testing Library + jsdom).
- [x] AuthContext test: hydration from localStorage, logout clears token.
- [x] AssessmentPage test: paste tracking, focus tracking, code_context sent with messages.
- [x] Score display test: radar chart renders with mock data.

### Operations & Monitoring

- [x] /health endpoint checks PostgreSQL and Redis connectivity; returns "healthy" or "degraded".
- [x] Structured JSON logging configured for production (JsonFormatter in logging_config.py).
- [x] Sentry integrated in backend (optional, env-driven).
- [x] Deployment docs updated with CORS_EXTRA_ORIGINS, EMAIL_FROM, all new env vars.
- [x] Database backups automated via Railway.

---

## Definition of Done

A criterion is complete only when ALL of the following are true:
- Code is merged in backend/frontend on main branch.
- Input validation and error handling exist for the feature.
- Endpoint/UI behavior works with real deployment environment variables (not just localhost).
- Tests exist (or updated) and pass locally.
- Criterion checkbox is explicitly marked [x].

---

## Guardrails for Agents

### Cost Control
- **NEVER call Claude per-prompt for scoring.** All prompt analysis is batched into a SINGLE Claude call at submit time. This is non-negotiable.
- Monitor token usage in analyze_prompt_session; set max_tokens appropriately.

### Browser Compatibility
- When using document.visibilityState or window focus/blur events, add fallback for browsers that don't support these APIs.
- Test tab tracking in Safari, Chrome, and Firefox.

### Score Weights
- **NEVER hardcode score weights.** Always pull from config (SCORE_WEIGHTS) or Task model (score_weights JSON field). Different clients want different weightings.

### Database Safety
- Always wrap db.commit() in try/except with db.rollback() on failure.
- Use with_for_update() for any status transitions.
- Run alembic revision --autogenerate for migrations; review before applying.

### Deployment
- Never commit .env files or API keys.
- Always verify CORS origins include production frontend URL before deploying.
- Never claim deployment success without checking live endpoint responses.
- Test E2B sandbox creation with production API key before marking start-assessment as working.

### Code Standards
- Backend: sync route handlers (def, not async def). Use get_db() and get_current_user() dependencies.
- Frontend: hash routing in App.jsx. API client in src/lib/api.js. Auth via context/AuthContext.jsx.
- All new API endpoints need Pydantic request/response schemas.
- Return proper HTTP status codes (201 create, 204 delete, 400 bad request, 404 not found, 503 service unavailable).

### Task Hygiene
- Always update this file when work is completed (mark [x]).
- Never repeat the same commit with no net code change.
- Never leave integration endpoints half-implemented without explicit TODO comments.
- Commit messages follow: "ralph: [feature] - description"

---

## Implementation Order

1. ~~Strip Demo Mode (Phase 1) -- 5% effort~~ DONE
2. ~~Expand Prompt Data Capture (Phase 2A) -- 10% effort~~ DONE
3. ~~Frontend Tracking: paste, focus, tabs (Phase 2G) -- 10% effort~~ DONE
4. ~~Build Scoring Engine: AI signals + heuristics (Phase 2B + 2C) -- 25% effort~~ DONE
5. ~~Calibration Prompt System (Phase 2D) -- 5% effort~~ DONE
6. ~~Composite Scoring + DB Migration (Phase 2E + 2F) -- 15% effort~~ DONE
7. ~~Frontend Score Display (Phase 2H) -- 20% effort~~ DONE
8. ~~Proctoring Mode (Phase 2I) -- 5% effort~~ DONE
9. Production Hardening + Integrations + Testing (Phase 3-6) -- 5% effort -- MOSTLY DONE

## Remaining Items

- [x] Calibration score computed via Claude analysis of single calibration interaction
- [x] Calibration score comparison UI in recruiter dashboard
- [x] WorkableService.post_assessment_result() full formatting
- [x] PDF report with full prompt analytics summary and branding
- [x] Candidates page at #/candidates with search
- [x] Test coverage >70% for core scoring modules (`prompt_analytics.py` coverage: 82%)
- [x] Frontend tests for AssessmentPage tracking and radar chart
- [x] Database backups via Railway (documented runbook in `docs/DEPLOYMENT.md`)
