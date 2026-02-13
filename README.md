# TALI Platform

**AI-augmented technical assessment** for screening engineers who work *with* AI tools. Candidates code in-browser (Monaco + Claude chat), run code in E2B sandboxes, and recruiters get scores, timelines, and optional ATS (Workable) and billing (Stripe) integration.

---

## What’s Implemented and Deployed

The core platform is implemented and deployable end-to-end, and active execution priorities now live in `RALPH_TASK.md` (current hardening plan).

### Backend (Railway)

- **Stack:** FastAPI, PostgreSQL (SQLAlchemy 2 + Alembic), Redis, Celery.
- **Auth:** Register, login, JWT (`/api/v1/auth/*`), forgot/reset password.
- **Assessments:** Create (candidate inline), list (filters, pagination, `candidate_name` / `candidate_email` / `task_name`), get by id, start by token, execute code, Claude chat, submit. E2B sandbox create/reuse, timeline and results persisted.
- **Tasks:** List, get, create, PATCH, DELETE; template vs org tasks.
- **Organizations:** Get, update; Workable OAuth: `GET authorize-url`, `POST workable/connect`.
- **Billing:** `GET usage`, `GET costs` (per-assessment + per-tenant infrastructure cost estimates), `POST checkout-session` (Stripe Checkout, £25).
- **Other:** Analytics endpoint, rate limiting (auth + assessment), invite + results emails via Celery. Health: `GET /health`.

### Frontend (Vercel)

- **Stack:** Vite 5, React 18, Tailwind; hash routing (`#/...`), AuthContext, `src/lib/api.js`.
- **Landing:** Hero, problem/solution, features, pricing (£25 pay-per-use, £300 monthly), nav, footer.
- **Auth:** Login, register, forgot/reset password; protected routes.
- **Dashboard:** Four stat cards (active assessments, completion rate, average score, cost this month); assessments table with Candidate, Task, Status, Score, Time, **Assessment link** (Copy link), View; filters and pagination; “Create Assessment” modal (select/create candidate, select task, send).
- **Candidate detail (View):** Header, score card, tabs (Test Results, AI Usage, Timeline, Code Review), CV/Job Fit analysis, and recruiter action buttons (Download PDF, Post to Workable, Delete).
- **Tasks:** List with View (all tasks, including templates) and Edit/Delete for non-templates.
- **Settings:** Workable tab (Connect Workable, OAuth callback at `#/settings/workable/callback`); Billing tab (usage, “Add credits” → Stripe Checkout).
- **Candidate flow:** `#/assess/{token}` → welcome → start → AssessmentPage (Monaco, Claude chat, Run Code, execute, submit) → results.

### Deployment

- **Backend:** Railway. Start runs `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`. PostgreSQL and Redis as Railway add-ons. If the project has multiple services, run `railway up --service <your-backend-service-name>` from `backend/` (service name is in the Railway dashboard).
- **Frontend:** Vercel; build from `frontend/` with `npm run build`; `VITE_API_URL` points to backend.
- **Docs:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (Vercel + Railway only), [docs/ENV_SETUP.md](docs/ENV_SETUP.md).

**Live (current):**
- **Frontend:** https://frontend-psi-navy-15.vercel.app — set `VITE_API_URL` in Vercel to your Railway backend URL so login/dashboard work.
- **Backend:** Deploy from `backend/` with Railway; use the dashboard to get the backend service name and run `railway up -s <name>` if needed.

---

## Repository layout

```
tali-platform/
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
│   │   ├── App.jsx          # Hash routing, all main views
│   │   ├── context/AuthContext.jsx
│   │   ├── lib/api.js       # Axios client (VITE_API_URL)
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

1. **Backend (Railway)**  
   - New project; add PostgreSQL and Redis.  
   - Set env vars (see [ENV_SETUP.md](docs/ENV_SETUP.md)): `SECRET_KEY`, `E2B_API_KEY`, `ANTHROPIC_API_KEY`, `STRIPE_*`, `WORKABLE_*`, `RESEND_API_KEY`, `REDIS_URL`, `DATABASE_URL`, `FRONTEND_URL`, `BACKEND_URL`.  
   - Deploy from `backend/` (e.g. `railway up`).  
   - Confirm: `curl https://<your-backend>.up.railway.app/health` → `{"status":"healthy","service":"tali-api"}`.

2. **Frontend (Vercel)**  
   - Import or deploy from `frontend/`.  
   - Set `VITE_API_URL=https://<your-backend>.up.railway.app` and `VITE_STRIPE_PUBLISHABLE_KEY`.  
   - Redeploy.  
   - Open `https://<your-app>.vercel.app` — login/register and dashboard should work.

3. **Webhooks (after backend URL is fixed)**  
   - Stripe: endpoint `https://<backend>/api/v1/webhooks/stripe`, set `STRIPE_WEBHOOK_SECRET`.  
   - Workable: endpoint `https://<backend>/api/v1/webhooks/workable`, set `WORKABLE_WEBHOOK_SECRET`.  
   See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for event lists.

4. **Celery**  
   - Email and “post to Workable” tasks require a Celery worker (e.g. separate Railway service) with the same `REDIS_URL` and app env: `celery -A app.tasks worker --loglevel=info`. If no worker is run, invite/results emails and Workable post won’t run.

---


## Known limitations

- Production smoke tests hit live infrastructure and are intentionally separated from the default backend run (`pytest -m "production"`).
- Frontend tests currently pass with residual React `act(...)` warnings in some suites; behavior is validated but cleanup is still recommended.
- Frontend bundle size warning remains (~768KB main JS chunk), so additional code-splitting is still advised.

---

## Not yet implemented (optional / polish)

- **Candidate detail actions:** “Download PDF”, “Post to Workable” (button in UI), “Delete” assessment. (Backend has `posted_to_workable` and Celery task; no recruiter-triggered API/UI yet.)
- **Candidate workflow depth:** candidate CRUD exists, but multi-step creation (assign task + review/send in one guided flow) is not complete.
- **Stripe webhooks:** Checkout session is used; extra handlers (e.g. `payment_intent.succeeded`) can be added.
- **Workable webhooks:** Endpoint exists; auto-create assessment / post on completion can be extended.
- **Frontend test quality:** test script exists and suite passes, but there are remaining `act(...)` warnings to clean up for quieter CI logs.
- **Monitoring:** Sentry, structured JSON logs, uptime checks, DB backups not required for current deployment but recommended for production.

---

## Tech stack summary

| Layer      | Technology |
|-----------|------------|
| Backend   | FastAPI, Python 3.11+, PostgreSQL 15, SQLAlchemy 2, Alembic, Redis, Celery, JWT |
| Frontend  | Vite 5, React 18, Tailwind CSS, Monaco Editor, hash routing |
| Execution | E2B Code Interpreter SDK |
| AI        | Anthropic Claude (environment-tiered: Haiku non-prod, configurable production) |
| ATS       | Workable (OAuth + webhooks) |
| Payments  | Stripe (Checkout, usage) |
| Email     | Resend |
| Hosting   | Backend: Railway; Frontend: Vercel |

---

## Links

- **Deployment:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)  
- **Environment variables:** [docs/ENV_SETUP.md](docs/ENV_SETUP.md)  
- **Full task list and criteria:** [RALPH_TASK.md](RALPH_TASK.md)  
- **Observability metrics:** [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)
