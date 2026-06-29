---
name: local-ci
description: >
  Run the same gates that block merge in CI (.github/workflows/ci.yml) before
  pushing: backend syntax, alembic single-head, backend file-size, frontend
  architecture, and frontend build. Use before opening or updating a PR.
allowed-tools: Bash(python -m compileall*) Bash(python scripts/check_alembic_single_head.py) Bash(python scripts/check_file_sizes.py) Bash(npm run*) Bash(npm ci) Read Grep Glob
---

# Run the CI gates locally

CI is intentionally minimal pre-pilot: it runs build/syntax gates, **not** the test
suites. These are the exact checks from `.github/workflows/ci.yml` that block merge.
Run them before pushing. (Run the test suites separately via `/run-tests`.)

## Backend gates (from `backend/`)

```
python -m compileall -q app                      # syntax / import-name check
python scripts/check_alembic_single_head.py      # one migration head only
python scripts/check_file_sizes.py               # API routes & services <= 500 LOC
```

- **compileall fails** → fix the syntax error it prints.
- **single-head fails** → add a merge revision (see `/new-migration`), don't delete migrations.
- **file-size fails** → split the offending file into smaller modules; don't grow the allowlist.

## Frontend gates (from `frontend/`)

```
npm ci                       # if node_modules is missing/stale
npm run check:architecture   # per-file LOC cap (~2600)
npm run build                # Vite build catches import/type/module errors
```

## Quick all-in-one

```
( cd backend  && python -m compileall -q app && python scripts/check_alembic_single_head.py && python scripts/check_file_sizes.py ) \
&& ( cd frontend && npm run check:architecture && npm run build )
```

If every gate passes, the PR will clear CI. Still run `/run-tests` for anything
behavioral — the suite is the real safety net while CI skips it.
