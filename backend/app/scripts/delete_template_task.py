"""
Delete a template task by task_key (e.g. after removing its JSON from backend/tasks/).

Usage (from backend/ with DATABASE_URL set, or via Railway shell):
  python -m app.scripts.delete_template_task data_eng_c_backfill_schema

Alternatively call the admin API (no DB access needed):
  curl -X POST https://<api>/api/v1/tasks/admin/delete-template \\
    -H "Content-Type: application/json" -H "X-Admin-Secret: <SECRET_KEY>" \\
    -d '{"task_key": "data_eng_c_backfill_schema"}'
"""
from __future__ import annotations

import sys
from app.platform.database import SessionLocal
from app.models.task import Task


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.scripts.delete_template_task <task_key>", file=sys.stderr)
        sys.exit(1)
    task_key = sys.argv[1].strip()
    if not task_key:
        print("task_key is required", file=sys.stderr)
        sys.exit(1)
    db = SessionLocal()
    try:
        task = (
            db.query(Task)
            .filter(
                Task.task_key == task_key,
                Task.is_template == True,
                Task.organization_id == None,
            )
            .first()
        )
        if not task:
            print(f"No template task found with task_key={task_key!r}. Nothing to delete.")
            return
        db.delete(task)
        db.commit()
        print(f"Deleted template task id={task.id} task_key={task_key!r}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
