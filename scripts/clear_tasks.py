"""
Remove ALL tasks from the database. Nullifies assessment.task_id first so FK
does not block deletion. For use with Railway or any env with DATABASE_URL.

Usage:
  railway run python scripts/clear_tasks.py
  DATABASE_URL="postgresql://..." python scripts/clear_tasks.py
"""
import os
import sys

from sqlalchemy import create_engine, text

# Prefer public URL when running locally (e.g. railway run) so we can reach DB
DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL is not set. Set it or run via: railway run python scripts/clear_tasks.py")
    sys.exit(1)

engine = create_engine(DATABASE_URL)
with engine.begin() as conn:
    conn.execute(text("UPDATE assessments SET task_id = NULL WHERE task_id IS NOT NULL"))
    result = conn.execute(text("DELETE FROM tasks"))
    deleted = result.rowcount
print(f"Cleared {deleted} task(s). Assessments now have task_id = NULL where applicable.")
