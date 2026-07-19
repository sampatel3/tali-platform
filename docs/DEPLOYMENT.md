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
./scripts/deploy_production.sh
./scripts/railway/fetch_logs.sh resourceful-adaptation
```

Workers-only recovery (always deploys and validates both workers):

```bash
RAILWAY_WORKER_SERVICE=<general-worker-service> \
RAILWAY_SCORING_WORKER_SERVICE=<scoring-worker-service> \
  ./scripts/railway/deploy_worker.sh
```

Why this matters:
- Every production-capable wrapper independently requires a clean worktree whose
  release SHA is in `origin/main`; calling a lower-level Railway wrapper directly
  does not bypass the guard.
- A coordinated rollout uses a process-scoped attestation to stay pinned to its
  exact kickoff SHA if `main` advances while Railway and Vercel are deploying;
  setting a SHA environment variable alone cannot enter coordinated mode.
- Before any production variable mutation, migration, or service deployment, the
  wrapper queries `alembic_version` without printing the database URL and refuses
  a database revision absent from or unreachable in the exact release tree.
  A genuinely empty new database is accepted as Alembic base; a database with
  user tables but no `alembic_version` table fails closed.
- CLI deploys run `railway up` from the repository root without a path argument.
  Railway then uploads the monorepo and applies the configured `/backend` service
  root and `/backend/railway.json`, exactly as GitHub-triggered deployments do.
- Web, general-worker, and scoring-worker services are validated by exact name.
- The coordinated wrapper pins live metering and native apply, migrates
  production, deploys both workers, deploys web, waits for public `/ready`, and
  validates the default assessment-provider path.
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
INTEGRATION_ENCRYPTION_KEY=<generate independently: openssl rand -hex 32>
# Required by the coordinated rollout's authenticated capability probe.
ADMIN_SECRET=<generate independently: openssl rand -hex 32>
TRUST_RAILWAY_X_REAL_IP=true
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

E2B_API_KEY=<from e2b.dev dashboard>
ANTHROPIC_API_KEY=<from console.anthropic.com>
CLAUDE_MODEL=claude-haiku-4-5-20251001
CLAUDE_SCORING_BATCH_MODEL=claude-haiku-4-5-20251001

DEPLOYMENT_ENV=production
USAGE_METER_LIVE=true
ATS_PUBLIC_APPLY_ENABLED=true

# Only when Workable ATS sync/OAuth is enabled:
WORKABLE_CLIENT_ID=<from Workable partner portal>
WORKABLE_CLIENT_SECRET=<from Workable partner portal>

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
`SECRET_KEY`, `INTEGRATION_ENCRYPTION_KEY`, `ADMIN_SECRET`, `DEPLOYMENT_ENV`, `AUTO_GENERATE_ASSESSMENT_TASKS=true`,
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

Both Railway config files select `backend/nixpacks.toml` explicitly. Its install
phase replaces Nixpacks' generated `pip install -r requirements.txt` command;
it first creates and activates Nixpacks' canonical `/opt/venv`, verifies the
runtime lock's source digest, installs the complete production graph with
`python -m pip install --require-hashes --no-deps -r
requirements-runtime-lock.txt`, and finishes with `pip check`. Creating that
virtual environment is part of the locked command: Nixpacks places
`/opt/venv/bin` on the runtime path but does not create it after its generated
install command is replaced. The production preparation step also pins the
same byte-for-byte command as `NIXPACKS_INSTALL_CMD` on web and both workers,
because Nixpacks environment configuration has higher priority than its file
plan. Each service wrapper uses a case-sensitive exact readback before
`railway up`, so an old dashboard override cannot restore the mutable provider
install.

`backend/requirements-runtime-lock.txt` is compiled from `requirements.txt`
for the exact Python patch release in `backend/runtime.txt` on x86-64 Linux;
both files are bound into its freshness digest. The existing
`backend/requirements-lock.txt` remains the dev-inclusive CI/test lock. After a
runtime dependency or Python runtime change, regenerate both from `backend/`;
the helper invokes `uv` without a shell and embeds the exact source digest in
each generated header:

```bash
python scripts/check_requirements_lock.py --compile
```

### 5. Run the coordinated production rollout

Keep GitHub automatic deployments disabled for the production branch on the
Railway web, general-worker, and scoring-worker services. Leave each service's
repository source connected so the wrapper can still deploy the attested
checkout. The root `vercel.json` used by Git deployments and
`frontend/vercel.json` used by the linked CLI release likewise disable automatic
deployments only for `main` while preserving branch previews. This prevents a
merge webhook from starting web or worker processes against the old schema
before the migration and ordered rollout complete. The orchestrator below is
the sole production release path; re-enable provider autodeploy only if this
coordination contract is deliberately replaced.

Use the single orchestrator from repo root. Substitute names only if the
Railway services were renamed:

```bash
RAILWAY_ENVIRONMENT=production \
RAILWAY_BACKEND_SERVICE=resourceful-adaptation \
RAILWAY_WORKER_SERVICE=taali-worker \
RAILWAY_SCORING_WORKER_SERVICE=taali-worker-scoring \
RAILWAY_BACKEND_URL=https://resourceful-adaptation-production.up.railway.app \
  ./scripts/deploy_production.sh
```

The order is enforced:

1. Fetch `origin/main`, require the exact clean release SHA, query production's
   current `alembic_version` rows, and verify every row exists and is reachable
   in the release migration graph. The read-only provider preflight also binds
   the checkout to the expected Railway project, environment, three services,
   authenticated user, and exact Vercel project before any provider mutation.
   A private process attestation pins every child step to the kickoff SHA even
   if `main` advances while the coordinated release is running.
2. Resolve the web service's validated pre-screen policy, then pin the full
   production agent/ATS contract (`USAGE_METER_LIVE`, native apply, Bullhorn,
   Workable, trusted Railway proxy IPs, and both scoring-gate variables) with
   `--skip-deploys` on web and both workers. Read every value back before any
   service deploy.
3. Recheck both the release source and migration provenance immediately before
   running `python -m app.scripts.database_migrate` against the production
   `DATABASE_PUBLIC_URL`. The command serializes deploys with a PostgreSQL
   advisory lock, rejects unversioned partial schemas, applies the complete
   Alembic chain only from the verified release tree, and verifies the release
   head, model columns, required invariant triggers, and search indexes. Lock
   acquisition fails after 300 seconds by default; set
   `DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS` to tune that bounded wait.
   Revision 189's roles fence has an additional five-second **acquisition** cap:
   a lower operator timeout is preserved, and a higher timeout is restored as
   soon as the fence is acquired. Ordinary reads continue, while role writers
   and row-locking action authorizers queue until the repair, structural/data
   validation, and shared-family trigger commit together. A queued old-version
   action therefore observes the repaired flags and durable invariant.
4. Pin and validate `taali-worker` as `queues=celery`, `Beat=true`; deploy it and
   wait for a new `SUCCESS` deployment ID.
5. Pin and validate `taali-worker-scoring` as `queues=scoring`, `Beat=false`;
   deploy it and wait for its own new `SUCCESS` deployment ID.
6. Deploy web with bare `railway up` from the repository root so Railway applies
   the configured `/backend` service root, wait for its new deployment to
   succeed, poll public `/ready`, then use `ADMIN_SECRET` against `/admin/health`
   and require the default worker's live Anthropic, read-only E2B access,
   Resend delivery, and GitHub capability checks to pass. The E2B check is an
   authenticated one-item sandbox-list GET cached for five minutes: it creates no
   sandbox and incurs no sandbox runtime cost. The secret is read from Railway
   without printing the variable payload and is passed to curl through a
   mode-0600 temporary header file rather than a process-list argument.
7. Revalidate the unchanged attested source, deploy the linked Vercel production
   project from `frontend/`, and revalidate the same SHA once more.

Any non-canonical source, out-of-tree database revision, missing service,
duplicate service name, wrong topology variable, failed deployment, migration
failure, or readiness timeout makes the wrapper exit non-zero. A single healthy
worker cannot produce a successful rollout.

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

`/health` is a cheap public liveness response. Production `/ready` returns only
a redacted verdict and succeeds when live usage metering, both `celery` and
`scoring` workers, and their live model access are healthy. Detailed dependency,
queue, and provider diagnostics are available only from authenticated
`/admin/health`; the coordinated deployment reads `ADMIN_SECRET` from Railway
and performs that stricter probe without exposing the secret. Its default-agent
gate also requires cached read-only E2B credential verification, real verified
GitHub access, and the worker's cached Resend test send. An assessment-free
role can still use the narrower per-role readiness contract.
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
- **Install Command**: `npm ci`

### 3. Configure environment variables

In the Vercel dashboard → your project → **Settings** → **Environment Variables**:

```
VITE_API_URL=https://your-backend.up.railway.app
# Optional full public developer API base; include /public/v1.
VITE_PUBLIC_API_BASE_URL=https://your-backend.up.railway.app/public/v1
VITE_STRIPE_PUBLISHABLE_KEY=pk_live_...
```

### 4. Redeploy with variables

```bash
cd ..
./scripts/deploy_production.sh
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
   - `checkout.session.completed`
5. Copy the **Signing secret** → set as `STRIPE_WEBHOOK_SECRET` in Railway

`checkout.session.completed` is the event that idempotently grants the current
one-time top-up credits. Do not substitute `payment_intent.succeeded`: the
handler does not grant credits from that event. Legacy subscription event
handlers remain for older records but are not required by the pay-per-use
deployment.

### Workable Webhooks

Do not register an inbound Workable webhook yet. The endpoint verifies a
configured signature but deliberately returns `501` because no durable inbound
event consumer is implemented; acknowledging events would make Workable discard
stage changes that Taali had not processed. Workable OAuth and scheduled/manual
sync remain available. Add webhook setup here only after the durable consumer
ships with replay and idempotency coverage.

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

Use the production test account (`sampatel@deeplight.ae`) without putting its
password in shell history or a long-lived exported variable. Run either or both
smokes from this dedicated Bash subshell:

```bash
(
  set -eu
  set +x
  taali_test_password=''
  trap 'unset taali_test_password' EXIT
  read -r -s -p 'Production test-account password: ' taali_test_password
  printf '\n'

  export TAALI_TEST_EMAIL=sampatel@deeplight.ae
  export TAALI_API_BASE_URL='https://resourceful-adaptation-production.up.railway.app/api/v1'

  # Workable metadata sync smoke
  TAALI_TEST_PASSWORD="$taali_test_password" \
    ./scripts/qa/prod_account_workable_smoke.sh

  # Model policy smoke (Haiku check)
  TAALI_TEST_PASSWORD="$taali_test_password" \
  EXPECTED_CLAUDE_MODEL=claude-haiku-4-5-20251001 \
    ./scripts/qa/prod_model_smoke.sh
)
```

For unattended automation, source the same short-lived shell variable from the
approved secret store without printing it, pass it only on the individual
command as above, and unset it immediately. Each smoke script copies the value
to a mode-0600 form file and unsets its inherited password before starting curl.

---

## Custom Domain Configuration

### Backend (Railway)

1. Railway dashboard → your service → **Settings** → **Domains**
2. Click **Add Custom Domain**
3. Enter your verified API domain (e.g., `api.example.com`)
4. Add the provided CNAME record to your DNS provider
5. Wait for DNS propagation and SSL provisioning
6. After the health check succeeds on that domain, update `BACKEND_URL` and `VITE_API_URL` to its HTTPS URL

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
- `VITE_PUBLIC_API_BASE_URL` in Vercel, if explicitly configured
- Stripe webhook endpoint URL
- Workable OAuth redirect URI
