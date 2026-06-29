---
name: new-migration
description: >
  Create an Alembic database migration safely in the Tali backend, keeping a single
  migration head (a CI gate). Use when a SQLAlchemy model change needs a schema
  migration, or when the alembic single-head gate is failing.
allowed-tools: Bash(alembic*) Bash(python scripts/check_alembic_single_head.py) Read Grep Glob Edit
---

# Creating an Alembic migration

Migrations live in `backend/alembic/versions/` and are **append-only**. CI runs
`python scripts/check_alembic_single_head.py` and fails the build if two migrations
branch from the same parent (multiple heads), because `alembic upgrade head` runs on
boot and will crash-loop the deploy.

## Steps (from `backend/`)

1. Make the model change in `app/models/`.
2. Generate the migration from the current DB state:
   ```
   alembic upgrade head            # ensure local DB is current first
   alembic revision --autogenerate -m "short description"
   ```
3. **Review the generated file** in `alembic/versions/`. Autogenerate misses some
   changes (server defaults, enums, index renames, data backfills) — edit the
   `upgrade()` / `downgrade()` functions by hand where needed.
4. Apply and verify it runs cleanly:
   ```
   alembic upgrade head
   alembic current
   ```
5. Confirm there is exactly one head:
   ```
   alembic heads
   python scripts/check_alembic_single_head.py
   ```

## If there are multiple heads

This happens when two branches each added a migration off the same parent. **Do not
delete a migration** — others may have applied it. Add a merge revision instead:

```
alembic merge -m "merge heads" <head1> <head2>
python scripts/check_alembic_single_head.py
```

## Conventions

- Keep the `-m` message short and descriptive; the version files are numbered
  sequentially (`NNN_description.py`).
- Never edit a migration that may already be applied elsewhere — add a new one.
- Pair schema changes with the model/schema code and a test in the same PR.
