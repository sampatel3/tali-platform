"""Recruiter-visible terminal events emitted by autonomous agent cycles."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..models.agent_run import AgentRun
from ..models.candidate_application_event import CandidateApplicationEvent

logger = logging.getLogger("taali.agent_runtime")


def emit_cycle_abort_event(
    db: Session,
    *,
    run: AgentRun,
    application_id: int | None,
    reason: str,
) -> None:
    """Surface an aborted application-focused cycle on its timeline.

    Role-wide cron failures remain visible through ``AgentRun`` status. Event
    persistence is best-effort so observability can never break the run path.
    """

    if application_id is None:
        return
    try:
        idempotency_key = f"agent_cycle_aborted:run:{int(run.id) if run.id else 0}"
        db.add(
            CandidateApplicationEvent(
                application_id=int(application_id),
                organization_id=int(run.organization_id),
                event_type="agent_cycle_aborted",
                actor_type="agent",
                actor_id=int(run.id) if run.id else None,
                reason=reason,
                idempotency_key=idempotency_key,
                event_metadata={
                    "agent_run_id": int(run.id) if run.id else None,
                    "status": str(run.status),
                    "trigger": str(run.trigger),
                },
            )
        )
    except Exception:  # pragma: no cover - defensive observability path
        logger.exception(
            "agent_cycle_aborted event emit failed run_id=%s app_id=%s",
            getattr(run, "id", None),
            application_id,
        )


__all__ = ["emit_cycle_abort_event"]
