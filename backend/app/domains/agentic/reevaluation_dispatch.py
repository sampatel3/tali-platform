"""Transactional receipt + immediate kick for decision re-evaluation."""
from __future__ import annotations

import logging

logger = logging.getLogger("taali.agentic.reevaluation_dispatch")


def persist_intent(db, *, decision, role, supersede) -> tuple[int, bool]:
    runnable = bool(
        role is not None
        and role.agent_paused_at is None
        and role.agentic_mode_enabled
    )
    count = supersede(
        db,
        int(decision.application_id),
        reason="recruiter_requested_re_evaluate",
    )
    if runnable:
        decision.reevaluation_status = "pending"
        decision.reevaluation_attempts = 0
        decision.reevaluation_next_attempt_at = None
        decision.reevaluation_lease_until = None
        decision.reevaluation_error = None
    db.commit()
    return count, runnable


def kick(decision_id: int) -> tuple[bool, str | None]:
    from ...tasks.reevaluation_tasks import run_agent_re_evaluation

    try:
        result = run_agent_re_evaluation.delay(int(decision_id))
        return True, str(result.id) if getattr(result, "id", None) else None
    except Exception:
        # Broker acceptance is ambiguous; the committed receipt remains the
        # source of truth and Beat safely retries it.
        logger.exception(
            "re-evaluation publish failed/ambiguous decision=%s; recovery pending",
            decision_id,
        )
        return False, None
