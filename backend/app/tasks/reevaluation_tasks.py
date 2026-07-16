"""Durable dispatch wrapper for recruiter-requested agent re-evaluations."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .celery_app import celery_app

logger = logging.getLogger("taali.tasks.reevaluation")
_LEASE = timedelta(minutes=8)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _defer(db, decision, reason: str) -> dict:
    attempts = int(decision.reevaluation_attempts or 1)
    decision.reevaluation_status = "pending"
    decision.reevaluation_lease_until = None
    decision.reevaluation_next_attempt_at = _now() + timedelta(
        minutes=2 ** min(max(attempts, 1), 6)
    )
    decision.reevaluation_error = reason[:500]
    db.commit()
    return {"status": "pending", "reason": reason, "decision_id": int(decision.id)}


@celery_app.task(name="app.tasks.reevaluation_tasks.run_agent_re_evaluation")
def run_agent_re_evaluation(decision_id: int) -> dict:
    """Lease one receipt and run the focused agent cycle once.

    Duplicate publishes skip an active/completed lease. The agent runtime's
    per-role in-flight guard handles overlap with an unrelated cycle; that
    outcome is deferred rather than falsely acknowledged as completion.
    """
    from ..models.agent_decision import AgentDecision
    from ..models.role import Role
    from ..platform.database import SessionLocal

    with SessionLocal() as db:
        decision = (
            db.query(AgentDecision)
            .filter(AgentDecision.id == int(decision_id))
            .with_for_update()
            .one_or_none()
        )
        if decision is None:
            return {"status": "missing", "decision_id": decision_id}
        next_attempt = _as_utc(decision.reevaluation_next_attempt_at)
        if (
            decision.reevaluation_status == "pending"
            and next_attempt is not None
            and next_attempt > _now()
        ):
            # A delayed duplicate broker delivery must not bypass the durable
            # exponential backoff chosen by a previous failed attempt.
            return {"status": "skipped", "reason": "not_due"}
        lease = _as_utc(decision.reevaluation_lease_until)
        if (
            decision.reevaluation_status == "running"
            and lease is not None
            and lease > _now()
        ):
            return {"status": "skipped", "reason": "already_running"}
        if decision.reevaluation_status not in ("pending", "running"):
            return {"status": "skipped", "reason": "already_closed"}
        decision.reevaluation_status = "running"
        decision.reevaluation_attempts = int(decision.reevaluation_attempts or 0) + 1
        decision.reevaluation_lease_until = _now() + _LEASE
        decision.reevaluation_next_attempt_at = None
        decision.reevaluation_error = None
        role_id = int(decision.role_id)
        application_id = int(decision.application_id)
        db.commit()

        role = db.get(Role, role_id)
        if role is None:
            decision = db.get(AgentDecision, int(decision_id))
            decision.reevaluation_status = "failed"
            decision.reevaluation_lease_until = None
            decision.reevaluation_error = "role_missing"
            db.commit()
            return {"status": "failed", "reason": "role_missing"}
        if role.agent_paused_at is not None or not bool(role.agentic_mode_enabled):
            return _defer(db, db.get(AgentDecision, int(decision_id)), "role_not_runnable")

        from .agent_tasks import agent_manual_run

        try:
            result = agent_manual_run.run(
                role_id=role_id,
                application_id=application_id,
                dispatch_key=f"agent-reevaluation/{int(decision_id)}",
            )
        except Exception as exc:  # noqa: BLE001 - durable retry owns the failure
            logger.exception("agent re-evaluation failed decision=%s", decision_id)
            return _defer(
                db,
                db.get(AgentDecision, int(decision_id)),
                f"agent_cycle_error:{type(exc).__name__}",
            )

        decision = db.get(AgentDecision, int(decision_id))
        if result.get("status") == "ok" and result.get("run_status") == "failed":
            # A keyed paid run reached an honest terminal failure. Replaying
            # that key must not spend again; the AgentRun event surfaces the
            # failure and scheduled cycles can still produce a fresh decision.
            decision.reevaluation_status = "failed"
            decision.reevaluation_lease_until = None
            decision.reevaluation_next_attempt_at = None
            decision.reevaluation_error = "agent_cycle_failed"
            db.commit()
            return {"status": "failed", "reason": "agent_cycle_failed"}
        if result.get("status") != "ok" or result.get("run_status") != "succeeded":
            reason = str(result.get("reason") or result.get("run_status") or result.get("status"))
            return _defer(db, decision, f"agent_cycle_not_completed:{reason}")
        decision.reevaluation_status = "done"
        decision.reevaluation_lease_until = None
        decision.reevaluation_next_attempt_at = None
        decision.reevaluation_error = None
        db.commit()
        return {
            "status": "done",
            "decision_id": int(decision_id),
            "agent_run_id": result.get("agent_run_id"),
        }


@celery_app.task(name="app.tasks.reevaluation_tasks.recover_agent_re_evaluations")
def recover_agent_re_evaluations(limit: int = 100) -> dict:
    """Bounded recovery of pending receipts and expired worker leases."""
    from ..models.agent_decision import AgentDecision
    from ..platform.database import SessionLocal

    now = _now()
    with SessionLocal() as db:
        stale = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.reevaluation_status == "running",
                AgentDecision.reevaluation_lease_until < now,
            )
            .limit(max(1, int(limit)))
            .all()
        )
        for decision in stale:
            decision.reevaluation_status = "pending"
            decision.reevaluation_lease_until = None
            decision.reevaluation_next_attempt_at = now
            decision.reevaluation_error = "worker_interrupted"
        db.commit()
        ids = [
            int(row[0])
            for row in (
                db.query(AgentDecision.id)
                .filter(
                    AgentDecision.reevaluation_status == "pending",
                    (
                        AgentDecision.reevaluation_next_attempt_at.is_(None)
                        | (AgentDecision.reevaluation_next_attempt_at <= now)
                    ),
                )
                .order_by(AgentDecision.id.asc())
                .limit(max(1, int(limit)))
                .all()
            )
        ]

    kicked = publish_failed = 0
    for pending_id in ids:
        try:
            run_agent_re_evaluation.delay(pending_id)
            kicked += 1
        except Exception:
            publish_failed += 1
            logger.exception("re-evaluation recovery publish failed decision=%s", pending_id)
    return {
        "scanned": len(ids),
        "stale_recovered": len(stale),
        "kicked": kicked,
        "publish_failed": publish_failed,
    }
