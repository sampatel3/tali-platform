# TAALI Platform

**AI-augmented technical assessment** for screening engineers who work *with* AI tools. Candidates code in-browser (Monaco + Claude chat), run code in E2B sandboxes, and recruiters get scores, timelines, and optional ATS (Workable) and billing (Stripe) integration.

---

## Implementation and release status

The core platform is implemented and deployable end-to-end. The audit branch is
locally verified but has not been deployed. Active execution
priorities belong in the repository's issue/PR workflow; `RALPH_TASK.md` is a
historical record of the completed Jobs-first redesign, not a current backlog.

### Backend (Railway)

- **Stack:** FastAPI, PostgreSQL (SQLAlchemy 2 + Alembic), Redis, Celery.
- **Auth:** Register, login, JWT (`/api/v1/auth/*`), forgot/reset password.
- **Assessments:** Create (candidate inline), list (filters, pagination, `candidate_name` / `candidate_email` / `task_name`), get by id, start by token, execute code, Claude chat, submit. E2B sandbox create/reuse, timeline and results persisted.
- **Repository context model:** generated tasks are provisioned and verified in GitHub before activation. Each assessment uses a candidate-specific branch; submission checkpoints the exact branch/HEAD before grading, and retry workers recover that verified artifact in a fresh sandbox if the original E2B session has ended.
- **Tasks:** Browse backend-managed templates and generated organization tasks. Direct
  task-authoring CRUD exists only behind the deliberately disabled
  `TASK_AUTHORING_API_ENABLED` operator flag; the normal product path generates,
  battle-tests, and approves a task from the role specification.
- **Organizations:** Get, update; Workable OAuth: `GET authorize-url`, `POST workable/connect`.
- **Billing:** Usage and cost reporting plus usage-based Stripe top-ups. There is no
  subscription or minimum-spend requirement in the current product model.
- **Other:** Analytics endpoint, rate limiting (auth + assessment), invite + results emails via Celery. Public liveness is `GET /health`, redacted dependency readiness is `GET /ready`, and detailed diagnostics require `ADMIN_SECRET` at `GET /admin/health`.
- **Autonomous roles:** create/publish a requisition, accept or edit its monthly cap, and click **Turn on**. That click persists a durable server-side command; the browser may close immediately. The platform then generates and repairs the assessment, runs its sandbox battle test, provisions/verifies its repository, approves that exact passing draft, checks production readiness, opens the native job, starts the first complete cohort pass, and continuously processes incoming native/Workable applications. Publish itself is spend-free, and there is no separate Tasks-page setup or second approval click. Transient failures retry automatically; genuinely unusable job input or exhausted automated repair is surfaced as a human-input state. Irreversible reject recommendations remain human-confirmed.

### Frontend (Vercel)

- **Stack:** Vite 8, React 18, Tailwind; `react-router-dom` route modules, AuthContext, domain API clients in `src/shared/api/*`.
- **Landing:** Hero, product walkthrough, features, usage-based pricing, navigation,
  legal pages, and footer.
- **Auth:** Login, register, forgot/reset password; protected routes.
- **Dashboard:** Four stat cards (active assessments, completion rate, average score, cost this month); assessments table with Candidate, Task, Status, Score, Time, **Assessment link** (Copy link), View; filters and pagination; “Create Assessment” modal (select/create candidate, select task, send).
- **Candidate detail (View):** Header, score card, tabs (Test Results, AI Usage, Timeline, Code Review), CV/Job Fit analysis, and recruiter action buttons (Download PDF, Post to Workable, Delete).
- **Tasks:** Read-only task library for templates and generated organization tasks;
  task authoring is driven from the role workflow.
- **Settings:** Workable tab (Connect Workable, OAuth callback at `/settings/workable/callback`); Billing tab (usage, “Add credits” → Stripe Checkout).
- **Candidate flow:** `/assess/{token}` → welcome → start → AssessmentPage (Monaco, Claude chat, Run Code, execute, submit) → results.

### Deployment

- **Backend:** Railway. The supported web bootstrap validates production config,
  waits for PostgreSQL, applies `alembic upgrade head`, then starts uvicorn.
  PostgreSQL and Redis are Railway add-ons; repository deployment wrappers select
  and validate web plus the general and scoring workers.
- **Dependency locks:** CI installs the 158-pin/3,239-hash development-inclusive
  `requirements-lock.txt` (input digest
  `1ccf343d68762a1d159649a6a4866feacd719932f64154854f27c5f31a0d956c`).
  Production validates and installs only the 126-pin/2,961-hash
  `requirements-runtime-lock.txt` (input digest
  `5d9434231b3aac770ce1a2ba107d3addf89a2941123340602caf0fc11bd4edde`)
  into `/opt/venv` with `--require-hashes --no-deps` before import and audit
  checks. This keeps test tooling out of runtime without removing capability.
- **Frontend:** Vercel; build from `frontend/` with `npm run build`; `VITE_API_URL` points to backend.
- **Docs:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (Vercel + Railway only), [docs/ENV_SETUP.md](docs/ENV_SETUP.md).

**Production endpoints and release contract:**
- **Frontend:** https://www.taali.ai rendered cleanly in the final read-only
  1280px desktop check: one H1 and one main landmark after load, no horizontal
  overflow, and no console warnings or errors. No audit assets were deployed.
- **Backend:** the legacy `https://api.taali.ai/health` navigation did not
  complete during the final check, so production backend readiness and worker
  topology were not verified. Use the deployment guide's redacted and
  authenticated health gates rather than inferring health from local tests.
- **Release:** deploy backend and frontend from the same clean `main` commit
  with `./scripts/deploy_production.sh`; do not bypass its coordinated migration,
  worker, readiness, capability, and Vercel gates with direct provider commands.

---

## Repository layout

```
taali-platform/
├── backend/                 # FastAPI app
│   ├── app/
│   │   ├── domains/         # domain-owned HTTP routes and application services
│   │   ├── components/      # reusable assessment/integration components
│   │   ├── platform/        # config, database, security, middleware
│   │   ├── models/          # SQLAlchemy models
│   │   ├── schemas/         # Pydantic request/response
│   │   ├── services/        # shared E2B, Claude, Stripe, Workable, email services
│   │   └── tasks/           # Celery (invite email, results email, post to Workable)
│   ├── alembic/             # Migrations
│   ├── Procfile
│   ├── railway.json
│   ├── requirements-lock.txt          # development-inclusive hashed lock
│   ├── requirements-runtime-lock.txt  # production-only hashed lock
│   ├── runtime.txt                    # exact Python runtime contract
│   └── requirements.txt
├── frontend/                # Vite + React
│   ├── src/
│   │   ├── AppShell.jsx     # Route shell and lazy feature page composition
│   │   ├── context/         # authentication and toast state
│   │   ├── contexts/        # background-job state
│   │   ├── shared/api/*     # Domain API clients (VITE_API_URL)
│   │   └── components/assessment/
│   ├── package.json
│   └── vite.config.js
├── docs/
│   ├── DEPLOYMENT.md        # How to deploy (Vercel + Railway)
│   ├── ENV_SETUP.md         # All env vars
│   └── API.md
├── PRODUCT_PLAN.md          # Product plan and current capability contracts
└── README.md                # This file
```

---

## Quick start (production setup)

1. **Backend + two workers (Railway)**
   - New project; add PostgreSQL and Redis, plus web, general-worker, and scoring-worker services from the repository root. The coordinated wrapper supplies each service's root-safe build and start contract.
   - Share the production env set across all three services (see [ENV_SETUP.md](docs/ENV_SETUP.md)): `DEPLOYMENT_ENV=production`, `AUTO_GENERATE_ASSESSMENT_TASKS=true`, independently generated high-entropy `SECRET_KEY`, `INTEGRATION_ENCRYPTION_KEY`, and `ADMIN_SECRET` values, `ANTHROPIC_API_KEY`, pinned model variables, `E2B_API_KEY`, `RESEND_API_KEY`, real GitHub credentials, `REDIS_URL`, `DATABASE_URL`, `FRONTEND_URL`, and `BACKEND_URL`.
   - Merge through `main`, fetch it locally, and run `./scripts/deploy_production.sh` from that exact clean commit. The release guard refuses dirty, stale, and feature-branch worktrees, verifies the migration head, chat design system, provider identities, exact linked projects, and required services, then attests Railway and Vercel to the kickoff SHA even if `main` advances mid-rollout. Before changing variables and again immediately before migration, it verifies that every production revision is present and reachable in that release's Alembic graph. The Railway phase pins and validates the agent/ATS policy on all three services, runs the locked production migration bootstrap, deploys general `celery` + Beat, deploys scoring-only without Beat, deploys web from the repository root, then polls `/ready` and the authenticated capability gate.
   - The shared `backend/railway.json` deliberately has no HTTP healthcheck because Celery workers do not serve one; the wrapper's final gate validates web, both queue canaries, and live Anthropic/E2B/Resend/GitHub capability for the default assessment path.

2. **Frontend (Vercel)**  
   - Import or deploy from `frontend/`.  
   - Set `VITE_API_URL=https://<your-backend>.up.railway.app` and `VITE_STRIPE_PUBLISHABLE_KEY`.  
   - Redeploy.  
   - Open `https://<your-app>.vercel.app` — login/register and dashboard should work.

3. **Webhooks (after backend URL is fixed)**  
   - Stripe: endpoint `https://<backend>/api/v1/webhooks/stripe`, set `STRIPE_WEBHOOK_SECRET`.  
   - Workable inbound webhooks are not active yet: the reserved endpoint verifies signatures but returns `501` until a durable consumer exists. Use OAuth plus scheduled/manual sync; do not register the webhook endpoint yet.
   See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for event lists.

4. **Fund usage once**
   - Fund the organization credit ledger. Turn on remains durably queued and the role stays off when it cannot afford one conservative funnel pass; after a top-up or restored dependency, recovery continues automatically. Process Candidates and manual sync remain recovery controls, not requisition steps.

---


## Known limitations

- Production smoke tests hit live infrastructure and are intentionally separated from the default backend run (`pytest -m "production"`).
- Frontend is code-split by route/module; the largest remaining vendor chunk is `graph_vendor` (434.16 kB raw / 135.77 kB gzip in the audited build) and should stay monitored.

---

## Not yet implemented (optional / polish)

- **Candidate detail actions:** Additional one-off recruiter exports/actions can still be added, but they are not part of the autonomous requisition funnel.
- **Workable stage mapping:** Candidate import, scoring, invite delivery, and the configured assessment-stage/note handoff are automatic for linked agent roles. Configure the organization's interview-stage target once to allow autonomous Workable advancement; without it, the local pipeline advances and the external move remains a safe human handoff.
- **Stripe webhooks:** `checkout.session.completed` is the sole credit-grant event. Additional events may be handled for observability, but payment-intent events must never grant credits independently.
- **Frontend test quality:** the audited 156-file/1,102-test suite is warning-free; CI rejects React scheduling, router-future, Motion reduced-preference, unexpected API-network calls, and generic warning regressions instead of suppressing them.
- **Monitoring:** Structured JSON logs and readiness checks are built in. External error tracking, independent uptime checks, and managed database backups remain recommended production operations.

---

## Tech stack summary

| Layer      | Technology |
|-----------|------------|
| Backend   | FastAPI, Python 3.11.9, PostgreSQL 16, SQLAlchemy 2, Alembic, Redis, Celery, JWT |
| Frontend  | Vite 8, React 18, Tailwind CSS, Monaco Editor, react-router-dom routing |
| Execution | E2B Code Interpreter SDK |
| AI        | Anthropic Claude (pinned snapshots; ordinary requisition chat uses the configured chat/Haiku path, while current-role/spec/document-sensitive work uses the configured primary model) |
| ATS       | Workable (OAuth + scheduled/manual sync; inbound webhook reserved/501) |
| Payments  | Stripe (Checkout, usage) |
| Email     | Resend |
| Hosting   | Backend: Railway; Frontend: Vercel |

---

## Links

- **Deployment:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)  
- **Environment variables:** [docs/ENV_SETUP.md](docs/ENV_SETUP.md)  
- **Claude API integrations:** [docs/claude/README.md](docs/claude/README.md)
- **Repo baseline cleanup:** [docs/REPO_BASELINE_CLEANUP.md](docs/REPO_BASELINE_CLEANUP.md)
- **Backend testing:** [backend/docs/TESTING.md](backend/docs/TESTING.md)
- **Historical Jobs-first redesign record:** [RALPH_TASK.md](RALPH_TASK.md)
- **Observability metrics:** [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)
