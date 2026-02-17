# Backend Testing Guide

## Dependency parity

Backend tests rely on SQLite for local/CI execution and require `aiosqlite` to match runtime behavior.

Pinned dependency:

- `aiosqlite==0.20.0` in `/backend/requirements.txt`

CI validation:

- `.github/workflows/ci.yml` imports `aiosqlite` after dependency install to fail fast if missing.

## Local test commands

Run full backend suite:

```bash
cd backend
../.venv/bin/pytest -q
```

Run targeted suites:

```bash
cd backend
../.venv/bin/pytest -q tests/test_api_roles.py tests/test_api_assessment_pause.py
```

## Notes

1. Tests use SQLite (`sqlite:///./test.db`) from `backend/tests/conftest.py`.
2. Production smoke checks are separately marked and not part of the default local run.
3. If you see "attempt to write a readonly database" or "disk I/O error", remove stale DB files first: `rm -f backend/test.db backend/test_mig.db` then re-run tests.
