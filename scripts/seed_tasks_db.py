"""
Seed tasks from tasks/*.json into the production database.
Uses task_spec_loader to validate specs (including rubric weights sum to 1.0).
Removes ALL existing tasks first. Assessments with task_id references will have
task_id nullified so the FK constraint doesn't block deletion.

Usage:
  python scripts/seed_tasks_db.py
  railway run python scripts/seed_tasks_db.py
  DATABASE_URL="postgresql://..." python scripts/seed_tasks_db.py
"""
import json
import os
import sys

# Add backend so we can import app.services
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.task_spec_loader import load_task_specs

# Prefer public URL when running locally (e.g. railway run) so we can reach DB
DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL or DATABASE_PUBLIC_URL is not set.")
    sys.exit(1)

TASKS_DIR = os.path.join(os.path.dirname(__file__), "..", "tasks")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

try:
    # ── 1. Remove all existing tasks ──────────────────────────────────────────
    db.execute(text("UPDATE assessments SET task_id = NULL WHERE task_id IS NOT NULL"))
    deleted = db.execute(text("DELETE FROM tasks")).rowcount
    db.commit()
    print(f"Deleted {deleted} existing tasks.")

    # ── 2. Load and validate task specs (rubric weights must sum to ~1.0) ──────
    specs = load_task_specs(TASKS_DIR)
    if not specs:
        print("No valid task specs found in", TASKS_DIR)
        sys.exit(1)

    created = 0
    for t in specs:
        task_id_str = t.get("task_id", "unknown")
        name = t.get("name", task_id_str)
        role = t.get("role", None)
        duration_minutes = t.get("duration_minutes", 30)
        claude_budget_limit_usd = t.get("claude_budget_limit_usd")
        scenario = t.get("scenario", None)
        repo_structure = t.get("repo_structure", None)
        evaluation_rubric = t.get("evaluation_rubric", None)
        known_keys = {"task_id", "name", "role", "duration_minutes", "claude_budget_limit_usd", "scenario", "repo_structure", "evaluation_rubric"}
        extra_data = {k: v for k, v in t.items() if k not in known_keys}

        # Map role to legacy task_type field
        task_type = role or "general"
        difficulty = "medium"

        db.execute(
            text("""
                INSERT INTO tasks (
                    name, description, task_type, difficulty, duration_minutes,
                    claude_budget_limit_usd, is_template, is_active, organization_id,
                    task_key, role, scenario, repo_structure, evaluation_rubric, extra_data
                ) VALUES (
                    :name, :description, :task_type, :difficulty, :duration_minutes,
                    :claude_budget_limit_usd, true, true, NULL,
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
                "claude_budget_limit_usd": claude_budget_limit_usd,
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
