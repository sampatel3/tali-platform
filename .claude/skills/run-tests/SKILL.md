---
name: run-tests
description: >
  Run the Tali test suites correctly — backend pytest (SQLite in-memory, Celery
  eager) and/or frontend Vitest. Use when asked to run tests, verify a change with
  tests, or reproduce a test failure in this repo.
allowed-tools: Bash(pytest*) Bash(python -m pytest*) Bash(npm test*) Bash(npm run test*) Read Grep Glob
---

# Running tests in Tali

CI does **not** run the test suites pre-pilot, so running them locally is the real
safety net. Pick the side(s) you changed.

## Backend (from `backend/`)

Tests use an in-memory SQLite DB and run Celery tasks eagerly — no Postgres/Redis
needed. The `production` marker is excluded by default (see `backend/pytest.ini`).

```
cd backend
pytest -q                          # full suite
pytest tests/test_api_roles.py     # one file
pytest -k "scoring and not slow"   # filter by name
pytest -m "not slow"               # skip slow tests
```

If imports fail with a missing-module error, the venv isn't set up:

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q
```

Notes:
- `aiosqlite` is required for the async DB layer in tests (it's in `requirements.txt`).
- If a stale `backend/test.db*` causes weird failures, delete it — the suite uses
  in-memory by default. See `backend/docs/TESTING.md` for known issues.

## Frontend (from `frontend/`)

```
cd frontend
npm test                 # Vitest, single run (jsdom + Testing Library)
npm run test:watch       # watch mode
npm run typecheck        # tsc --noEmit
```

If `node_modules` is missing, run `npm ci` first.

## When a change spans both

Run backend `pytest -q` and frontend `npm test`, then the CI gates via `/local-ci`
before pushing. Add or update tests alongside behavior changes.
