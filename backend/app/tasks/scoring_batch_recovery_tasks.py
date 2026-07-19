"""Provider-free recovery tasks for durable scoring-batch dispatches."""

from __future__ import annotations

import logging

from .celery_app import celery_app


logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.scoring_batch_recovery_tasks.recover_scoring_batch_dispatches"
)
def recover_scoring_batch_dispatches(limit: int = 25) -> dict:
    """Re-publish lost roots and start drained durable successor intents."""

    from ..platform.database import SessionLocal
    from ..services.scoring_batch_fanout_recovery import (
        claim_due_scoring_fanouts,
        mark_scoring_fanout_publish_failed,
        mark_scoring_fanout_published,
    )
    from ..services.scoring_backfill_recovery import (
        reconcile_scoring_backfill_fanout,
    )
    from ..services.scoring_backfill_terminal_reconcile import (
        reconcile_scoring_backfill_parents,
    )
    from ..services.scoring_batch_successor_reconcile import (
        reconcile_queued_scoring_successors,
    )
    from ..services.scoring_batch_terminal_reconcile import (
        reconcile_drained_scoring_batches,
    )
    from .scoring_tasks import batch_score_role

    bounded_limit = max(1, min(int(limit), 100))
    with SessionLocal() as db:
        scanned, payloads = claim_due_scoring_fanouts(
            db,
            limit=bounded_limit,
        )
        db.commit()

    kicked = publish_failed = 0
    for payload in payloads:
        publish_scope = {
            "run_id": int(payload["run_id"]),
            "role_id": int(payload["role_id"]),
            "organization_id": int(payload["organization_id"]),
        }
        try:
            batch_score_role.delay(
                int(payload["role_id"]),
                include_scored=bool(payload["include_scored"]),
                applied_after=payload.get("applied_after"),
                run_id=int(payload["run_id"]),
            )
        except Exception as exc:
            publish_failed += 1
            mark_scoring_fanout_publish_failed(**publish_scope)
            logger.error(
                "Scoring batch recovery publish failed run_id=%s error_type=%s",
                payload["run_id"],
                type(exc).__name__,
            )
        else:
            kicked += 1
            mark_scoring_fanout_published(**publish_scope)

    terminals = reconcile_drained_scoring_batches(limit=bounded_limit)
    backfills = reconcile_scoring_backfill_fanout(limit=bounded_limit)
    backfill_terminals = reconcile_scoring_backfill_parents(limit=bounded_limit)
    successors = reconcile_queued_scoring_successors(limit=bounded_limit)
    return {
        "scanned": scanned,
        "claimed": len(payloads),
        "kicked": kicked,
        "publish_failed": publish_failed,
        "terminals": terminals,
        "backfills": backfills,
        "backfill_terminals": backfill_terminals,
        "successors": successors,
    }


__all__ = ["recover_scoring_batch_dispatches"]
