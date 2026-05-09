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


### Claude Model Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CLAUDE_MODEL` | No | `claude-3-5-haiku-latest` | Model for assessment terminal, chat, and general use. |
| `CLAUDE_SCORING_BATCH_MODEL` | No | `claude-3-5-haiku-latest` | Cost-optimised model used by batch scoring jobs. Falls back to `CLAUDE_MODEL` when unset. |
| `CLAUDE_SCORING_MODEL` | No | `""` | **Deprecated.** Old single-model selector. If set it must equal `CLAUDE_MODEL`, otherwise startup fails. Leave unset on new deployments. |
| `MAX_TOKENS_PER_RESPONSE` | No | `1024` | Maximum tokens returned per Claude response. |
| `ANTHROPIC_ADMIN_API_KEY` | No | `""` | Anthropic Admin API key for provisioning per-org workspace keys. Empty = workspace provisioning disabled, all calls fall back to `ANTHROPIC_API_KEY`. |

### Cost Observability Controls

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CLAUDE_INPUT_COST_PER_MILLION_USD` | No | `1.0` | Input token cost model. Default tracks Claude Haiku 4.5 — the model the platform routes to today. The pre-2026 defaults (`0.25` / `1.25`) were Haiku 3.5 rates and produced ~4x under-counts in the Anthropic reconciliation panel. |
| `CLAUDE_OUTPUT_COST_PER_MILLION_USD` | No | `5.0` | Output token cost model. See note above. |
| `USAGE_METER_LIVE` | No | `false` | When `false`, every Claude call writes a `usage_events` row but the credit ledger is **not** debited and gates do **not** block — shadow mode for validating attribution before flipping live. Set to `true` once shadow data confirms the meter matches Anthropic's dashboard. |
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
| `ASSESSMENT_PRICE_CURRENCY` | No | `aed` | ISO currency code used in Stripe checkout sessions. |
| `ASSESSMENT_PRICE_MAJOR` | No | `25` | Price per assessment in major currency units (used in display copy and receipts). |
| `ASSESSMENT_PRICE_MINOR` | No | `2500` | Price per assessment in minor units (e.g. fils/cents) — what Stripe actually bills. |
| `ASSESSMENT_PRICE_PENCE` | No | `2500` | **Deprecated** alias for `ASSESSMENT_PRICE_MINOR`. Original sunset target (2026-04-15) has passed; remove from any active env files. |
| `ASSESSMENT_EXPIRY_DAYS` | No | `7` | Number of days before an assessment invite link expires. |
| `EMAIL_FROM` | No | `TAALI <noreply@taali.ai>` | Sender address used by all transactional emails. |
| `SCORE_WEIGHTS` | No | JSON defaults | JSON string for composite scoring weights (tests, code_quality, prompt_quality, etc.). |
| `DEFAULT_CALIBRATION_PROMPT` | No | Reverse-string prompt | Baseline calibration prompt used when a task does not define `calibration_prompt`. |
| `ASSESSMENT_TERMINAL_ENABLED` | No | `true` | Hard gate on the terminal-native Claude Code runtime. Startup fails fast if set to `false`. |
| `ASSESSMENT_TERMINAL_ALLOW_GLOBAL_KEY_FALLBACK` | No | `false` | Strict by default — candidate sessions must use a per-org workspace key, never the platform-wide `ANTHROPIC_API_KEY`. |
| `CLAUDE_CLI_PERMISSION_MODE_DEFAULT` | No | `acceptEdits` | Default `--permission-mode` for the Claude Code CLI. |
| `CLAUDE_CLI_DISALLOWED_TOOLS` | No | `Bash` | Comma-separated tools blocked from the candidate Claude Code session. |

### Pre-Screen Gate

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ENABLE_PRE_SCREEN_GATE` | No | `false` | When `true`, every v3 score is preceded by a cheap pre-screen LLM call (~$0.0002/CV); candidates below `PRE_SCREEN_THRESHOLD` skip full scoring entirely. |
| `PRE_SCREEN_THRESHOLD` | No | `30` | Numeric threshold (0-100) for the pre-screen gate. |
| `FRAUD_COPY_PASTE_THRESHOLD` | No | `0.05` | When the copy-paste fraction of CV-vs-JD exceeds this, the candidate's pre-screen score is capped at `FRAUD_PENALTY_CAP_SCORE`. Set to `1.0` to disable. |
| `FRAUD_PENALTY_CAP_SCORE` | No | `10.0` | Score cap applied to fraud-positive candidates. Defaults below `PRE_SCREEN_THRESHOLD` so fraud-positive always skips full scoring. |

### GitHub (Assessment Repositories)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GITHUB_TOKEN` | **Yes** in production | `""` | Personal access token used to create assessment branches and push starter code. |
| `GITHUB_ORG` | No | `taali-assessments` | GitHub org that owns one repo per task; assessments push to a branch named `assessment/<id>`. |
| `GITHUB_MOCK_MODE` | No | `false` | When `true`, repo writes are stubbed to a local fixture root (`GITHUB_MOCK_ROOT`). Used in tests and local dev. |

### Knowledge Graph (Optional)

The candidate knowledge-graph view and graph predicates in NL search are powered by Neo4j + Graphiti. When `NEO4J_URI` is blank these features degrade gracefully (the graph view shows a configuration hint and graph predicates drop out of NL queries).

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NEO4J_URI` | No | `""` | Neo4j Bolt URL. Production is the Railway Neo4j template; local dev typically leaves blank. |
| `NEO4J_USER` | No | `neo4j` | Neo4j auth user. |
| `NEO4J_PASSWORD` | No | `""` | Neo4j auth password. |
| `NEO4J_DATABASE` | No | `neo4j` | Neo4j database name. |
| `VOYAGE_API_KEY` | No | `""` | Voyage AI key used by Graphiti for embeddings. Empty disables Graphiti entirely (graph features behave as if Neo4j were unset). |
| `GRAPHITI_LLM_MODEL` | No | `claude-haiku-4-5-20251001` | Anthropic model used by Graphiti for entity extraction. Reuses `ANTHROPIC_API_KEY`. |
| `GRAPHITI_LLM_SMALL_MODEL` | No | `claude-haiku-4-5-20251001` | Smaller-task variant of the above. |
| `GRAPHITI_EMBEDDING_MODEL` | No | `voyage-3` | Voyage embedding model. |
| `GRAPHITI_EMBEDDING_DIMS` | No | `1024` | Vector dim for the embedding model. Must match the model's native dim. |
| `GRAPHITI_MAX_EPISODES_PER_CANDIDATE` | No | `40` | Hard cap on per-candidate Graphiti episodes during backfill — guards against runaway LLM cost on candidates with hundreds of experience entries. |

### Feature Flags

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MVP_DISABLE_STRIPE` | No | `false` | Stripe is the live payment processor for credit top-ups (since 2026-04-29). |
| `MVP_DISABLE_WORKABLE` | No | `true` | Disables Workable ATS sync; the integration is feature-flagged off by default. |
| `MVP_DISABLE_CLAUDE_SCORING` | No | `true` | Disables the v3 scoring pipeline; assessments fall back to deterministic scoring. |
| `MVP_DISABLE_CALIBRATION` | No | `false` | Calibration is enabled by default (`false`). |
| `MVP_DISABLE_PROCTORING` | No | `true` | Proctoring signals (browser focus, tab switches) are recorded but not gated on. |
| `SCORING_V2_ENABLED` | No | `false` | Legacy scoring pipeline toggle; v2 is untested in production. |
| `TASK_AUTHORING_API_ENABLED` | No | `false` | Gates the task-authoring API (tasks are backend-authored by default). |
| `AI_ASSISTED_EVAL_ENABLED` | No | `false` | Gates the v2 AI-assisted evaluator (suggestions only). |
| `ADMIN_SECRET` | No | `""` | Required to call `/admin/*` debug routes. Leave blank to disable admin debug entirely. |

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

**Important (Vercel):** When setting `VITE_API_URL` in the Vercel dashboard, ensure there is **no trailing newline or space**. A literal `\n` at the end can break API requests. The frontend shared API client strips whitespace defensively, but fix the value at the source.
