"""Durable note dispatch and lock-free graph emission after confirmation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .decision_provider_checkpoint import lock_claim_application
from .decision_provider_claim import DecisionProviderClaim
from .decision_provider_operation import update_decision_post_operation


logger = logging.getLogger("taali.decision_provider_lifecycle")


def queue_decision_post_operation(
    db: Session,
    *,
    claim: DecisionProviderClaim,
    post: dict[str, Any] | None,
) -> None:
    if not isinstance(post, dict) or str(post.get("status") or "") == "queued":
        return
    from .workable_op_runner import OP_POST_NOTE, enqueue_workable_op

    try:
        job_run_id = enqueue_workable_op(
            organization_id=claim.snapshot.organization_id,
            op_type=OP_POST_NOTE,
            payload={
                "application_id": claim.snapshot.application_id,
                "user_id": post.get("actor_id"),
                "body": post.get("body"),
                "provider": post.get("provider"),
                "provider_target_id": post.get("provider_target_id"),
                "candidate_provider_id": post.get("candidate_provider_id"),
                "operation_id": post.get("operation_id"),
                "body_sha256": post.get("body_sha256"),
                "parent_decision_operation_id": claim.operation_id,
            },
            scope_id=claim.snapshot.application_id,
            dispatch_key=str(post["operation_id"]),
        )
    except Exception:
        app = lock_claim_application(db, claim)
        if app is not None:
            update_decision_post_operation(
                app,
                operation_id=claim.operation_id,
                status="queue_failed",
            )
            db.commit()
        else:
            db.rollback()
        logger.exception(
            "decision summary note durable enqueue failed operation_id=%s",
            claim.operation_id,
        )
        return
    app = lock_claim_application(db, claim)
    if app is not None:
        update_decision_post_operation(
            app,
            operation_id=claim.operation_id,
            status="queued",
            job_run_id=int(job_run_id),
        )
        db.commit()
    else:
        db.rollback()


def emit_decision_graph_episode(
    *, claim: DecisionProviderClaim, actor, note: str | None
) -> None:
    """Emit from copied primitives after the local commit released the DB."""

    try:
        from ..candidate_graph import agent_episodes

        action = (
            "override" if claim.snapshot.disposition == "overridden" else "approve"
        )
        reason = note
        if action == "override" and claim.snapshot.override_action:
            reason = " | ".join(
                item
                for item in (
                    f"override_action={claim.snapshot.override_action}",
                    note,
                )
                if item
            )
        agent_episodes.emit_recruiter_action_event(
            organization_id=claim.snapshot.organization_id,
            role_id=claim.snapshot.acting_role_id,
            decision_id=claim.snapshot.decision_id,
            recruiter_id=int(actor.user_id) if actor.user_id else 0,
            action=action,
            reason=reason,
            happened_at=datetime.now(timezone.utc),
        )
    except Exception:
        logger.warning(
            "recruiter graph episode failed decision_id=%s",
            claim.snapshot.decision_id,
            exc_info=True,
        )


__all__ = ["emit_decision_graph_episode", "queue_decision_post_operation"]
