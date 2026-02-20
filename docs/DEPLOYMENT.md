# TAALI Deployment Guide

This guide covers deployment only (Vercel for frontend, Railway for backend). Local run is not required.

## Prerequisites

You will need accounts on the following services:

| Service | Purpose | Sign up |
|---------|---------|---------|
| [Railway](https://railway.app) | Backend hosting + Postgres + Redis | railway.app |
| [Vercel](https://vercel.com) | Frontend hosting | vercel.com |
| [Stripe](https://stripe.com) | Payment processing | dashboard.stripe.com |
| [Workable](https://www.workable.com) | ATS integration | workable.com/partner |
| [E2B](https://e2b.dev) | Code sandbox execution | e2b.dev |
| [Anthropic](https://console.anthropic.com) | Claude AI API | console.anthropic.com |
| [Resend](https://resend.com) | Transactional email | resend.com |

Optional:
- **AWS S3** — for assessment artifact storage
- **Sentry** — for error monitoring

---

## Backend Deployment (Railway)

### Railway CLI contract (default path)

Always deploy backend services through repository wrapper scripts from repo root:

```bash
./scripts/railway/check_status.sh
./scripts/railway/deploy_backend.sh
./scripts/railway/fetch_logs.sh resourceful-adaptation
```

Worker (if present):

```bash
RAILWAY_WORKER_SERVICE=<worker-service-name> ./scripts/railway/deploy_worker.sh
```

Why this matters:
- Deploys are forced from `backend/` so Railway does not attempt a repo-root build.
- Service/environment are validated before deploy.
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

WORKABLE_CLIENT_ID=<from Workable partner portal>
WORKABLE_CLIENT_SECRET=<from Workable partner portal>
WORKABLE_WEBHOOK_SECRET=<from Workable webhook settings>

STRIPE_API_KEY=<from Stripe dashboard → API keys>
STRIPE_WEBHOOK_SECRET=<from Stripe webhook endpoint>

RESEND_API_KEY=<from resend.com dashboard>

FRONTEND_URL=https://your-app.vercel.app
BACKEND_URL=https://your-backend.up.railway.app
```

See [ENV_SETUP.md](./ENV_SETUP.md) for the full variable reference.

### 4. Deploy

```bash
# Default (from repo root)
./scripts/railway/deploy_backend.sh
```

Railway will detect the `railway.json` configuration and:
1. Build with Nixpacks (auto-detects Python + `requirements.txt`)
2. Run `alembic upgrade head` to apply database migrations
3. Start the uvicorn server on the assigned `$PORT`

### 5. Verify

```bash
curl https://your-backend.up.railway.app/health
# Expected: {"status":"healthy","service":"taali-api"}
```

### 6. Celery worker (second Railway service)

When `MVP_DISABLE_CELERY=False`, async tasks (assessment invitation emails, Workable posting) require a Celery worker. Add a **second service** to the same Railway project:

1. In the Railway project dashboard, click **New** → **GitHub Repo** (or reuse the same repo).
2. Set **Root Directory** to `backend` (same as the web service).
3. Railway will use `railway.json` by default; for the worker, either:
   - Set **Override** in the service settings to use `railway.worker.json` (if your CLI/project supports multiple configs), or  
   - In the worker service → **Settings** → **Deploy**, set **Custom start command** to:
     ```bash
     celery -A app.tasks worker --loglevel=info --concurrency=2
     ```
4. Add the **same environment variables** as the web service (or use Railway [shared variables](https://docs.railway.app/develop/variables#shared-variables)): at minimum `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `RESEND_API_KEY`, `ANTHROPIC_API_KEY`, and any keys used by your tasks.

The worker uses the same Redis as the web app as the broker; no extra Redis service is needed.

Deploy worker after the service is created:

```bash
RAILWAY_WORKER_SERVICE=<worker-service-name> ./scripts/railway/deploy_worker.sh
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
EXPECTED_CLAUDE_MODEL=claude-3-5-haiku-latest ./scripts/qa/prod_model_smoke.sh
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
