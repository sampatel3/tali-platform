"""Recovery publisher for durable recruiter-triggered Agent runs."""

from __future__ import annotations

import logging

from ..models.agent_run import AGENT_RUN_DISPATCHING, AgentRun
from ..models.role import Role
from ..platform.database import SessionLocal
from ..services.manual_agent_run_dispatch import (
    claim_publish,
    finish_manual_run_intent,
    manual_run_role_block_reason,
    publish_due_filter,
)
from ..services.role_agent_dispatch import dispatch_role_agent_cycle
from .celery_app import celery_app


logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.agent_tasks.recover_dispatching_manual_agent_runs"
)
def recover_dispatching_manual_agent_runs(limit: int = 100) -> dict:
    """Re-publish confirmed manual-run intents left before broker acceptance."""

    bounded_limit = max(1, int(limit))
    with SessionLocal() as db:
        rows = (
            db.query(AgentRun)
            .filter(
                AgentRun.status == AGENT_RUN_DISPATCHING,
                publish_due_filter(),
            )
            .order_by(AgentRun.id.asc())
            .limit(bounded_limit)
            .with_for_update(skip_locked=True)
            .all()
        )
        payloads = []
        for row in rows:
            payload = claim_publish(row)
            if payload is not None:
                payloads.append(payload)
            if len(payloads) >= bounded_limit:
                break
        # Reserve the next attempt before touching the broker. Another Beat
        # pod that acquires these rows after commit sees them as not due.
        db.commit()

    kicked = publish_failed = 0
    for payload in payloads:
        try:
            with SessionLocal() as role_db:
                role = role_db.get(Role, int(payload["role_id"]))
                block_reason = manual_run_role_block_reason(role_db, role=role)
                if block_reason is not None:
                    finish_manual_run_intent(
                        role_db,
                        dispatch_key=payload.get("dispatch_key"),
                        organization_id=int(payload["organization_id"]),
                        role_id=int(payload["role_id"]),
                        application_id=payload.get("application_id"),
                        status="aborted",
                        error=block_reason,
                    )
                    role_db.commit()
                    logger.warning(
                        "manual agent run recovery aborted role_id=%s reason=%s",
                        payload["role_id"],
                        block_reason,
                    )
                    continue
                dispatch_role_agent_cycle(
                    role,
                    manual=True,
                    application_id=payload.get("application_id"),
                    dispatch_key=payload.get("dispatch_key"),
                )
            kicked += 1
        except Exception:
            publish_failed += 1
            logger.exception(
                "manual agent run recovery publish failed dispatch_key=%s",
                payload["dispatch_key"],
            )
    return {
        "scanned": len(payloads),
        "kicked": kicked,
        "publish_failed": publish_failed,
    }


__all__ = ["recover_dispatching_manual_agent_runs"]
