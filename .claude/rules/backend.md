---
paths:
  - "backend/**/*.py"
---

# Backend conventions (FastAPI / SQLAlchemy)

Applies when editing Python under `backend/`.

- **Module layout**: routes in `app/api/`, DDD modules in `app/domains/`, feature
  modules in `app/components/`, integrations (Claude, E2B, Stripe, Workable, email)
  in `app/services/`, ORM in `app/models/`, Pydantic in `app/schemas/`, Celery in
  `app/tasks/`, cross-cutting (config, db, security, middleware) in `app/platform/`.
- **Don't import from `app/core/`** — it holds deprecated re-export shims. Import
  the real symbol from `app/platform/` (or the owning module) instead.
- **File-size cap**: API route files and service modules must stay **≤ 500 LOC**
  (CI gate `scripts/check_file_sizes.py`). Split into new modules rather than
  growing a file or extending the allowlist.
- **Schema changes** require an Alembic migration with a single head — see the
  `/new-migration` skill. Migrations are append-only.
- **Tests**: add/update `backend/tests/` (pytest, SQLite in-memory, Celery eager).
  Run with `pytest -q` from `backend/`. Exclude the `production` marker locally
  (the default).
- **Determinism & fairness**: code under `app/decision_policy/`, scoring, and bias
  auditing must stay deterministic and EEOC-aware — read `NORTH_STAR.md` before
  changing it.
- **Claude/LLM touchpoints**: consult `docs/claude/README.md` (the registry of every
  Claude call, data flow, and test) before editing model-facing code. Use the latest
  Claude models for new AI features.
- **Secrets**: never read or commit `.env` files; local config lives in untracked
  `backend/.env` (`docs/ENV_SETUP.md`).
