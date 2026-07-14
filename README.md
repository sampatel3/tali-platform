# TAALI Platform

**AI-augmented technical assessment** for screening engineers who work *with* AI tools. Candidates code in-browser (Monaco + Claude chat), run code in E2B sandboxes, and recruiters get scores, timelines, and optional ATS (Workable) and billing (Stripe) integration.

---

## What’s Implemented and Deployed

The core platform is implemented and deployable end-to-end, and active execution priorities now live in `RALPH_TASK.md` (current hardening plan).

### Backend (Railway)

- **Stack:** FastAPI, PostgreSQL (SQLAlchemy 2 + Alembic), Redis, Celery.
- **Auth:** Register, login, JWT (`/api/v1/auth/*`), forgot/reset password.
- **Assessments:** Create (candidate inline), list (filters, pagination, `candidate_name` / `candidate_email` / `task_name`), get by id, start by token, execute code, Claude chat, submit. E2B sandbox create/reuse, timeline and results persisted.
- **Repository context model:** generated tasks are provisioned and verified in GitHub before activation. Each assessment uses a candidate-specific branch; submission checkpoints the exact branch/HEAD before grading, and retry workers recover that verified artifact in a fresh sandbox if the original E2B session has ended.
- **Tasks:** List, get, create, PATCH, DELETE; template vs org tasks.
- **Organizations:** Get, update; Workable OAuth: `GET authorize-url`, `POST workable/connect`.
- **Billing:** `GET usage`, `GET costs` (per-assessment + per-tenant infrastructure cost estimates), `POST checkout-session` (Stripe Checkout, £25).
- **Other:** Analytics endpoint, rate limiting (auth + assessment), invite + results emails via Celery. Health: `GET /health`.
- **Autonomous roles:** create/publish a requisition, accept or edit its monthly cap, and click **Turn on**. That click persists a durable server-side command; the browser may close immediately. The platform then generates and repairs the assessment, runs its sandbox battle test, provisions/verifies its repository, approves that exact passing draft, checks production readiness, opens the native job, starts the first complete cohort pass, and continuously processes incoming native/Workable applications. Publish itself is spend-free, and there is no separate Tasks-page setup or second approval click. Transient failures retry automatically; genuinely unusable job input or exhausted automated repair is surfaced as a human-input state. Irreversible reject recommendations remain human-confirmed.

### Frontend (Vercel)

- **Stack:** Vite 5, React 18, Tailwind; `react-router-dom` route modules, AuthContext, domain API clients in `src/shared/api/*`.
- **Landing:** Hero, problem/solution, features, pricing (£25 pay-per-use, £300 monthly), nav, footer.
- **Auth:** Login, register, forgot/reset password; protected routes.
- **Dashboard:** Four stat cards (active assessments, completion rate, average score, cost this month); assessments table with Candidate, Task, Status, Score, Time, **Assessment link** (Copy link), View; filters and pagination; “Create Assessment” modal (select/create candidate, select task, send).
- **Candidate detail (View):** Header, score card, tabs (Test Results, AI Usage, Timeline, Code Review), CV/Job Fit analysis, and recruiter action buttons (Download PDF, Post to Workable, Delete).
- **Tasks:** List with View (all tasks, including templates) and Edit/Delete for non-templates.
- **Settings:** Workable tab (Connect Workable, OAuth callback at `/settings/workable/callback`); Billing tab (usage, “Add credits” → Stripe Checkout).
- **Candidate flow:** `/assess/{token}` → welcome → start → AssessmentPage (Monaco, Claude chat, Run Code, execute, submit) → results.

### Deployment

- **Backend:** Railway. The supported web bootstrap validates production config,
  waits for PostgreSQL, applies `alembic upgrade head`, then starts uvicorn.
  PostgreSQL and Redis are Railway add-ons; repository deployment wrappers select
  and validate web plus the general and scoring workers.
- **Frontend:** Vercel; build from `frontend/` with `npm run build`; `VITE_API_URL` points to backend.
- **Docs:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (Vercel + Railway only), [docs/ENV_SETUP.md](docs/ENV_SETUP.md).

**Live (current):**
- **Frontend:** https://frontend-psi-navy-15.vercel.app — set `VITE_API_URL` in Vercel to your Railway backend URL so login/dashboard work.
- **Backend:** Deploy from `backend/` with Railway; use the dashboard to get the backend service name and run `railway up -s <name>` if needed.

---

## Repository layout

```
taali-platform/
├── backend/                 # FastAPI app
│   ├── app/
│   │   ├── api/v1/          # auth, assessments, tasks, organizations, billing, analytics, webhooks
│   │   ├── core/            # config, database, security, middleware
│   │   ├── models/          # SQLAlchemy models
│   │   ├── schemas/         # Pydantic request/response
│   │   ├── services/        # E2B, Claude, Stripe, Workable, email
│   │   └── tasks/           # Celery (invite email, results email, post to Workable)
│   ├── alembic/             # Migrations
│   ├── Procfile
│   ├── railway.json
│   └── requirements.txt
├── frontend/                # Vite + React
│   ├── src/
│   │   ├── App.jsx          # Route shell and lazy feature page composition
│   │   ├── context/AuthContext.jsx
│   │   ├── shared/api/*     # Domain API clients (VITE_API_URL)
│   │   └── components/assessment/
│   ├── package.json
│   └── vite.config.js
├── docs/
│   ├── DEPLOYMENT.md        # How to deploy (Vercel + Railway)
│   ├── ENV_SETUP.md         # All env vars
│   └── API.md
├── RALPH_TASK.md            # Full success criteria and current state
└── README.md                # This file
```

---

## Quick start (deployed setup)

1. **Backend + two workers (Railway)**
   - New project; add PostgreSQL and Redis, plus web, general-worker, and scoring-worker services from `backend/`.
   - Share the production env set across all three services (see [ENV_SETUP.md](docs/ENV_SETUP.md)): `DEPLOYMENT_ENV=production`, `AUTO_GENERATE_ASSESSMENT_TASKS=true`, `SECRET_KEY`, `ANTHROPIC_API_KEY`, pinned model variables, `E2B_API_KEY`, `RESEND_API_KEY`, real GitHub credentials, `REDIS_URL`, `DATABASE_URL`, `FRONTEND_URL`, and `BACKEND_URL`.
   - Run `./scripts/railway/deploy_production.sh`. It pins and validates live metering and native apply on all three services, migrates via production `DATABASE_PUBLIC_URL`, deploys general `celery` + Beat, deploys scoring-only without Beat, deploys web, then polls `/ready`.
   - The shared `backend/railway.json` deliberately has no HTTP healthcheck because Celery workers do not serve one; the wrapper's final gate validates web, both queue canaries, and live Anthropic/E2B/Resend/GitHub capability for the default assessment path.

2. **Frontend (Vercel)**  
   - Import or deploy from `frontend/`.  
   - Set `VITE_API_URL=https://<your-backend>.up.railway.app` and `VITE_STRIPE_PUBLISHABLE_KEY`.  
   - Redeploy.  
   - Open `https://<your-app>.vercel.app` — login/register and dashboard should work.

3. **Webhooks (after backend URL is fixed)**  
   - Stripe: endpoint `https://<backend>/api/v1/webhooks/stripe`, set `STRIPE_WEBHOOK_SECRET`.  
   - Workable: endpoint `https://<backend>/api/v1/webhooks/workable`, set `WORKABLE_WEBHOOK_SECRET`.  
   See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for event lists.

4. **Fund usage once**
   - Fund the organization credit ledger. Turn on remains durably queued and the role stays off when it cannot afford one conservative funnel pass; after a top-up or restored dependency, recovery continues automatically. Process Candidates and manual sync remain recovery controls, not requisition steps.

---


## Known limitations

- Production smoke tests hit live infrastructure and are intentionally separated from the default backend run (`pytest -m "production"`).
- Frontend tests currently pass with residual React `act(...)` warnings in some suites; behavior is validated but cleanup is still recommended.
- Frontend is code-split by route/module; largest remaining vendor chunk is `charts_vendor` (Recharts-heavy) and should stay monitored.

---

## Not yet implemented (optional / polish)

- **Candidate detail actions:** Additional one-off recruiter exports/actions can still be added, but they are not part of the autonomous requisition funnel.
- **Workable stage mapping:** Candidate import, scoring, invite delivery, and the configured assessment-stage/note handoff are automatic for linked agent roles. Configure the organization's interview-stage target once to allow autonomous Workable advancement; without it, the local pipeline advances and the external move remains a safe human handoff.
- **Stripe webhooks:** Checkout session is used; extra handlers (e.g. `payment_intent.succeeded`) can be added.
- **Frontend test quality:** test script exists and suite passes, but there are remaining `act(...)` warnings to clean up for quieter CI logs.
- **Monitoring:** Structured JSON logs and readiness checks are built in. External error tracking, independent uptime checks, and managed database backups remain recommended production operations.

---

## Tech stack summary

| Layer      | Technology |
|-----------|------------|
| Backend   | FastAPI, Python 3.11+, PostgreSQL 15, SQLAlchemy 2, Alembic, Redis, Celery, JWT |
| Frontend  | Vite 5, React 18, Tailwind CSS, Monaco Editor, react-router-dom routing |
| Execution | E2B Code Interpreter SDK |
| AI        | Anthropic Claude (pinned Haiku 4.5 snapshot by default; explicit snapshot overrides only) |
| ATS       | Workable (OAuth + webhooks) |
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
- **Full task list and criteria:** [RALPH_TASK.md](RALPH_TASK.md)  
- **Observability metrics:** [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)
