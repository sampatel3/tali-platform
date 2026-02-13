# TAALI Environment Variables

Complete reference for all environment variables used by the TAALI platform.

---

## Backend Variables

### Database

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | **Yes** | — | PostgreSQL connection string. Set in Railway when using their Postgres add-on. |

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


### Claude Model Tiering (Phase P6)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DEPLOYMENT_ENV` | No | `development` | Deployment environment (`development`, `staging`, `production`). |
| `CLAUDE_MODEL` | No | `None` | Explicit model override for all environments. |
| `CLAUDE_MODEL_NON_PROD` | No | `claude-3-5-haiku-latest` | Default model used for test/staging/dev (cheapest tier). |
| `CLAUDE_MODEL_PRODUCTION` | No | `claude-3-5-sonnet-20241022` | Default production model when `DEPLOYMENT_ENV=production`. |
| `MAX_TOKENS_PER_RESPONSE` | No | `1024` | Maximum tokens returned per Claude response. |

Model resolution precedence: `CLAUDE_MODEL` (if set) → environment default (`CLAUDE_MODEL_PRODUCTION` for production, otherwise `CLAUDE_MODEL_NON_PROD`).

For low-cost local testing, set:

```bash
CLAUDE_MODEL=claude-3-5-haiku-latest
```

### Cost Observability Controls (Phase P6)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CLAUDE_INPUT_COST_PER_MILLION_USD` | No | `0.25` | Input token cost model for Claude usage estimates. |
| `CLAUDE_OUTPUT_COST_PER_MILLION_USD` | No | `1.25` | Output token cost model for Claude usage estimates. |
| `E2B_COST_PER_HOUR_USD` | No | `0.30` | Hourly E2B runtime cost estimate per active assessment sandbox. |
| `EMAIL_COST_PER_SEND_USD` | No | `0.01` | Per-email send cost estimate (invite/results notifications). |
| `STORAGE_COST_PER_GB_MONTH_USD` | No | `0.023` | Storage cost estimate for persisted assessment artifacts. |
| `STORAGE_RETENTION_DAYS_DEFAULT` | No | `30` | Retention window used in storage-cost estimates. |
| `COST_ALERT_DAILY_SPEND_USD` | No | `200.0` | Alert threshold for tenant daily spend estimate. |
| `COST_ALERT_PER_COMPLETED_ASSESSMENT_USD` | No | `10.0` | Alert threshold for cost per completed assessment. |

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
| `REDIS_URL` | **Yes** | — | Redis connection string for Celery task queue. Set in Railway when using their Redis add-on. |

### URLs

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FRONTEND_URL` | **Yes** | — | Frontend app URL (e.g. your Vercel URL). **Must match the browser origin** for CORS; set to your production frontend URL so login and assessment start work. |
| `BACKEND_URL` | **Yes** | — | Backend API URL (e.g. your Railway URL). Used in email templates and webhook configurations. |
| `CORS_EXTRA_ORIGINS` | No | — | Comma-separated extra CORS origins (e.g. a second Vercel URL). Use if you have multiple frontend origins. |

### Assessment and Scoring

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ASSESSMENT_PRICE_PENCE` | No | `2500` | Price per assessment (in pence) used by billing and Stripe flows. |
| `ASSESSMENT_EXPIRY_DAYS` | No | `7` | Number of days before an assessment invite link expires. |
| `EMAIL_FROM` | No | `TAALI <noreply@taali.ai>` | Sender address used by all transactional emails. |
| `SCORE_WEIGHTS` | No | JSON defaults | JSON string for composite scoring weights (tests, code_quality, prompt_quality, etc.). |
| `DEFAULT_CALIBRATION_PROMPT` | No | Reverse-string prompt | Baseline calibration prompt used when a task does not define `calibration_prompt`. |

### AWS S3 (Optional)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AWS_ACCESS_KEY_ID` | No | `None` | AWS IAM access key for S3 uploads. |
| `AWS_SECRET_ACCESS_KEY` | No | `None` | AWS IAM secret key for S3 uploads. |
| `AWS_S3_BUCKET` | No | `taali-assessments` | S3 bucket name for storing assessment artifacts. |
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
| `VITE_API_URL` | **Yes** | — | Backend API base URL (e.g., `https://api.taali.ai`). |
| `VITE_STRIPE_PUBLISHABLE_KEY` | **Yes** | — | Stripe publishable key (starts with `pk_live_` or `pk_test_`). |

**Where to get it:**
- `VITE_API_URL`: your Railway backend URL
- `VITE_STRIPE_PUBLISHABLE_KEY`: [Stripe Dashboard](https://dashboard.stripe.com) → Developers → API keys → Publishable key

**Important (Vercel):** When setting `VITE_API_URL` in the Vercel dashboard, ensure there is **no trailing newline or space**. A literal `\n` at the end can break API requests. The frontend `api.js` strips whitespace defensively, but fix the value at the source.
