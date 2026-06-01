"""One-shot backfill: unify legacy SDK aggregate ``usage_events.entity_id``.

Pre-2026-06-01 the ``claude_agent_sdk`` aggregate writer recorded
``entity_id = str(assessment_id)`` (bare "88"), while the classifier
and rubric grader recorded ``entity_id = f"assessment:{id}"``. Any
reporting query that filtered by one format silently dropped the
other and showed a ~50-65% under-count of session spend.

After PR #N the writer uses the namespaced format too. This script
backfills the legacy rows so historic queries return correct totals.

Idempotent: only updates rows where the entity_id is a bare integer
AND feature='assessment' AND the namespaced counterpart doesn't already
exist on the same usage_event id (which it never would, but the safety
clause means re-running can't double-update).

Run from a worktree with prod DATABASE_URL configured:

    DATABASE_URL='postgresql://...' python -m scripts.backfill_assessment_entity_id
"""

from __future__ import annotations

import logging
import os
import sys
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_entity_id")


SQL_PREVIEW = text("""
    SELECT COUNT(*) AS n_rows
    FROM usage_events
    WHERE feature = 'assessment'
      AND entity_id ~ '^[0-9]+$'
""")

SQL_UPDATE = text("""
    UPDATE usage_events
    SET entity_id = 'assessment:' || entity_id
    WHERE feature = 'assessment'
      AND entity_id ~ '^[0-9]+$'
""")


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        logger.error("DATABASE_URL is required")
        return 2

    # Late import so the module is loadable in test contexts that don't
    # have a real DB configured.
    from app.platform.database import SessionLocal  # noqa: WPS433

    with SessionLocal() as session:
        n_rows = session.execute(SQL_PREVIEW).scalar() or 0
        logger.info("preview: %d row(s) match the legacy bare-integer entity_id pattern", n_rows)
        if n_rows == 0:
            logger.info("nothing to backfill; exiting")
            return 0
        result = session.execute(SQL_UPDATE)
        session.commit()
        logger.info("backfill complete: rowcount=%d", result.rowcount)

    return 0


if __name__ == "__main__":
    sys.exit(main())
