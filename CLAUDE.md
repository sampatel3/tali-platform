# CLAUDE.md

Guidance for Claude Code (and other AI coding agents) working in this repository.
Keep this file concise — it loads into every session. Detailed, situational
guidance lives in `.claude/skills/` and `.claude/rules/`.

## What this is

Tali (a.k.a. Taali / TAA) is an AI-native hiring platform: candidates complete
real, sandboxed work assessments and recruiters get deterministic, role-fit-grounded,
EEOC-aware scoring with a recruiter-in-the-loop. It is a modular monolith:

- **`backend/`** — Python 3.10 + FastAPI, SQLAlchemy 2 + Alembic (Postgres),
  Celery + Redis, Anthropic/Claude + E2B sandboxes. ~100k LoC.
- **`frontend/`** — Vite 5 + React 18 + Tailwind, Vitest. Deployed to Vercel.
- **`docs/`**, **`backend/docs/`** — architecture, deployment, env, ADRs.

Read `NORTH_STAR.md` for product principles (determinism, role-fit, recruiter-in-loop,
fair hiring) and `ARCHITECTURE.md` for the platform design before large changes.

## Commands

Backend (run from `backend/`):

| Task | Command |
| --- | --- |
| Run tests | `pytest -q` (SQLite in-memory; excludes `production` marker by default) |
| Run one test | `pytest tests/test_api_roles.py` |
| Dev server | `python -m uvicorn app.main:app --reload --port 8000` |
| Apply migrations | `alembic upgrade head` |
| New migration | `alembic revision --autogenerate -m "description"` |
| Celery worker | `celery -A app.tasks worker --loglevel=info` |

Frontend (run from `frontend/`):

| Task | Command |
| --- | --- |
| Install | `npm ci` |
| Dev server | `npm run dev` (port 5173; proxies `/api` → `:8000`) |
| Run tests | `npm test` (Vitest) |
| Typecheck | `npm run typecheck` |
| Build | `npm run build` |

Local infra (from repo root): `docker-compose up -d` starts Postgres 15 + Redis 7.
See `docs/ENV_SETUP.md` for environment variables and `backend/docs/TESTING.md`
for test setup notes.

## CI gates — these run on every PR (`.github/workflows/ci.yml`)

CI is intentionally minimal pre-pilot (build/syntax gates, **no test suite**), so
**run tests locally** before significant changes. The gates that *do* block merge:

- **Backend syntax** — `python -m compileall -q app` (from `backend/`)
- **Alembic single-head** — `python scripts/check_alembic_single_head.py`. Two
  migrations branching from one parent breaks boot. Fix = add a merge revision,
  never delete migrations.
- **Backend file-size** — `python scripts/check_file_sizes.py`. API routes and
  service modules must stay **≤ 500 LOC** unless allowlisted. Fix = split the file,
  don't grow the allowlist.
- **Frontend architecture** — `npm run check:architecture` (≤ ~2600 LOC/file)
- **Frontend build** — `npm run build` (catches import/type errors)

Run all of these locally before pushing with `/local-ci` (see `.claude/skills/`).

## Repo conventions

- **Backend layout**: `app/api/` (routes) · `app/domains/` (DDD modules) ·
  `app/components/` (feature modules) · `app/services/` (integrations:
  Claude, E2B, Stripe, Workable, email) · `app/models/` + `app/schemas/` ·
  `app/tasks/` (Celery) · `app/platform/` (config, db, security, middleware).
  `app/core/` holds deprecated re-export shims — import from `app/platform/` instead.
- **Keep files small.** The 500-LOC cap on routes/services is enforced in CI.
  Prefer new modules over growing existing ones.
- **Migrations are append-only.** Generate with Alembic, keep a single head, never
  edit a migration that may have been applied elsewhere. See `/new-migration`.
- **Tests** live in `backend/tests/` (pytest, SQLite, Celery eager) and
  `frontend/src/**/*.test.jsx` (Vitest + Testing Library). Add/update tests with
  behavior changes even though CI doesn't run them yet.
- **Secrets**: never read or commit `.env` files. Local config goes in untracked
  `backend/.env`; see `docs/ENV_SETUP.md`.
- **Claude/LLM work**: `docs/claude/README.md` is the registry of every Claude
  touchpoint, data flow, and the relevant tests — consult it before touching
  model-facing code. Use the latest Claude models for new AI features.

## Working agreement

- Make the smallest change that solves the problem; match the style of surrounding code.
- Before pushing: run the relevant tests and the CI gates above.
- Don't introduce new top-level dependencies without a clear need.
- When a change touches scoring, decision policy, or bias auditing, treat
  determinism and fairness as hard requirements — read `NORTH_STAR.md` first.
