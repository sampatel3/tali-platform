# TAALI Deployment Guide

This guide covers deployment only (Vercel for frontend, Railway for backend). Local run is not required.

## Prerequisites

You will need accounts on the following services:

| Service | Purpose | Sign up |
|---------|---------|---------|
| [Railway](https://railway.app) | Backend hosting + Postgres + Redis | railway.app |
| [Vercel](https://vercel.com) | Frontend hosting | vercel.com |
| [Stripe](https://stripe.com) | Payment processing | dashboard.stripe.com |
| [E2B](https://e2b.dev) | Code sandbox execution | e2b.dev |
| [Anthropic](https://console.anthropic.com) | Claude AI API | console.anthropic.com |
| [Resend](https://resend.com) | Transactional email | resend.com |

Optional:
- **Workable** — ATS integration; Taali-native requisitions do not require it
- **AWS S3** — for assessment artifact storage
- **Sentry** — for error monitoring

---

## Backend Deployment (Railway)

### Railway CLI contract (default path)

Always deploy backend services through repository wrapper scripts from repo root:

```bash
./scripts/railway/check_status.sh
./scripts/railway/deploy_production.sh
./scripts/railway/fetch_logs.sh resourceful-adaptation
```

Workers-only recovery (always deploys and validates both workers):

```bash
RAILWAY_WORKER_SERVICE=<general-worker-service> \
RAILWAY_SCORING_WORKER_SERVICE=<scoring-worker-service> \
  ./scripts/railway/deploy_worker.sh
```

Why this matters:
- Deploys are forced from `backend/` so Railway does not attempt a repo-root build.
- Web, general-worker, and scoring-worker services are validated by exact name.
- The coordinated wrapper pins live metering, migrates production, deploys both
  workers, deploys web, and waits for public `/ready`.
- This avoids `Railpack could not determine how to build app` failures caused by wrong root directory detection.
- Manual Workable sync runs are queued to Celery when enabled, so keep the worker service deployed for durable background execution.

### 1. Create a new Railway project

```bash
# Install the Railway CLI
npm install -g @railway/cli

# Login
railway login

# Initialize from the backend directory
cd backend
railway init
```

### 2. Provision databases

Add the following services to your Railway project via the dashboard:

- **PostgreSQL** — click "New" → "Database" → "PostgreSQL"
- **Redis** — click "New" → "Database" → "Redis"

Railway automatically injects `DATABASE_URL` and `REDIS_URL` for provisioned databases. You do **not** need to set these manually.

### 3. Configure environment variables

In the Railway dashboard, go to your backend service → **Variables** and add:

```
SECRET_KEY=<generate with: openssl rand -hex 32>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

E2B_API_KEY=<from e2b.dev dashboard>
ANTHROPIC_API_KEY=<from console.anthropic.com>
CLAUDE_MODEL=claude-haiku-4-5-20251001
CLAUDE_SCORING_BATCH_MODEL=claude-haiku-4-5-20251001

DEPLOYMENT_ENV=production
USAGE_METER_LIVE=true
ATS_PUBLIC_APPLY_ENABLED=true

# Only when Workable ATS sync is enabled:
WORKABLE_CLIENT_ID=<from Workable partner portal>
WORKABLE_CLIENT_SECRET=<from Workable partner portal>
WORKABLE_WEBHOOK_SECRET=<from Workable webhook settings>

STRIPE_API_KEY=<from Stripe dashboard → API keys>
STRIPE_WEBHOOK_SECRET=<from Stripe webhook endpoint>

RESEND_API_KEY=<from resend.com dashboard>

GITHUB_TOKEN=<real token with access to the assessment org>
GITHUB_MOCK_MODE=false

FRONTEND_URL=https://your-app.vercel.app
BACKEND_URL=https://your-backend.up.railway.app
```

See [ENV_SETUP.md](./ENV_SETUP.md) for the full variable reference.

### 4. Create the two Celery worker services before web rollout

Production uses three application services from the same `backend/` root:

| Service | Mode | Queues | Beat |
|---|---|---|---|
| Web | `web` (default) | — | — |
| General worker | `worker` | `celery` | `true` |
| Scoring worker | `worker` | `scoring` | `false` |

Create both worker services from the same repository and set each **Root
Directory** to `backend`. Use Railway shared variables so all three services
receive the same production runtime set: `DATABASE_URL`, `REDIS_URL`,
`SECRET_KEY`, `DEPLOYMENT_ENV`, `AUTO_GENERATE_ASSESSMENT_TASKS=true`,
`ANTHROPIC_API_KEY`, pinned model variables, `FRONTEND_URL`, `BACKEND_URL`, and
`ATS_PUBLIC_APPLY_ENABLED`; assessment-enabled deployments also need `E2B_API_KEY`,
`RESEND_API_KEY`, `GITHUB_TOKEN`, `GITHUB_ORG`, and `GITHUB_MOCK_MODE=false`.
Optional provider credentials used by tasks must also be shared.

The wrapper pins the exact queue and Beat variables before every rollout. Only
the general worker owns Beat. The scoring service is scoring-only and never
runs a second scheduler.

`backend/railway.json` is shared by all three services and therefore does not
declare Railway's HTTP `healthcheckPath`: Celery processes do not serve HTTP.
Public web readiness is polled explicitly after deployment instead.

### 5. Run the coordinated production rollout

Use the single orchestrator from repo root. Substitute names only if the
Railway services were renamed:

```bash
RAILWAY_ENVIRONMENT=production \
RAILWAY_BACKEND_SERVICE=resourceful-adaptation \
RAILWAY_WORKER_SERVICE=taali-worker \
RAILWAY_SCORING_WORKER_SERVICE=taali-worker-scoring \
RAILWAY_BACKEND_URL=https://resourceful-adaptation-production.up.railway.app \
  ./scripts/railway/deploy_production.sh
```

The order is enforced:

1. Set `USAGE_METER_LIVE=true` with `--skip-deploys` on web and both workers,
   then read back and validate all three values.
2. Resolve the web service's production `DATABASE_PUBLIC_URL`, run
   `python -m alembic upgrade head` separately from service startup, then run
   `python -m alembic current` against the same public database.
3. Pin and validate `taali-worker` as `queues=celery`, `Beat=true`; deploy it and
   wait for a new `SUCCESS` deployment ID.
4. Pin and validate `taali-worker-scoring` as `queues=scoring`, `Beat=false`;
   deploy it and wait for its own new `SUCCESS` deployment ID.
5. Deploy web, wait for its new Railway deployment to succeed, then poll public
   `/ready` until the API confirms both queue canaries and production providers.

Any missing service, duplicate service name, wrong topology variable, failed
deployment, migration failure, or readiness timeout makes the wrapper exit
non-zero. A single healthy worker cannot produce a successful rollout.

### 6. Verify autonomous Turn on readiness

The coordinated wrapper performs these checks. They can also be repeated
read-only:

```bash
RAILWAY_ENVIRONMENT=production \
RAILWAY_BACKEND_SERVICE=resourceful-adaptation \
RAILWAY_WORKER_SERVICE=taali-worker \
RAILWAY_SCORING_WORKER_SERVICE=taali-worker-scoring \
  ./scripts/railway/check_status.sh

curl --fail-with-body \
  https://resourceful-adaptation-production.up.railway.app/health
curl --fail-with-body \
  https://resourceful-adaptation-production.up.railway.app/ready
```

`/health` provides diagnostics; production `/ready` returns success only when
live usage metering, both `celery` and `scoring` workers, and their live model
access are healthy. The workers also verify GitHub access and perform
one daily send to Resend's non-delivering test recipient; an assessment-enabled
role cannot Turn on until its worker has proved the configured sender/key works.
The agent sweep runs hourly, but Turn on and Resume enqueue a complete role pass
immediately.

This is one-time platform setup. The healthy per-role workflow is:

1. Create/publish the requisition.
2. Accept or edit the monthly cap and click **Turn on**. The click authorizes
   a durable server-side workflow. The browser may close immediately: task
   generation/repair, sandbox battle testing, repository verification, exact
   draft approval, production readiness, activation, and the first cohort pass
   continue from persisted state. Skipping the stage is an optional override,
   not a required second decision.

Activation applies the Agent settings already visible on the role. On an
untouched workspace, reversible assessment send/resend and interview advance
start on, while deterministic rejection and assessment skipping stay off. It
opens a native requisition for applications, starts the full funnel pass, and
fails closed if a dependency or the minimum funded credit balance is missing.
No routine Process Candidates or Workable sync click is required. Human action
remains intentional for
irreversible reject confirmation, ambiguous/off-policy exceptions, and
restoring external funding/credentials after a hold. Once a system hold's cause
is healthy, the recovery sweeps resume the workflow and dispatch a full pass
automatically; only recruiter-authored pauses require an explicit Resume.

### Common crash causes

If Railway is restart-looping during boot, the new bootstrap scripts now log the exact reason. The most common causes are:

- `DATABASE_URL` still pointing at `localhost` because the PostgreSQL service was not attached/shared into the app service
- `REDIS_URL` still pointing at `localhost` for the worker service
- `SECRET_KEY` still using the insecure default while `FRONTEND_URL` is set to a real production domain
- `ASSESSMENT_TERMINAL_ENABLED=false` or `ASSESSMENT_TERMINAL_DEFAULT_MODE` set to anything except `claude_cli_terminal`

You can preflight the web service config locally from `backend/` with:

```bash
python -m app.scripts.railway_start --check-only
```

---

## Frontend Deployment (Vercel)

### 1. Install and login

```bash
npm install -g vercel
vercel login
```

### 2. Deploy from the frontend directory

```bash
cd frontend
vercel
```

Follow the prompts:
- **Framework Preset**: Vite
- **Build Command**: `npm run build`
- **Output Directory**: `dist`
- **Install Command**: `npm install`

### 3. Configure environment variables

In the Vercel dashboard → your project → **Settings** → **Environment Variables**:

```
VITE_API_URL=https://your-backend.up.railway.app
VITE_STRIPE_PUBLISHABLE_KEY=pk_live_...
```

### 4. Redeploy with variables

```bash
vercel --prod
```

### 5. Verify

Open `https://your-app.vercel.app` in a browser — the login page should load and API calls should reach the Railway backend.

---

## Environment Variables Reference

See [ENV_SETUP.md](./ENV_SETUP.md) for the complete list of backend and frontend environment variables, including which are required vs. optional and where to obtain each key.

---

## Post-Deployment: Webhook Setup

### Stripe Webhooks

1. Go to [Stripe Dashboard → Webhooks](https://dashboard.stripe.com/webhooks)
2. Click **Add endpoint**
3. URL: `https://your-backend.up.railway.app/api/v1/webhooks/stripe`
4. Select events:
   - `payment_intent.succeeded`
   - `customer.subscription.deleted`
   - `customer.subscription.updated`
   - `invoice.payment_failed`
5. Copy the **Signing secret** → set as `STRIPE_WEBHOOK_SECRET` in Railway

### Workable Webhooks

1. In your Workable account → **Integrations** → **Webhooks**
2. Add a new webhook
3. URL: `https://your-backend.up.railway.app/api/v1/webhooks/workable`
4. Select events:
   - `candidate_stage_changed`
   - `candidate_created`
5. Copy the **Secret** → set as `WORKABLE_WEBHOOK_SECRET` in Railway

---

## Database Backups (Railway)

Production requires automated PostgreSQL backups.

1. Open Railway dashboard → your PostgreSQL service
2. Go to **Backups**
3. Enable automated backups (daily minimum, 7+ day retention)
4. Create a manual backup before major migrations/deployments
5. Test restore quarterly to confirm disaster recovery works

Recommended policy:
- Daily automated backups
- 14-day retention
- Manual pre-release snapshot for every production rollout

---

## Production Smoke (Test Account)

Use the production test account (`sampatel@deeplight.ae`) via env vars; never commit secrets:

```bash
export TAALI_TEST_EMAIL=sampatel@deeplight.ae
export TAALI_TEST_PASSWORD='<secure-secret>'
export TAALI_API_BASE_URL='https://resourceful-adaptation-production.up.railway.app/api/v1'
```

Run Workable metadata sync smoke:

```bash
./scripts/qa/prod_account_workable_smoke.sh
```

Run model policy smoke (Haiku check):

```bash
EXPECTED_CLAUDE_MODEL=claude-haiku-4-5-20251001 ./scripts/qa/prod_model_smoke.sh
```

---

## Custom Domain Configuration

### Backend (Railway)

1. Railway dashboard → your service → **Settings** → **Domains**
2. Click **Add Custom Domain**
3. Enter your domain (e.g., `api.taali.ai`)
4. Add the provided CNAME record to your DNS provider
5. Wait for DNS propagation and SSL provisioning
6. Update `BACKEND_URL` env var to `https://api.taali.ai`

### Frontend (Vercel)

1. Vercel dashboard → your project → **Settings** → **Domains**
2. Click **Add Domain**
3. Enter your domain (e.g., `app.taali.ai`)
4. Add the provided CNAME or A records to your DNS provider
5. Vercel automatically provisions SSL
6. Update `FRONTEND_URL` in the Railway backend env vars to `https://app.taali.ai`

### Important

After changing domains, update these values everywhere:
- `FRONTEND_URL` in Railway (used for CORS and OAuth redirects)
- `BACKEND_URL` in Railway
- `VITE_API_URL` in Vercel
- Stripe webhook endpoint URL
- Workable webhook endpoint URL
- Workable OAuth redirect URI
