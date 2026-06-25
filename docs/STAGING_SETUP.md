# Staging environment setup (ATS build → gate testing)

Stand up a **live, non-prod** environment to test the ATS work at each gate. It is
a second **environment inside the existing `tali-platform` Railway project** plus
the existing Vercel `frontend` project's **Preview** env — isolated DB, **email-
incapable by construction**, and it deploys the long-lived **`ats`** branch only.
Production is never touched.

> Status legend: 🧑 = needs the Railway/Vercel dashboard (you); 🤖 = CLI (Claude can run).

## 0. Prereqs (already done)
- `ats` branch exists and is pushed to origin.
- `staging` environment created in the `tali-platform` Railway project.

## 1. 🧑 Create the staging services (Railway dashboard, project `tali-platform`, env `staging`)
The CLI in this version drops into an interactive menu for these, so they're dashboard steps:
1. **New → Database → PostgreSQL**
2. **New → Database → Redis**
3. **New → GitHub Repo → `sampatel3/tali-platform`** → in that service's **Settings**:
   - **Root Directory** = `backend`
   - **Source → Branch** = `ats`
   - (Build is auto: Nixpacks + `backend/railway.json`.)

Note the API service's name (e.g. `tali-staging-api`) — call it `$SVC` below.
(A worker service is optional for the P0 gate — skip unless testing async flows;
if added, set `TALI_SERVICE_MODE=worker` on it.)

## 2. 🤖 Set staging env vars (email-incapable, feature-on)
Run from repo root. `--skip-deploys` so nothing boots mid-config; always `-e staging`.
```bash
SVC=tali-staging-api   # <-- the API service name from step 1
SAFE=(
  "SECRET_KEY=$(openssl rand -hex 32)"
  "ASSESSMENT_TERMINAL_ENABLED=true"
  "ASSESSMENT_TERMINAL_DEFAULT_MODE=claude_cli_terminal"
  "MVP_DISABLE_WORKABLE=true"      # no Workable calls
  "MVP_DISABLE_STRIPE=true"        # no billing
  "MVP_DISABLE_CELERY=true"        # no worker dependency (P0 gate)
  "GITHUB_MOCK_MODE=true"          # no real assessment repos
  "ATS_CONFIGURABLE_STAGES_ENABLED=true"  # the feature under test (staging-only)
)
for kv in "${SAFE[@]}"; do railway variables set -e staging -s "$SVC" --skip-deploys "$kv"; done
# Deliberately NOT set: RESEND_API_KEY (staging cannot email real candidates).
# Optional, only when testing scoring/assessments — copy from prod without printing:
# for K in ANTHROPIC_API_KEY E2B_API_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_REGION AWS_S3_BUCKET; do
#   V=$(railway variables -s resourceful-adaptation -e production --kv | grep "^$K=" | cut -d= -f2-)
#   [ -n "$V" ] && railway variables set -e staging -s "$SVC" --skip-deploys "$K=$V"
# done
```
`DATABASE_URL` / `REDIS_URL` are auto-injected by Railway once the DBs are attached to `$SVC`.
After Vercel (step 5) set `FRONTEND_URL` + `BACKEND_URL` to the real staging URLs.

## 3. 🤖 Initialize the staging DB (one-time)
The alembic chain **cannot build from empty** (base tables come from `create_all`,
not migrations). So seed the schema with `create_all` + stamp `alembic_version` at
head, then deploys' `alembic upgrade head` is a no-op until the next migration.
```bash
PGURL=$(railway variables -s Postgres -e staging --kv | grep '^DATABASE_PUBLIC_URL=' | cut -d= -f2-)
cd backend
DATABASE_URL="$PGURL" SECRET_KEY=x /Users/sampatel/Code/tali-platform/backend/.venv/bin/python \
  -c "import app.models; from app.platform.database import Base, engine; Base.metadata.create_all(bind=engine); print('tables', len(Base.metadata.tables))"
HEAD=$(DATABASE_URL="$PGURL" SECRET_KEY=x /Users/sampatel/Code/tali-platform/backend/.venv/bin/python -m alembic heads | awk '{print $1}')
/opt/homebrew/opt/libpq/bin/psql "$PGURL" -c \
  "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(255) NOT NULL PRIMARY KEY); INSERT INTO alembic_version VALUES ('$HEAD') ON CONFLICT DO NOTHING;"
```

## 4. 🤖 Deploy `ats` + verify
The service auto-deploys from `ats` on push. To trigger/verify:
```bash
railway up -e staging -s "$SVC" --ci   # or Dashboard → service → Deploy
curl -s "https://<staging-api-domain>/health"   # expect {"status":"healthy","service":"taali-api"}
```
(Find the domain in the service's **Settings → Networking → Public Domain**.)

## 5. 🧑/🤖 Wire the frontend (Vercel `frontend` Preview env)
This is what finally makes full-stack previews work (Preview previously had no API URL).
```bash
# 🤖 point Preview builds at the staging API (scope to the ats branch)
vercel env add VITE_API_URL preview ats   # paste: https://<staging-api-domain>
```
Then push to `ats` (or redeploy) → the Preview deployment serves the ATS build against staging.
Set Railway `FRONTEND_URL` to that Preview URL (CORS/links): `railway variables set -e staging -s "$SVC" "FRONTEND_URL=https://<preview-url>"`.

## 6. 🤖 Seed a demo ATS org + smoke the gate
- Register a staging recruiter (via the app or `register_user`), then exercise the P0 gate:
```bash
# list (auto-seeds canonical stages), add a custom stage, move a candidate, observe
curl -s -H "Authorization: Bearer $TOKEN" https://<api>/api/v1/pipeline/stages
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Phone Screen","kind":"screening"}' https://<api>/api/v1/pipeline/stages
```

## Safety invariants (do not violate)
- **No `RESEND_API_KEY` on staging** until an explicit comms-testing gate — staging must not email real candidates (bounce-incident history).
- **Always `-e staging`** on every `railway variables`/`up` — the CLI defaults to the linked env; never mutate `production`.
- `ATS_CONFIGURABLE_STAGES_ENABLED` is **staging-only**; prod stays default-off.
- The `ats` branch **never merges to `main`** until the whole program is complete and signed off.
