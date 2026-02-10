# TALI Environment Variables

Complete reference for all environment variables used by the TALI platform.

---

## Backend Variables

### Database

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | **Yes** | `postgresql://tali:tali_dev_password@localhost:5432/tali_db` | PostgreSQL connection string. Auto-injected by Railway when using their Postgres add-on. |

### Security

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | **Yes** | `dev-secret-key-change-in-production` | JWT signing key. **Must** be changed in production. |
| `ALGORITHM` | No | `HS256` | JWT signing algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `30` | JWT token expiry in minutes. |

#### Generating SECRET_KEY

```bash
# Option 1: OpenSSL (recommended)
openssl rand -hex 32

# Option 2: Python
python -c "import secrets; print(secrets.token_hex(32))"
```

Use the output as the value for `SECRET_KEY`. Never reuse the dev default in production.

### E2B (Code Sandbox)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `E2B_API_KEY` | **Yes** | `""` | API key for E2B code execution sandboxes. |

**Where to get it:** Sign up at [e2b.dev](https://e2b.dev) → Dashboard → API Keys → Create new key.

### Anthropic (Claude AI)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | **Yes** | `""` | API key for the Claude AI assistant. |

**Where to get it:** Sign up at [console.anthropic.com](https://console.anthropic.com) → API Keys → Create Key.

### Workable (ATS Integration)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WORKABLE_CLIENT_ID` | **Yes** | `""` | OAuth2 client ID for Workable integration. |
| `WORKABLE_CLIENT_SECRET` | **Yes** | `""` | OAuth2 client secret for Workable integration. |
| `WORKABLE_WEBHOOK_SECRET` | **Yes** | `""` | Secret used to verify incoming Workable webhook signatures. |

**Where to get it:** Apply for a Workable partner integration at [workable.com](https://www.workable.com) → Partner Portal. You'll receive client credentials after approval.

### Stripe (Payments)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `STRIPE_API_KEY` | **Yes** | `""` | Stripe secret key (starts with `sk_live_` or `sk_test_`). |
| `STRIPE_WEBHOOK_SECRET` | **Yes** | `""` | Webhook signing secret (starts with `whsec_`). |

**Where to get it:**
- API Key: [Stripe Dashboard](https://dashboard.stripe.com) → Developers → API keys
- Webhook Secret: [Stripe Dashboard](https://dashboard.stripe.com) → Developers → Webhooks → your endpoint → Signing secret

### Resend (Email)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RESEND_API_KEY` | **Yes** | `""` | API key for sending transactional emails. |

**Where to get it:** Sign up at [resend.com](https://resend.com) → API Keys → Create API Key.

### Redis

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_URL` | **Yes** | `redis://localhost:6379` | Redis connection string for Celery task queue. Auto-injected by Railway when using their Redis add-on. |

### URLs

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FRONTEND_URL` | **Yes** | `http://localhost:5173` | Frontend app URL. Used for CORS, OAuth redirects, and email links. |
| `BACKEND_URL` | **Yes** | `http://localhost:8000` | Backend API URL. Used in email templates and webhook configurations. |

### AWS S3 (Optional)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AWS_ACCESS_KEY_ID` | No | `None` | AWS IAM access key for S3 uploads. |
| `AWS_SECRET_ACCESS_KEY` | No | `None` | AWS IAM secret key for S3 uploads. |
| `AWS_S3_BUCKET` | No | `tali-assessments` | S3 bucket name for storing assessment artifacts. |
| `AWS_REGION` | No | `us-east-1` | AWS region for the S3 bucket. |

**Where to get it:** [AWS Console](https://console.aws.amazon.com) → IAM → Users → Create user with S3 permissions → Access Keys.

### Sentry (Optional)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SENTRY_DSN` | No | `None` | Sentry DSN for error tracking and performance monitoring. |

**Where to get it:** [sentry.io](https://sentry.io) → Create project (Python / FastAPI) → copy the DSN.

---

## Frontend Variables

These are compile-time variables injected by Vite. They must be prefixed with `VITE_`.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VITE_API_URL` | **Yes** | — | Backend API base URL (e.g., `https://api.tali.dev`). |
| `VITE_STRIPE_PUBLISHABLE_KEY` | **Yes** | — | Stripe publishable key (starts with `pk_live_` or `pk_test_`). |

**Where to get it:**
- `VITE_API_URL`: your Railway backend URL
- `VITE_STRIPE_PUBLISHABLE_KEY`: [Stripe Dashboard](https://dashboard.stripe.com) → Developers → API keys → Publishable key

---

## Local Development Quick Start

1. Copy the example env file:

```bash
cd tali/backend
cp .env.example .env
```

2. Start Postgres and Redis:

```bash
cd tali/
docker-compose up -d
```

3. Generate a dev secret key:

```bash
openssl rand -hex 32
```

4. Edit `.env` and fill in the values. For local dev, only `DATABASE_URL`, `SECRET_KEY`, and `REDIS_URL` are strictly required. Service integrations (E2B, Anthropic, etc.) are needed only for the features that use them.
