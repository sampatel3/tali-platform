"""
Seed backend-authored template tasks from backend/tasks/*.json into the database.
Uses the shared canonical catalog resolver and task loader/validator.

Usage:
  python scripts/seed_tasks_db.py
  railway run python scripts/seed_tasks_db.py
  DATABASE_URL="postgresql://..." python scripts/seed_tasks_db.py
"""
import os
import sys

# Add backend so we can import app.services
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.task_catalog import canonical_task_catalog_dir, sync_template_task_specs
from app.services.task_spec_loader import load_task_specs

# Prefer public URL when running locally (e.g. railway run) so we can reach DB
DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL or DATABASE_PUBLIC_URL is not set.")
    sys.exit(1)

TASKS_DIR = canonical_task_catalog_dir()

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

try:
    # ── 1. Load and validate canonical task specs ──────────────────────────────
    specs = load_task_specs(TASKS_DIR)
    if not specs:
        print("No valid task specs found in", TASKS_DIR)
        sys.exit(1)

    # ── 2. Upsert canonical templates, deactivate stale templates ──────────────
    stats = sync_template_task_specs(db, specs)
    print("Canonical task sync complete:")
    print(f"  created={stats['created']}")
    print(f"  updated={stats['updated']}")
    print(f"  deactivated={stats['deactivated']}")
    print(f"  preserved_referenced={stats['preserved_referenced']}")

except Exception as e:
    db.rollback()
    print(f"ERROR: {e}")
    raise
finally:
    db.close()
