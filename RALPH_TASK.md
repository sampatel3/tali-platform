# RALPH_TASK.md - TALI Platform

---
task: TALI Platform - AI-Augmented Technical Assessment
test_command: "cd backend && pytest tests/ -v"
---

**Project**: TALI - AI-Augmented Technical Assessment Platform  
**Objective**: Production-ready platform for screening engineers using AI-era assessment methods.  
**Repository**: Monorepo with working UI and backend; extend and refine per criteria below.

## Project Overview

TALI is a technical screening platform that tests how engineers work WITH AI tools (not against them). The platform measures 10+ unique signals including prompt quality, design thinking, AI collaboration efficiency, and problem-solving approach.

**Core Workflow:**
1. Recruiter creates assessment via dashboard
2. Email sent to candidate with unique token
3. Candidate completes task in browser (Monaco editor + Claude chat)
4. Code executes in E2B sandbox, tests run automatically
5. Results instantly available to recruiter with detailed analytics
6. Optional: Results posted to Workable ATS

## Tech Stack

### Backend
- **Framework**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL 15 + SQLAlchemy 2.0 + Alembic
- **Cache/Queue**: Redis + Celery
- **Auth**: JWT (PyJWT)
- **Deployment**: Railway

### Frontend
- **Framework**: Vite 5 + React 18 (JSX)
- **Styling**: Tailwind CSS (black/white/purple #9D00FF theme)
- **Routing**: Hash-based (#/dashboard, #/login, #/assess/{token}, #/settings, etc.) in single App.jsx
- **Editor**: Monaco Editor (@monaco-editor/react)
- **Auth**: Custom AuthContext + JWT in localStorage (no NextAuth)
- **API client**: src/lib/api.js (axios baseURL from VITE_API_URL)
- **Deployment**: Vercel

### Integrations
- **Code Execution**: E2B Code Interpreter SDK
- **AI**: Anthropic Claude Sonnet 4 API
- **ATS**: Workable API (OAuth + webhooks)
- **Payments**: Stripe API
- **Email**: Resend API

## Database Schema

```sql
-- Core tables (create in order):
1. organizations (id, name, slug, workable_*, stripe_*, plan, assessments_used, assessments_limit)
2. users (id, email, hashed_password, full_name, is_active, is_superuser, organization_id)
3. candidates (id, organization_id, email, full_name, position, workable_candidate_id, workable_data)
4. tasks (id, organization_id, name, description, task_type, difficulty, duration_minutes, starter_code, test_code, sample_data, dependencies, success_criteria, test_weights, is_template, is_active)
5. assessments (id, organization_id, candidate_id, task_id, token, status, duration_minutes, started_at, completed_at, expires_at, score, tests_passed, tests_total, code_quality_score, time_efficiency_score, ai_usage_score, test_results, ai_prompts, code_snapshots, timeline, e2b_session_id, workable_candidate_id, workable_job_id, posted_to_workable, posted_to_workable_at)
6. assessment_sessions (id, assessment_id, session_start, session_end, keystrokes, code_executions, ai_requests, activity_log)

-- Add indexes:
- users.email, users.organization_id
- assessments.token, assessments.organization_id, assessments.candidate_id, assessments.status
- candidates.organization_id, candidates.email
```

---

## Success Criteria

### 🔐 Authentication & User Management

- [ ] POST /api/v1/auth/register endpoint accepts email, password, full_name
- [ ] POST /api/v1/auth/register validates email format and password strength
- [ ] POST /api/v1/auth/register hashes password with bcrypt before storing
- [ ] POST /api/v1/auth/register creates user in database and returns user data
- [ ] POST /api/v1/auth/login endpoint accepts email and password
- [ ] POST /api/v1/auth/login verifies credentials against database
- [ ] POST /api/v1/auth/login generates JWT token with 30-minute expiry
- [ ] POST /api/v1/auth/login returns access token and user data
- [ ] GET /api/v1/auth/me endpoint requires valid JWT token in Authorization header
- [ ] GET /api/v1/auth/me returns current user data from decoded token
- [ ] Auth dependency function get_current_user extracts and validates JWT
- [ ] Auth dependency function get_current_user returns User object or raises 401
- [ ] Frontend login page at #/login with email and password inputs
- [ ] Frontend login page calls /api/v1/auth/login and stores token (AuthContext + localStorage)
- [ ] Frontend login page redirects to dashboard on success
- [ ] Frontend register page at #/register with full_name, email, password, confirm_password inputs
- [ ] Frontend register page validates password confirmation matches
- [ ] Frontend register page calls /api/v1/auth/register then redirects to #/login
- [ ] AuthContext provides user, login, logout, and token to all components
- [ ] Protected routes (dashboard, settings, tasks, analytics) redirect to landing if no valid token
- [ ] Navigation shows user email when authenticated; forgot-password and reset-password flows exist

### 📋 Assessment Management (Backend)

- [ ] POST /api/v1/assessments endpoint creates assessment with organization_id, candidate_id, task_id
- [ ] POST /api/v1/assessments generates unique token (UUID4)
- [ ] POST /api/v1/assessments sets expires_at to 7 days from now
- [ ] POST /api/v1/assessments sets status to "pending"
- [ ] POST /api/v1/assessments returns created assessment with token
- [ ] GET /api/v1/assessments endpoint lists assessments for current user's organization
- [ ] GET /api/v1/assessments supports query params: status, task_id, limit, offset
- [ ] GET /api/v1/assessments returns paginated results: { items, total, limit, offset }
- [ ] GET /api/v1/assessments/{id} returns single assessment with candidate and task data joined
- [ ] GET /api/v1/assessments/{id} checks user belongs to same organization
- [ ] POST /api/v1/assessments/token/{token}/start verifies token is valid and not expired
- [ ] POST /api/v1/assessments/token/{token}/start checks status is "pending"
- [ ] POST /api/v1/assessments/token/{token}/start creates E2B sandbox via E2BService
- [ ] POST /api/v1/assessments/token/{token}/start stores e2b_session_id in assessment
- [ ] POST /api/v1/assessments/token/{token}/start updates status to "in_progress" and sets started_at
- [ ] POST /api/v1/assessments/token/{token}/start returns task data (description, starter_code, duration_minutes)
- [ ] POST /api/v1/assessments/{assessment_id}/execute accepts code string in request body; uses Header X-Assessment-Token
- [ ] POST /api/v1/assessments/{assessment_id}/execute reconnects to E2B sandbox using e2b_session_id
- [ ] POST /api/v1/assessments/{assessment_id}/execute runs code in sandbox via E2BService.execute_code
- [ ] POST /api/v1/assessments/{assessment_id}/execute returns execution result (stdout, stderr, etc.)
- [ ] POST /api/v1/assessments/{assessment_id}/claude accepts message and conversation_history; uses Header X-Assessment-Token
- [ ] POST /api/v1/assessments/{assessment_id}/claude sends prompt to Claude API via ClaudeService
- [ ] POST /api/v1/assessments/{assessment_id}/claude appends prompt to assessment.ai_prompts JSONB array
- [ ] POST /api/v1/assessments/{assessment_id}/claude returns Claude response
- [ ] POST /api/v1/assessments/{assessment_id}/submit accepts final_code in request body; uses Header X-Assessment-Token
- [ ] POST /api/v1/assessments/{assessment_id}/submit runs tests in E2B sandbox
- [ ] POST /api/v1/assessments/{assessment_id}/submit calculates score (tests_passed/tests_total, code quality, AI usage)
- [ ] POST /api/v1/assessments/{assessment_id}/submit stores test_results, code_quality_score, ai_usage_score, timeline
- [ ] POST /api/v1/assessments/{assessment_id}/submit updates status to "completed" and sets completed_at
- [ ] POST /api/v1/assessments/{assessment_id}/submit returns complete results
- [ ] PATCH /api/v1/tasks/{id} and DELETE /api/v1/tasks/{id} for tasks (assessments use create/list/get/start/execute/submit only)

### 🧑‍💼 Candidate Management

- [ ] POST /api/v1/candidates endpoint creates candidate with organization_id, email, full_name, position
- [ ] POST /api/v1/candidates validates email format
- [ ] POST /api/v1/candidates checks for duplicate email within organization
- [ ] POST /api/v1/candidates returns created candidate
- [ ] GET /api/v1/candidates lists candidates for organization with pagination
- [ ] GET /api/v1/candidates supports search by email or name
- [ ] GET /api/v1/candidates/{id} returns single candidate with all assessments
- [ ] PATCH /api/v1/candidates/{id} updates candidate data
- [ ] DELETE /api/v1/candidates/{id} soft deletes candidate

### 📝 Task Management

- [ ] POST /api/v1/tasks endpoint creates task with name, description, task_type, difficulty
- [ ] POST /api/v1/tasks accepts starter_code, test_code, sample_data, dependencies
- [ ] POST /api/v1/tasks validates success_criteria is valid JSON
- [ ] POST /api/v1/tasks returns created task
- [ ] GET /api/v1/tasks lists all template tasks (is_template=true) plus organization's custom tasks
- [ ] GET /api/v1/tasks filters by task_type, difficulty
- [ ] GET /api/v1/tasks/{id} returns single task
- [ ] PATCH /api/v1/tasks/{id} updates task (only if organization owns it; template tasks are read-only)
- [ ] DELETE /api/v1/tasks/{id} deletes task (only if not template)
- [ ] Tasks page: every task card has "View" button (eye icon); template tasks are view-only, non-template have Edit/Delete
- [ ] Seed script creates template tasks; templates visible to all orgs
- [ ] Debugging Challenge task has 3 bugs, Python code, pytest tests
- [ ] RAG Pipeline task has 4 bugs, document loading, Claude API integration

### 🔌 E2B Integration Service

- [ ] E2BService class initializes with E2B API key from environment
- [ ] E2BService.create_sandbox() creates new E2B sandbox instance
- [ ] E2BService.create_sandbox() returns sandbox object with session_id
- [ ] E2BService.create_sandbox() logs sandbox creation with timestamp
- [ ] E2BService.execute_code(sandbox, code) runs Python code in sandbox
- [ ] E2BService.execute_code() captures stdout and stderr
- [ ] E2BService.execute_code() handles execution errors gracefully
- [ ] E2BService.execute_code() returns dict with stdout, stderr, exit_code, execution_time
- [ ] E2BService.run_tests(sandbox, test_code) writes test file to sandbox
- [ ] E2BService.run_tests() executes pytest in sandbox
- [ ] E2BService.run_tests() parses pytest output for passed/failed counts
- [ ] E2BService.run_tests() returns dict with tests_passed, tests_total, test_details
- [ ] E2BService.install_dependencies(sandbox, packages) runs pip install in sandbox
- [ ] E2BService.install_dependencies() handles package installation errors
- [ ] E2BService.close_sandbox(sandbox) terminates sandbox
- [ ] E2BService.close_sandbox() logs closure with session_id

### 🤖 Claude Integration Service

- [ ] ClaudeService class initializes with Anthropic API key from environment
- [ ] ClaudeService.chat(messages, system) sends messages to Claude API
- [ ] ClaudeService.chat() uses claude-sonnet-4-20250514 model
- [ ] ClaudeService.chat() sets max_tokens to 4096
- [ ] ClaudeService.chat() returns dict with response text and token usage
- [ ] ClaudeService.chat() handles API errors with retry logic (3 attempts)
- [ ] ClaudeService.analyze_code_quality(code) prompts Claude to score code 0-10
- [ ] ClaudeService.analyze_code_quality() extracts scores for readability, efficiency, correctness
- [ ] ClaudeService.analyze_code_quality() returns dict with overall_score and breakdown
- [ ] ClaudeService.analyze_prompt_quality(prompt) scores prompt clarity 0-10
- [ ] ClaudeService.analyze_prompt_quality() returns quality_score

### 💳 Stripe Integration Service

- [ ] StripeService class initializes with Stripe API key from environment
- [ ] StripeService.create_customer(email, name) creates Stripe customer
- [ ] StripeService.create_customer() returns customer_id
- [ ] StripeService.charge_assessment(customer_id, amount) creates PaymentIntent for £25.00
- [ ] StripeService.charge_assessment() sets amount to 2500 (pence)
- [ ] StripeService.charge_assessment() confirms payment immediately
- [ ] StripeService.charge_assessment() returns payment status
- [ ] StripeService.create_subscription(customer_id, plan) creates subscription for £300/month
- [ ] StripeService.create_subscription() returns subscription_id
- [ ] StripeService.cancel_subscription(subscription_id) cancels subscription
- [ ] POST /api/v1/webhooks/stripe endpoint verifies Stripe webhook signature
- [ ] POST /api/v1/webhooks/stripe handles payment_intent.succeeded event
- [ ] POST /api/v1/webhooks/stripe handles payment_intent.failed event
- [ ] POST /api/v1/webhooks/stripe handles customer.subscription.deleted event
- [ ] POST /api/v1/webhooks/stripe updates organization billing status on events

### 🔗 Workable Integration Service

- [ ] WorkableService class initializes with client_id and client_secret from environment
- [ ] WorkableService.get_authorization_url(redirect_uri) returns OAuth URL
- [ ] WorkableService.exchange_code_for_token(code) exchanges OAuth code for access/refresh tokens
- [ ] WorkableService.exchange_code_for_token() returns dict with access_token, refresh_token
- [ ] WorkableService.refresh_access_token(refresh_token) gets new access_token
- [ ] WorkableService.get_candidate(candidate_id, access_token) fetches candidate from Workable
- [ ] WorkableService.post_assessment_result(candidate_id, data, access_token) posts to candidate activity
- [ ] WorkableService.post_assessment_result() formats data as comment with score and link
- [ ] WorkableService.update_candidate_stage(candidate_id, stage, access_token) moves candidate to stage
- [ ] GET /api/v1/organizations/workable/authorize-url returns { url } for Workable OAuth (frontend redirects)
- [ ] POST /api/v1/organizations/workable/connect accepts { code }, exchanges for tokens, stores on org
- [ ] Frontend callback at /settings/workable/callback?code=... sends code to connect, then redirects to #/settings
- [ ] POST /api/v1/webhooks/workable verifies webhook signature
- [ ] POST /api/v1/webhooks/workable handles candidate_stage_changed event
- [ ] POST /api/v1/webhooks/workable checks if auto-send is enabled for stage
- [ ] POST /api/v1/webhooks/workable creates assessment automatically if stage matches config

### 📧 Email Service & Celery Tasks

- [ ] EmailService class initializes with Resend API key from environment
- [ ] EmailService.send_assessment_invitation(candidate_email, token, task_name) sends email
- [ ] EmailService.send_assessment_invitation() uses HTML template with black/white/purple styling
- [ ] EmailService.send_assessment_invitation() includes unique assessment link with token
- [ ] EmailService.send_assessment_invitation() has clear CTA button
- [ ] EmailService.send_results_notification(recruiter_email, candidate_name, score) sends email
- [ ] EmailService.send_results_notification() includes score, tests passed, and dashboard link
- [ ] Celery app configured with Redis as broker
- [ ] Celery task send_assessment_email_task calls EmailService.send_assessment_invitation
- [ ] Celery task post_to_workable_task calls WorkableService.post_assessment_result
- [ ] Assessment creation triggers send_assessment_email_task asynchronously
- [ ] Assessment completion triggers post_to_workable_task if workable_connected=true
- [ ] Celery worker can be started with: celery -A app.tasks worker

### 🎨 Frontend - Landing Page

- [ ] Landing page at #/ (hash root) with hero section
- [ ] Hero has headline: "Screen AI-Era Engineers in 30 Minutes"
- [ ] Hero has subheadline explaining TALI's unique approach
- [ ] Hero has two CTAs: "Book Demo" and "Start Trial"
- [ ] Problem section explains AI arms race with visuals
- [ ] Solution section shows TALI's 10 data points
- [ ] Features section with 3-4 key features
- [ ] Pricing section with two cards: Pay-per-use (£25) and Monthly (£300)
- [ ] Footer with links, contact info, legal
- [ ] Navigation bar with logo, links, Login/Sign Up buttons
- [ ] All styling follows black/white/purple theme with 2px borders

### 🎨 Frontend - Authentication Pages

- [ ] Login page at #/login with email and password inputs
- [ ] Login page styled with black/white/purple theme
- [ ] Login page has "Forgot password?" link to #/forgot-password
- [ ] Login page has "Don't have an account? Sign up" link to #/register
- [ ] Login form validates email format before submit
- [ ] Login form shows error message on failed login
- [ ] Login form redirects to /dashboard on success
- [ ] Register page at /register with full_name, email, password, confirm_password inputs
- [ ] Register page validates password length (min 8 chars)
- [ ] Register page validates passwords match
- [ ] Register form shows error if email already exists
- [ ] Register form redirects to /login on success with success message
- [ ] Both pages use consistent Button and Input components from ui/

### 🎨 Frontend - Dashboard (Recruiter)

- [ ] Dashboard layout with navigation sidebar or top bar
- [ ] Navigation shows: Dashboard, Assessments, Candidates, Analytics, Settings
- [ ] Navigation shows user email with dropdown (Logout option)
- [ ] Dashboard home at #/dashboard shows 4 stat cards across top
- [ ] Stat card 1: Active Assessments (count where status="in_progress")
- [ ] Stat card 2: Completion Rate (completed / total %)
- [ ] Stat card 3: Average Score (avg of all completed assessments)
- [ ] Stat card 4: Cost This Month (assessments_used * £25)
- [ ] Dashboard home shows recent assessments table below stats
- [ ] Recent assessments table has columns: Candidate, Task, Status, Score, Time, Assessment link, Actions
- [ ] Candidate name and email from API (candidate_name, candidate_email in list response)
- [ ] Assessment link column has "Copy link" button (copies URL like origin/#/assess/{token})
- [ ] Status shown as colored badge (pending=gray, in_progress=blue, completed=green)
- [ ] Score shown as "8.7/10" with color (green >8, yellow 6-8, red <6)
- [ ] Actions column has "View" for completed assessments
- [ ] Table supports pagination (limit/offset from API)
- [ ] "Create Assessment" button in top right opens modal

### 🎨 Frontend - Assessment Creation Modal

- [ ] Modal opens when "Create Assessment" clicked
- [ ] Modal has dropdown to select or create candidate
- [ ] If "Create New Candidate": shows email, full_name, position inputs
- [ ] Modal has dropdown to select task template
- [ ] Task dropdown shows task name, difficulty badge, duration
- [ ] Modal has "Send Assessment" button
- [ ] On submit, calls POST /api/v1/assessments with candidate_email, candidate_name, task_id (candidate created or updated inline)
- [ ] Shows success message: "Assessment sent to {candidate_email}"
- [ ] Closes modal and refreshes assessments list
- [ ] Shows error message if API call fails

### 🎨 Frontend - Assessment Detail Page

- [ ] Assessment detail page: View from dashboard opens candidate detail (fetches GET /api/v1/assessments/{id})
- [ ] Header shows candidate name, position, assessment created date
- [ ] Overall score card shows score (large), tests passed (5/5), status badge
- [ ] Tabs: Test Results, AI Usage, Timeline, Code Review
- [ ] Test Results tab shows list of tests with pass/fail icons and descriptions
- [ ] AI Usage tab shows list of prompts with timestamp, prompt text, quality score
- [ ] AI Usage tab shows avg prompt quality, total prompts, AI efficiency score
- [ ] Timeline tab shows chronological list of events (started, bug fixed, test passed, submitted)
- [ ] Timeline events show timestamp, event type, description
- [ ] Code Review tab shows final code in Monaco editor (read-only)
- [ ] Code Review tab shows code quality score breakdown
- [ ] Action buttons: "Download PDF", "Post to Workable" (if connected), "Delete"
- [ ] "Post to Workable" button disabled if already posted
- [ ] "Post to Workable" calls backend API and shows success toast

### 🎨 Frontend - Assessment Interface (Candidate)

- [ ] Assessment interface at #/assess/{token}
- [ ] On load, calls POST /api/v1/assessments/token/{token}/start (or uses startData passed from welcome page to avoid double-start)
- [ ] Shows loading screen while sandbox creates
- [ ] Once started, shows split layout: 65% code editor, 35% right panel
- [ ] Top bar shows: Task name, Timer (counting down), Submit button
- [ ] Timer shows time remaining (e.g., "28:34 remaining" in MM:SS)
- [ ] Timer turns red when < 5 minutes
- [ ] Left panel: Monaco editor with starter code loaded
- [ ] Monaco editor has Python syntax highlighting
- [ ] Monaco editor has "Run Code" button below
- [ ] Right panel split: Top 60% = Claude chat, Bottom 40% = Output console
- [ ] Claude chat has message list (scrollable)
- [ ] Claude chat has input field at bottom with Send button
- [ ] Claude chat shows user messages on right (purple bubble)
- [ ] Claude chat shows Claude responses on left (white bubble)
- [ ] Claude chat has loading indicator when waiting for response
- [ ] Output console shows stdout/stderr from last execution
- [ ] Output console has tabs: Output, Tests (shows test results)
- [ ] "Run Code" button calls POST /api/v1/assessments/{assessment_id}/execute with X-Assessment-Token header
- [ ] "Run Code" button disabled while executing, shows loading spinner
- [ ] Send message calls POST /api/v1/assessments/{assessment_id}/claude with X-Assessment-Token header
- [ ] Submit button opens confirmation modal; Submit calls POST /api/v1/assessments/{assessment_id}/submit with final_code and X-Assessment-Token
- [ ] After submit, redirects to results page showing score and breakdown

### 🎨 Frontend - Settings Pages

- [ ] Settings page at #/settings with tabs: Workable, Billing
- [ ] Workable tab shows connection status (Connected/Not Connected)
- [ ] Workable tab has "Connect Workable" button if not connected; button fetches GET /api/v1/organizations/workable/authorize-url then redirects to returned URL
- [ ] Workable callback page at path /settings/workable/callback?code=... exchanges code via POST /organizations/workable/connect, then redirects to #/settings
- [ ] Billing tab shows current plan (Pay-per-use) and total usage from GET /api/v1/billing/usage
- [ ] Billing tab shows usage history table (date, candidate, task, cost) and "Add credits (£25)" button
- [ ] "Add credits" calls POST /api/v1/billing/checkout-session and redirects to Stripe Checkout

### 🧪 Backend Tests

- [ ] Test file test_auth.py with test_register_user
- [ ] Test file test_auth.py with test_login_success
- [ ] Test file test_auth.py with test_login_invalid_credentials
- [ ] Test file test_auth.py with test_get_current_user
- [ ] Test file test_assessments.py with test_create_assessment
- [ ] Test file test_assessments.py with test_list_assessments
- [ ] Test file test_assessments.py with test_start_assessment
- [ ] Test file test_assessments.py with test_execute_code
- [ ] Test file test_assessments.py with test_submit_assessment
- [ ] Test file test_e2b_service.py with test_create_sandbox
- [ ] Test file test_e2b_service.py with test_execute_code
- [ ] Test file test_e2b_service.py with test_run_tests
- [ ] All tests use pytest fixtures for db session, test client, test user
- [ ] Tests run with: cd backend && pytest tests/ -v
- [ ] Test coverage >70% for core modules

### 🧪 Frontend Tests

- [ ] Add npm test script to frontend/package.json (e.g. vitest or React Testing Library)
- [ ] Test key flows or components as needed; frontend currently has no test script

### 🚀 Deployment - Backend (Railway)

- [ ] Procfile created with: web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
- [ ] railway.json created with build and deploy config
- [ ] Environment variables set in Railway: DATABASE_URL, SECRET_KEY, E2B_API_KEY, ANTHROPIC_API_KEY, STRIPE_API_KEY, RESEND_API_KEY, REDIS_URL, FRONTEND_URL
- [ ] PostgreSQL addon added to Railway project
- [ ] Redis addon added to Railway project
- [ ] Railway start command runs: alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
- [ ] Backend deployed and accessible (e.g. Railway URL)
- [ ] Health check endpoint /health returns 200

### 🚀 Deployment - Frontend (Vercel)

- [ ] Frontend deployed to Vercel (build: npm run build from frontend/)
- [ ] Environment variable set: VITE_API_URL (backend API base URL)
- [ ] Frontend accessible and loads correctly; hash routes work (#/dashboard, #/assess/{token}, etc.)
- [ ] SPA: ensure /settings/workable/callback serves index.html for OAuth callback

### 🚀 Deployment - Celery Worker

- [ ] Separate Railway service created for Celery worker
- [ ] Celery service uses same Redis as main app
- [ ] Start command: celery -A app.tasks worker --loglevel=info
- [ ] Celery worker starts successfully and processes tasks

### 📊 Monitoring & Operations

- [ ] Sentry integrated in backend (app/main.py) if desired
- [ ] Error tracking in production; frontend has no Next.js (no next.config.js)
- [ ] Backend logs structured as JSON
- [ ] Health check endpoint /health checks database and Redis connectivity
- [ ] Uptime monitoring configured (UptimeRobot or similar)
- [ ] Database backups automated via Railway

---

## Guardrails (Lessons for AI Agents)

### Database & Migrations
- Always run `alembic revision --autogenerate` to generate migrations, never write migrations manually
- Review generated migrations before running `alembic upgrade head`
- Add indexes for all foreign keys and frequently queried fields
- Use JSONB for flexible data (prompts, timeline) to avoid schema changes

### API Design
- Always use Pydantic schemas for request/response validation
- Return proper HTTP status codes (201 for create, 404 for not found, 403 for forbidden)
- Include organization_id checks in all queries to prevent data leakage between orgs
- Use dependency injection for get_db() and get_current_user()

### E2B Integration
- Always handle E2B API errors gracefully (network issues, timeouts)
- Close sandboxes after use to avoid billing for idle sandboxes
- Set execution timeouts (90 minutes max per assessment)
- Test sandbox creation in development before deploying

### Frontend Best Practices
- Use Tailwind's utility classes, avoid custom CSS
- Keep components small and focused (single responsibility)
- Handle loading and error states for all async operations
- Store auth token in localStorage (via AuthContext), clear on logout
- Use src/lib/api.js for all API calls; routing is hash-based in App.jsx

### Testing
- Mock external APIs (E2B, Claude, Stripe) in tests
- Use pytest fixtures to avoid code duplication
- Test happy path first, then error cases
- Run tests before committing

### Deployment
- Never commit .env files or secrets
- Use environment variables for all config
- Test migrations on staging before production
- Monitor error rates after deployment

---

## Current State

**Implemented and working:**
- Backend: FastAPI, PostgreSQL, SQLAlchemy, Alembic, Redis, Celery. Auth (register, login, JWT, forgot/reset password). Assessments: create, list (with filters/pagination, candidate_name/task_name), get, token/start, execute, claude, submit. E2B sandbox create/reuse. Tasks: list, get, create, update, delete; template vs org tasks. Organizations: get, update, Workable authorize-url + connect. Billing: usage, Stripe checkout-session. Analytics endpoint. Rate limiting. Invite and results emails via Celery.
- Frontend: Vite + React, hash routing in App.jsx. AuthContext, login, register, forgot/reset password. Dashboard with stats, assessments table (candidate name, task, status, score, Assessment link column with Copy link, View). New Assessment modal. Candidate detail (prompts, timeline, results, breakdown). Tasks page with View/Edit/Delete (View for all tasks including templates). Settings: Workable tab (Connect Workable + callback), Billing tab (usage, Add credits). Candidate flow: #/assess/{token} welcome → start → AssessmentPage (Monaco, Claude chat, execute, submit).
- Deployment: Backend on Railway (alembic + uvicorn). Frontend on Vercel. Docs updated for Vercel + Railway only.

**Remaining / polish:**
- Optional: dedicated POST /candidates CRUD (candidates currently created inline with assessments). Optional: more Stripe webhook handling, Workable webhook auto-create assessment. Frontend test script and tests. Monitoring (Sentry, health checks).

---

## Build Priority

1. **Foundation First**: Database models, migrations, auth system
2. **Core Flow**: Assessment creation → start → execute → submit workflow
3. **Integrations**: E2B and Claude (required for core flow)
4. **Frontend Connection**: Hook up existing UI to backend APIs
5. **Secondary Features**: Workable, Stripe, email
6. **Testing & Deployment**: Tests, Railway/Vercel deployment

---

## Definition of Done

A feature is complete when:
- [ ] Code is written and follows project structure
- [ ] API endpoint returns correct response format
- [ ] Database changes have migrations
- [ ] Frontend page/component is connected to API
- [ ] Error handling is implemented
- [ ] At least one test exists for the feature
- [ ] Feature works end-to-end in development

---

## Notes for AI Agents

- Backend: FastAPI with sync route handlers (def, not async def). Use get_db() and get_current_user() dependencies. SQLAlchemy 2.0 with Session (e.g. db.query(Model) or select()). Use Pydantic schemas for request/response.
- Frontend: Single app in src/App.jsx with hash routing (#/dashboard, #/login, #/assess/{token}, #/settings, etc.). API client in src/lib/api.js (axios, VITE_API_URL). Auth via context/AuthContext.jsx (token in localStorage). No Next.js; no TypeScript (JSX).
- Monaco editor: import from '@monaco-editor/react'. Styling: Tailwind, black/white/purple #9D00FF theme, 2px borders, no rounded corners (brutalist).
- Assessment API: Start is POST /api/v1/assessments/token/{token}/start. Execute, claude, submit use POST /api/v1/assessments/{assessment_id}/... with header X-Assessment-Token. List returns { items, total, limit, offset } and each item includes candidate_name, candidate_email, task_name, token.
- Always return proper HTTP status codes and error messages from API.

---

**When you complete a feature, mark it [x] and commit with message: "ralph: [feature] - description"**