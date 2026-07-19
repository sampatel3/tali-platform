# Backend Testing Guide

## Dependency parity

Backend tests rely on SQLite for the default local/CI execution and require
`aiosqlite` to match runtime behavior.

Pinned dependency:

- `aiosqlite==0.20.0` in `/backend/requirements-dev.txt`

CI installs the complete `requirements-dev.txt`, runs the default SQLite suite
with coverage, then runs PostgreSQL-only invariants and the full Alembic chain
against PostgreSQL 16 in a separate job.

## Local test commands

Run full backend suite:

```bash
cd backend
python -m pytest -q

# Same coverage contract as CI
python -m pytest -q --maxfail=1 \
  --cov=app --cov-config=.coveragerc \
  --cov-report=term-missing:skip-covered --cov-report=xml
```

Run targeted suites:

```bash
cd backend
python -m pytest -q \
  tests/test_api_roles.py \
  tests/test_api_assessment_pause.py
```

## Notes

1. The shared fixture uses a named, shared in-memory SQLite database by default;
   no `test.db` cleanup workaround is required.
2. Set `TALI_TEST_DATABASE_URL` before pytest only when a deliberately separate
   test database is needed. Never point it at application or production data.
3. Production smoke checks are separately marked and not part of the default
   local run. Run them only with an explicit HTTPS `TALI_PROD_URL`.
4. The default suite is intentionally sequential because several integration
   tests exercise process-global runtime state. Do not add `pytest-xdist` to CI
   without first isolating those contracts.
