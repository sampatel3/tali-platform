"""Provider-backed auto-execution for interview-advance decisions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..actions.types import Actor
from ..models.agent_decision import AgentDecision
from ..models.organization import Organization
from ..models.role import Role


def execute_advance_auto_decision_provider(
    db: Session,
    *,
    role: Role,
    decision: AgentDecision,
    auto_toggle: str | None,
) -> bool:
    """Run the durable ATS lifecycle and retain an actionable hold on failure."""
    from fastapi import HTTPException

    from ..services.decision_provider_lifecycle import (
        execute_decision_provider_lifecycle,
    )
    from ..services.workable_actions_service import (
        WorkableWritebackError,
        resolve_workable_interview_stage,
    )

    organization_id = int(role.organization_id)
    decision_id = int(decision.id)
    organization = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .one_or_none()
    )
    target_stage, _target_error = resolve_workable_interview_stage(
        organization,
        role,
    )
    reason = (
        f"Auto-approved per role.{auto_toggle} "
        f"(decision #{decision_id})"
    )
    try:
        result = execute_decision_provider_lifecycle(
            db,
            organization_id=organization_id,
            decision_id=decision_id,
            disposition="approved",
            actor=Actor.system(),
            note=reason,
            target_stage=target_stage,
            expected_decision_type="advance_to_interview",
        )
    except (HTTPException, WorkableWritebackError) as exc:
        db.rollback()
        current = db.get(AgentDecision, decision_id)
        if current is not None:
            held = dict(current.evidence or {})
            held["auto_execute_hold"] = {
                "status": "ats_provider_not_confirmed",
                "detail": str(getattr(exc, "detail", None) or exc),
            }
            current.evidence = held
            db.add(current)
        return False
    return result.get("status") == "ok"


__all__ = ["execute_advance_auto_decision_provider"]
