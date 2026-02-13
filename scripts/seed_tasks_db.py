"""
Seed tasks from tasks/*.json into the production database.
Removes ALL existing tasks first. Assessments with task_id references will have
task_id nullified so the FK constraint doesn't block deletion.

Usage:
  python scripts/seed_tasks_db.py
  DATABASE_URL="postgresql://..." python scripts/seed_tasks_db.py
"""
import json
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:zQGFEMbwDNMrHiMwDybTRsMQhpnIFYDx@yamabiko.proxy.rlwy.net:17842/railway",
)

TASKS_DIR = os.path.join(os.path.dirname(__file__), "..", "tasks")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

try:
    # ── 1. Remove all existing tasks ──────────────────────────────────────────
    # Nullify task_id on assessments so FK constraint doesn't block deletion
    db.execute(text("UPDATE assessments SET task_id = NULL WHERE task_id IS NOT NULL"))
    deleted = db.execute(text("DELETE FROM tasks")).rowcount
    db.commit()
    print(f"Deleted {deleted} existing tasks.")

    # ── 2. Load JSON files ────────────────────────────────────────────────────
    json_files = sorted(
        f for f in os.listdir(TASKS_DIR) if f.endswith(".json")
    )
    if not json_files:
        print("No JSON files found in", TASKS_DIR)
        sys.exit(1)

    created = 0
    for fn in json_files:
        path = os.path.join(TASKS_DIR, fn)
        with open(path) as f:
            t = json.load(f)

        task_id_str = t.get("task_id", fn.replace(".json", ""))
        name = t.get("name", task_id_str)
        role = t.get("role", None)
        duration_minutes = t.get("duration_minutes", 30)
        scenario = t.get("scenario", None)
        repo_structure = t.get("repo_structure", None)
        evaluation_rubric = t.get("evaluation_rubric", None)

        # Collect any remaining keys as extra_data
        known_keys = {"task_id", "name", "role", "duration_minutes", "scenario", "repo_structure", "evaluation_rubric"}
        extra_data = {k: v for k, v in t.items() if k not in known_keys}

        # Map role to legacy task_type field
        task_type = role or "general"
        difficulty = "medium"

        db.execute(
            text("""
                INSERT INTO tasks (
                    name, description, task_type, difficulty, duration_minutes,
                    is_template, is_active, organization_id,
                    task_key, role, scenario, repo_structure, evaluation_rubric, extra_data
                ) VALUES (
                    :name, :description, :task_type, :difficulty, :duration_minutes,
                    true, true, NULL,
                    :task_key, :role, :scenario,
                    cast(:repo_structure as jsonb),
                    cast(:evaluation_rubric as jsonb),
                    cast(:extra_data as jsonb)
                )
            """),
            {
                "name": name,
                "description": scenario[:500] if scenario else name,
                "task_type": task_type,
                "difficulty": difficulty,
                "duration_minutes": duration_minutes,
                "task_key": task_id_str,
                "role": role,
                "scenario": scenario,
                "repo_structure": json.dumps(repo_structure) if repo_structure else None,
                "evaluation_rubric": json.dumps(evaluation_rubric) if evaluation_rubric else None,
                "extra_data": json.dumps(extra_data) if extra_data else None,
            },
        )
        created += 1
        print(f"  Created: {task_id_str!r} ({role}, {duration_minutes}min)")

    db.commit()
    print(f"\nDone. {created} tasks seeded.")

except Exception as e:
    db.rollback()
    print(f"ERROR: {e}")
    raise
finally:
    db.close()
