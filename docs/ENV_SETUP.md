# TAALI Environment Variables

Complete reference for all environment variables used by the TAALI platform.

---

## Backend Variables

### Deployment

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DEPLOYMENT_ENV` | **Yes in production** | `development` | Set to `production` on every production web and worker service. This explicitly activates production startup safeguards, including live usage-meter enforcement; public URL and Sentry configuration remain defensive production-like signals. |

### Database

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | **Yes** | — | PostgreSQL connection string. Set in Railway when using their Postgres add-on. |
| `DATABASE_PUBLIC_URL` | Deploy tools only | — | Public PostgreSQL URL for migrations/scripts run outside Railway. Runtime web/workers intentionally ignore it and use `DATABASE_URL`. |
| `DATABASE_POOL_SIZE` | No | `5` | Persistent connections per sync/async engine and process. Raise only alongside the Postgres connection budget. |
| `DATABASE_MAX_OVERFLOW` | No | `5` | Temporary overflow connections per engine and process. |
| `DATABASE_WORKSPACE_LOCK_POOL_SIZE` | No | `0` (derive `DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW`) | Lazy, lock-only connections per process for long-lived assessment advisory locks. `0` preserves the prior maximum workspace-provider concurrency without consuming the normal application pool; set a positive cap only after budgeting all web/worker replicas. The lock QueuePool opens connections on demand and has no overflow. |

Budget worst-case per process as two normal sync/async pools, each up to
`DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW`, plus the workspace-lock pool.
Multiply by every web and worker process sharing the database. QueuePool opens
connections lazily, so this is a capacity ceiling rather than an eager startup
cost.

### Security

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | **Yes** | `dev-secret-key-change-in-production` | JWT signing key. Production startup requires a non-default value of at least 32 characters. |
| `INTEGRATION_ENCRYPTION_KEY` | **Yes** | `""` | Dedicated at-rest key for provider credentials. Production startup requires at least 32 characters and a value different from `SECRET_KEY`. |
| `INTEGRATION_ENCRYPTION_KEY_PREVIOUS` | During rotation | `""` | Previous integration key retained temporarily so existing ciphertext can be read while it is rewritten. |
| `ADMIN_SECRET` | **Yes for coordinated production rollout** | `""` | Dedicated secret for `X-Admin-Secret`; blank disables secret-authenticated operator routes. The Railway rollout uses it to authenticate `/admin/health` after redacted `/ready` passes. A configured production value must be at least 32 characters and distinct from the other keys. |
| `TRUSTED_PROXY_CIDRS` | No | `""` | Comma-separated immediate proxy IPs/CIDRs allowed to supply `X-Forwarded-For`. Empty ignores forwarded client IPs. |
| `TRUST_RAILWAY_X_REAL_IP` | **Yes on production Railway services** | `false` | Trust Railway public networking's canonical `X-Real-IP` header for per-client logging and rate limits. Railway startup fails closed when this is false in production. Do not enable it on infrastructure where requests can bypass Railway's edge. |
| `FIREFLIES_LEGACY_RATE_LIMIT_PER_MINUTE` | No | `120` | Per-client abuse budget for only the unscoped compatibility webhook. Excess requests receive `429` plus `Retry-After`; organization-scoped webhook URLs stay outside this limiter. Set `0` only when equivalent edge protection exists and no legacy configuration remains. |
| `ALGORITHM` | No | `HS256` | JWT signing algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `30` | JWT token expiry in minutes. |
| `BCRYPT_ROUNDS` | No | `12` | Password-hash work factor. Production startup requires at least `12`; the test suite uses `4` while exercising the same bcrypt hash/verify path. |

#### Generating SECRET_KEY

```bash
# Option 1: OpenSSL (recommended)
openssl rand -hex 32

# Option 2: Python
python -c "import secrets; print(secrets.token_hex(32))"
```

Generate independent values for `SECRET_KEY`, `INTEGRATION_ENCRYPTION_KEY`, and
`ADMIN_SECRET`. Never reuse one value across these trust boundaries.

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
| `CLAUDE_MODEL` | No | `claude-haiku-4-5-20251001` | Valid pinned default for general/assessment and recruitment-agent calls. Use a snapshot id, not a retired `-latest` alias. |
| `CLAUDE_SCORING_BATCH_MODEL` | No | `claude-haiku-4-5-20251001` | Compatibility-named cost-optimised model for durable per-application background scoring. It does not select the removed scoring Message-Batches transport; falls back to `CLAUDE_MODEL` when unset. |
| `CLAUDE_CHAT_MODEL` | No | `claude-haiku-4-5-20251001` | Pinned candidate-facing agentic-chat model; independent of `CLAUDE_MODEL`. |
| `CLAUDE_AGENT_AUTONOMOUS_MODEL` | No | `""` | Optional autonomous cohort-loop override. Empty means the pinned `CLAUDE_MODEL` is used; a per-role `agent_model` remains the final override. |
| `CLAUDE_SEARCH_PARSER_MODEL` | No | `""` | Optional candidate-search parser override. Empty uses the pinned Sonnet default. |
| `CLAUDE_GROUNDING_MODEL` | No | `""` | Optional citation-grounding override. Empty uses the pinned Sonnet default. |
| `CLAUDE_SCORING_MODEL` | No | `""` | **Deprecated.** Old single-model selector. If set it must equal `CLAUDE_MODEL`, otherwise startup fails. Leave unset on new deployments. |
| `ANTHROPIC_ADMIN_API_KEY` | No | `""` | Admin credential for usage/cost reconciliation and optional read-only workspace lookup. It is never used to mint runtime API keys. |
| `ANTHROPIC_WORKSPACE_AUTH_ENABLED` | No | unset | Preferred per-org auth master gate. Unset preserves `ANTHROPIC_WORKSPACE_KEYS_ENABLED`; incomplete per-org auth falls back to the shared metered key and fails production activation readiness. |
| `ANTHROPIC_WORKSPACE_KEYS_ENABLED` | No | `false` | Legacy-compatible name for the per-org auth gate. Supports existing encrypted workspace keys and WIF; retained for deployed environments. |
| `ANTHROPIC_WORKSPACE_WIF_ENABLED` | No | `false` | Enables workspace-scoped Workload Identity Federation when an org has a persisted `wrkspc_` id and all fields below validate. |
| `ANTHROPIC_FEDERATION_RULE_ID` | With WIF | `""` | Anthropic federation rule id (`fdrl_...`). |
| `ANTHROPIC_ORGANIZATION_ID` | With WIF | `""` | Anthropic organization UUID used for token exchange. |
| `ANTHROPIC_SERVICE_ACCOUNT_ID` | With WIF | `""` | Target Anthropic service account id (`svac_...`). |
| `ANTHROPIC_IDENTITY_TOKEN_FILE` | With WIF | `""` | Absolute path to a readable rotating OIDC JWT file. The SDK re-reads it for every exchange; Tali never logs or persists its contents. |

Create [WIF](https://platform.claude.com/docs/en/manage-claude/workload-identity-federation)
issuers, service accounts, federation rules, and
[workspaces](https://platform.claude.com/docs/en/manage-claude/workspaces)
through the Anthropic Console/Admin workflow, then persist each organization’s
exact workspace id. The current [Admin API key
reference](https://platform.claude.com/docs/en/api/admin/api_keys) documents
get/list/update but no key-creation endpoint, so Tali does not perform
request-time key or workspace creation. Existing encrypted per-workspace keys
remain supported.

### Cost Observability Controls

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CLAUDE_INPUT_COST_PER_MILLION_USD` | No | `1.0` | Legacy fallback for historical token rows that have no model id. Explicit models use the verified rate registry; unknown non-empty models are rejected before provider work. |
| `CLAUDE_OUTPUT_COST_PER_MILLION_USD` | No | `5.0` | Output-token half of the model-less historical fallback above. |
| `USAGE_METER_LIVE` | **Yes in production** | `false` | When `false`, Claude calls still write `usage_events`, but the credit ledger is **not** debited and spend gates do **not** block. Production web and worker startup fail closed unless this is `true` or the emergency override below is explicitly enabled. Authenticated `/admin/health` exposes the active mode; public `/ready` exposes only redacted healthy/degraded readiness. |
| `USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY` | No | `false` | Emergency-only, time-bounded bypass that permits production to boot while `USAGE_METER_LIVE=false`. Credit gates remain disabled; authenticated `/admin/health` reports `shadow_emergency_override`, while `/ready` returns the redacted `degraded` status with HTTP 503. Return this to `false` when the incident is resolved. |
| `E2B_COST_PER_HOUR_USD` | No | `0.30` | Hourly E2B runtime cost estimate per active assessment sandbox. |
| `EMAIL_COST_PER_SEND_USD` | No | `0.01` | Per-email send cost estimate (invite/results notifications). |
| `STORAGE_COST_PER_GB_MONTH_USD` | No | `0.023` | Storage cost estimate for persisted assessment artifacts. |
| `STORAGE_RETENTION_DAYS_DEFAULT` | No | `30` | Retention window used in storage-cost estimates. |
| `COST_ALERT_DAILY_SPEND_USD` | No | `200.0` | Alert threshold for tenant daily spend estimate. |
| `COST_ALERT_PER_COMPLETED_ASSESSMENT_USD` | No | `10.0` | Alert threshold for cost per completed assessment. |

### Workable (ATS Integration)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WORKABLE_CLIENT_ID` | Only with Workable | `""` | OAuth2 client ID for the optional Workable integration. |
| `WORKABLE_CLIENT_SECRET` | Only with Workable | `""` | OAuth2 client secret for the optional Workable integration. |
| `WORKABLE_WEBHOOK_SECRET` | Not currently active | `""` | Reserved for signed inbound Workable webhooks. Do not register the endpoint until the durable inbound consumer is implemented; it currently rejects valid events with `501` instead of falsely acknowledging them. |

**Where to get it:** Apply for a Workable partner integration at [workable.com](https://www.workable.com) → Partner Portal. You'll receive client credentials after approval.

For a Taali-native requisition with no Workable job, set
`ATS_PUBLIC_APPLY_ENABLED=true`. Production agent activation refuses to open a
native job while its application endpoint is disabled.

### Autonomous Agent Runtime

The supported production worker command is:

```bash
python -m app.scripts.railway_worker_start
```

With its default settings it consumes both `celery` and `scoring` and owns the
single Beat scheduler. Queue-specific canaries are emitted every minute;
authenticated `/admin/health` exposes their detailed state, public `/ready`
returns only the resulting healthy/degraded status, and production Turn on
requires both canaries to be fresh. The role cohort sweep runs hourly, while
activation and resume enqueue an immediate complete pass.

One-time environment configuration replaces per-job operator steps. In
production set `DEPLOYMENT_ENV=production`, `USAGE_METER_LIVE=true`, a real
`ANTHROPIC_API_KEY`, and fund the organization credit ledger. Native
requisitions also need `ATS_PUBLIC_APPLY_ENABLED=true`. If assessments are used,
the worker needs `E2B_API_KEY`, `RESEND_API_KEY`, a real `GITHUB_TOKEN`, and
`GITHUB_MOCK_MODE=false`. Turn on fails closed and reports the missing item.

Per role, create/publish the requisition and click **Turn on**. That one action
persists the authorization and budget before any provider work starts. The
backend owns pending generation/repair, sandbox validation, repository approval,
readiness, activation, and the first complete pass; the page or browser may be
closed without interrupting it. Explicitly skipping the assessment remains
available as an override, not a prerequisite. There is no separate Tasks-page
setup. First activation materializes the role's visible Agent settings; an
untouched workspace defaults reversible assessment send/resend and interview
advance on while deterministic rejection and assessment skipping remain off.
It starts the complete funnel pass and opens a native requisition for
applications. Manual pauses require explicit Resume. Budget,
credit, failed-bootstrap, and runtime/provider holds are rechecked every five
minutes and resume automatically once both their cause and the full readiness
probe are healthy.

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
| `RESEND_API_KEY` | **Yes** | `""` | API key for transactional and verification emails. Production startup fails when it is missing or a placeholder because unverified accounts cannot log in. |

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
| `ASSESSMENT_EXPIRY_DAYS` | No | `7` | Number of days before an assessment invite link expires. |
| `EMAIL_FROM` | No | `TAALI <noreply@taali.ai>` | Sender address used by all transactional emails. |
| `ASSESSMENT_TERMINAL_ENABLED` | No | `true` | Hard gate on the terminal-native Claude Code runtime. Startup fails fast if set to `false`. |

### Pre-Screen Gate

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ENABLE_PRE_SCREEN_GATE` | No | `false` | When `true`, every v3 score is preceded by a cheap pre-screen LLM call (~$0.0002/CV); candidates below `PRE_SCREEN_THRESHOLD` skip full scoring entirely. |
| `PRE_SCREEN_THRESHOLD` | No | `30` | Numeric threshold (0-100) for the pre-screen gate. |
| `FRAUD_COPY_PASTE_THRESHOLD` | No | `0.05` | Review-flag threshold for deterministic CV-vs-JD overlap. Set to `1.0` to disable the signal. A hit does not change the score under the default action. |
| `FRAUD_COPY_PASTE_ACTION` | No | `flag` | `flag` records a neutral recruiter-review signal without changing score/verdict. `cap` explicitly restores the legacy hard-cap policy. |
| `FRAUD_PENALTY_CAP_SCORE` | No | `10.0` | Score cap used only when `FRAUD_COPY_PASTE_ACTION=cap`. |
| `PRESCREEN_ADVERSE_IMPACT_MONITOR_ENABLED` | No | `false` | Opt in to the daily aggregate monitor over actual gate, fraud-cap, and automated-reject outcomes. It reads segregated voluntary EEO self-ID, suppresses small cells, and never changes hiring state. |
| `PRESCREEN_ADVERSE_IMPACT_LOOKBACK_DAYS` | No | `30` | Closed UTC-day lookback window for the aggregate monitor. |
| `PRESCREEN_ADVERSE_IMPACT_MIN_CELL_N` | No | `5` | Minimum segment cell size emitted by the monitor; smaller cells are combined into an anonymous suppressed count. Must be at least 2. |

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
| `GRAPHITI_LLM_MODEL` | No | `claude-haiku-4-5-20251001` | Anthropic model used by Graphiti for entity extraction. Reuses `ANTHROPIC_API_KEY`; production rejects an override without a verified internal rate. |
| `GRAPHITI_LLM_SMALL_MODEL` | No | `claude-haiku-4-5-20251001` | Smaller-task variant of the above, with the same verified-rate requirement. |
| `GRAPHITI_EMBEDDING_MODEL` | No | `voyage-3` | Voyage embedding model. |
| `GRAPHITI_MAX_EPISODES_PER_CANDIDATE` | No | `40` | Hard cap (1–100, including the optional CV episode) on per-candidate Graphiti episodes — guards against runaway LLM cost on candidates with hundreds of experience entries. |

### Feature Flags

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MVP_DISABLE_STRIPE` | No | `false` | Stripe is the live payment processor for credit top-ups (since 2026-04-29). |
| `MVP_DISABLE_WORKABLE` | No | `true` | Disables Workable ATS sync; the integration is feature-flagged off by default. |
| `MVP_DISABLE_CLAUDE_SCORING` | No | `true` | Disables the v3 scoring pipeline; assessments fall back to deterministic scoring. |
| `MVP_DISABLE_PROCTORING` | No | `true` | Proctoring signals (browser focus, tab switches) are recorded but not gated on. |
| `TASK_AUTHORING_API_ENABLED` | No | `false` | Gates the task-authoring API (tasks are backend-authored by default). |

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
| `VITE_API_URL` | **Yes** | — | Backend API base URL (production: `https://resourceful-adaptation-production.up.railway.app`). |
| `VITE_PUBLIC_API_BASE_URL` | No | Derived from `VITE_API_URL` | Full base advertised by the public Developer Portal, including `/public/v1`. Leave unset unless the public API has a separate verified origin. |
| `VITE_STRIPE_PUBLISHABLE_KEY` | **Yes** | — | Stripe publishable key (starts with `pk_live_` or `pk_test_`). |

**Where to get it:**
- `VITE_API_URL`: your Railway backend URL
- `VITE_PUBLIC_API_BASE_URL`: optional full public API base such as `https://api.example.com/public/v1`
- `VITE_STRIPE_PUBLISHABLE_KEY`: [Stripe Dashboard](https://dashboard.stripe.com) → Developers → API keys → Publishable key

**Important (Vercel):** When setting `VITE_API_URL` in the Vercel dashboard, ensure there is **no trailing newline or space**. A literal `\n` at the end can break API requests. The frontend shared API client strips whitespace defensively, but fix the value at the source.
