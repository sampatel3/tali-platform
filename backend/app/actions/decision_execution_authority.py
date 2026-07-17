"""Optimistic authority carried from recruiter confirmation to execution.

Decision approval is asynchronous: the HTTP transaction accepts a row and a
durable worker performs the provider/local mutation later.  This module keeps
the exact action the recruiter saw attached to both phases and acquires the
worker's locks in the canonical application -> organization -> role family ->
decision order before any side effect can run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..schemas.role import RoleFamilyResponse
from ..services.role_family_reject_authority import (
    lock_current_role_families,
    require_expected_role_family,
)


DECISION_CHANGED = "DECISION_CHANGED"
_REJECT_TYPES = frozenset({"reject", "skip_assessment_reject"})
_SUPPORTED_OVERRIDES = {
    "send_assessment": frozenset({"reject", "skip_assessment_advance"}),
    "advance_to_interview": frozenset({"send_assessment", "reject"}),
    "reject": frozenset({"send_assessment", "advance"}),
    "skip_assessment_reject": frozenset(),
    "resend_assessment_invite": frozenset({"reject", "skip_assessment_advance"}),
    "escalate_low_confidence": frozenset(),
}


@dataclass(frozen=True)
class DecisionExecutionScope:
    application: CandidateApplication | None
    role: Role | None
    decision: AgentDecision


def role_family_payload(value: Any) -> dict[str, Any] | None:
    """Return a JSON-safe family snapshot for a durable operation payload."""

    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "dict"):
        return dict(value.dict())
    raise TypeError(f"unsupported role-family snapshot: {type(value).__name__}")


def require_expected_decision_type(
    *,
    decision_id: int,
    expected: str | None,
    current: str,
    required: bool,
) -> str:
    """Bind execution to the recommendation displayed to the recruiter."""

    expected_value = str(expected or "").strip()
    current_value = str(current or "").strip()
    if (expected_value or not required) and (
        not expected_value or expected_value == current_value
    ):
        return current_value
    raise HTTPException(
        status_code=409,
        detail={
            "code": DECISION_CHANGED,
            "message": (
                "This recommendation changed after it was displayed. Refresh "
                "the Decision Hub and confirm the current action again."
            ),
            "decision_id": int(decision_id),
            "expected_decision_type": expected_value or None,
            "current_decision_type": current_value or None,
        },
    )


def require_supported_override(
    *,
    decision_id: int,
    decision_type: str,
    override_action: str | None,
) -> None:
    """Reject a type-incompatible side effect even for direct API callers."""

    action = str(override_action or "").strip()
    if not action or action in {"hold", "manual_review"}:
        return
    allowed = _SUPPORTED_OVERRIDES.get(str(decision_type), frozenset())
    if action in allowed:
        return
    raise HTTPException(
        status_code=422,
        detail={
            "code": "UNSUPPORTED_DECISION_OVERRIDE",
            "message": (
                f"{action!r} is not an available alternative for "
                f"{str(decision_type)!r}. Refresh and choose a displayed action."
            ),
            "decision_id": int(decision_id),
            "decision_type": str(decision_type),
            "allowed_actions": sorted(allowed),
        },
    )


def _coerce_family(value: Any) -> RoleFamilyResponse | None:
    if value is None or isinstance(value, RoleFamilyResponse):
        return value
    return RoleFamilyResponse.model_validate(value)


def lock_decision_execution_scope(
    db: Session,
    *,
    organization_id: int,
    decision_id: int,
    expected_decision_type: str | None,
    expected_role_family: Any = None,
    reject_mode: Literal["approved_action", "override", "none"] = "none",
) -> DecisionExecutionScope:
    """Lock and revalidate the exact decision/family immediately before action."""

    identity = (
        db.query(AgentDecision.application_id, AgentDecision.role_id)
        .filter(
            AgentDecision.id == int(decision_id),
            AgentDecision.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    if identity is None:
        raise HTTPException(
            status_code=404,
            detail=f"agent_decision {int(decision_id)} not found",
        )

    application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(identity.application_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    if application is not None:
        from ..services.workspace_agent_control import (
            workspace_agent_control_snapshot,
        )

        workspace_agent_control_snapshot(
            db,
            organization_id=int(organization_id),
            lock=True,
        )

    role_id = int(application.role_id) if application is not None else None
    decision_role_id = int(identity.role_id)
    current_families = (
        lock_current_role_families(
            db,
            organization_id=int(organization_id),
            role_ids={role_id, decision_role_id},
        )
        if role_id is not None
        else {}
    )
    role = (
        db.query(Role)
        .filter(
            Role.id == decision_role_id,
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .one_or_none()
        if role_id is not None
        else None
    )
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == int(decision_id),
            AgentDecision.organization_id == int(organization_id),
            AgentDecision.application_id == int(identity.application_id),
            AgentDecision.role_id == decision_role_id,
        )
        .populate_existing()
        .with_for_update(of=AgentDecision)
        .one_or_none()
    )
    if decision is None:
        raise HTTPException(
            status_code=409,
            detail=f"agent_decision {int(decision_id)} changed before execution",
        )
    require_expected_decision_type(
        decision_id=int(decision.id),
        expected=expected_decision_type,
        current=str(decision.decision_type),
        required=expected_decision_type is not None,
    )

    rejects = reject_mode == "override" or (
        reject_mode == "approved_action"
        and str(decision.decision_type) in _REJECT_TYPES
    )
    current_family = current_families.get(int(decision.role_id))
    if (
        application is not None
        and (
            current_family is None
            or int(current_family.owner.id) != int(application.role_id)
        )
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": DECISION_CHANGED,
                "message": (
                    "This recommendation no longer belongs to the displayed "
                    "shared application. Refresh and review it again."
                ),
                "decision_id": int(decision.id),
            },
        )
    if rejects and current_family is not None:
        require_expected_role_family(
            expected=_coerce_family(expected_role_family),
            current=current_family,
        )
    return DecisionExecutionScope(
        application=application,
        role=role,
        decision=decision,
    )


__all__ = [
    "DECISION_CHANGED",
    "DecisionExecutionScope",
    "lock_decision_execution_scope",
    "require_expected_decision_type",
    "require_supported_override",
    "role_family_payload",
]
