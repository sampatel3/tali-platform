# TALI Deployment Guide

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
# From repo root (backend is the service directory)
cd backend && railway up
```

Railway will detect the `railway.json` configuration and:
1. Build with Nixpacks (auto-detects Python + `requirements.txt`)
2. Run `alembic upgrade head` to apply database migrations
3. Start the uvicorn server on the assigned `$PORT`

### 5. Verify

```bash
curl https://your-backend.up.railway.app/health
# Expected: {"status":"healthy","service":"tali-api"}
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

## Custom Domain Configuration

### Backend (Railway)

1. Railway dashboard → your service → **Settings** → **Domains**
2. Click **Add Custom Domain**
3. Enter your domain (e.g., `api.tali.dev`)
4. Add the provided CNAME record to your DNS provider
5. Wait for DNS propagation and SSL provisioning
6. Update `BACKEND_URL` env var to `https://api.tali.dev`

### Frontend (Vercel)

1. Vercel dashboard → your project → **Settings** → **Domains**
2. Click **Add Domain**
3. Enter your domain (e.g., `app.tali.dev`)
4. Add the provided CNAME or A records to your DNS provider
5. Vercel automatically provisions SSL
6. Update `FRONTEND_URL` in the Railway backend env vars to `https://app.tali.dev`

### Important

After changing domains, update these values everywhere:
- `FRONTEND_URL` in Railway (used for CORS and OAuth redirects)
- `BACKEND_URL` in Railway
- `VITE_API_URL` in Vercel
- Stripe webhook endpoint URL
- Workable webhook endpoint URL
- Workable OAuth redirect URI
