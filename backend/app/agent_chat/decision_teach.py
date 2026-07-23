"""Validated Agent Chat adapter for the canonical teach-feedback workflow."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..domains.agentic._hub_shared import FeedbackBody
from ..models.decision_feedback import (
    ATTRIBUTED_TO_VALUES,
    FAILURE_MODES,
    FEEDBACK_DIRECTIONS,
    FEEDBACK_SCOPES,
)
from ..models.role import Role
from ..models.user import User


def _validate_choice(
    value: str | None,
    *,
    field: str,
    allowed: tuple[str, ...],
    optional: bool = False,
) -> str | None:
    from .decision_commands import DecisionCommandError

    if value is None and optional:
        return None
    normalized = str(value or "").strip()
    if normalized not in allowed:
        raise DecisionCommandError(
            f"unsupported_{field}",
            f"Unsupported {field} {normalized!r}. Allowed: {list(allowed)}.",
            details={"allowed": list(allowed)},
        )
    return normalized


def _feedback_values(result: Any) -> tuple[int, str, bool]:
    feedback = getattr(result, "feedback", None)
    if feedback is None and isinstance(result, dict):
        feedback = result.get("feedback")
    if isinstance(feedback, dict):
        feedback_id = feedback["id"]
        cosign_required = feedback.get("cosign_required", False)
    else:
        feedback_id = feedback.id
        cosign_required = getattr(feedback, "cosign_required", False)
    status = (
        result.get("decision_status")
        if isinstance(result, dict)
        else result.decision_status
    )
    return int(feedback_id), str(status), bool(cosign_required)


def normalize_teach_payload(
    *,
    failure_mode: str,
    correction_text: str,
    scope: str,
    attributed_to: str | None = None,
    direction: str | None = None,
) -> dict[str, str | None]:
    """Validate and normalize model-facing teach fields before previewing."""

    from .decision_commands import DecisionCommandError

    failure = _validate_choice(
        failure_mode, field="failure_mode", allowed=FAILURE_MODES
    )
    feedback_scope = _validate_choice(scope, field="scope", allowed=FEEDBACK_SCOPES)
    attribution = _validate_choice(
        attributed_to,
        field="attributed_to",
        allowed=ATTRIBUTED_TO_VALUES,
        optional=True,
    )
    feedback_direction = _validate_choice(
        direction,
        field="direction",
        allowed=FEEDBACK_DIRECTIONS,
        optional=True,
    )
    correction = str(correction_text or "").strip()
    if not correction:
        raise DecisionCommandError(
            "correction_required",
            "Explain what the agent got wrong and what it should do instead.",
        )
    if len(correction) > 8000:
        raise DecisionCommandError(
            "correction_too_long",
            "The correction must be at most 8,000 characters.",
        )
    return {
        "failure_mode": failure,
        "correction_text": correction,
        "scope": feedback_scope,
        "attributed_to": attribution,
        "direction": feedback_direction,
    }


def teach_decision(
    db: Session,
    role: Role,
    user: User,
    *,
    decision_id: int,
    failure_mode: str,
    correction_text: str,
    scope: str,
    attributed_to: str | None = None,
    direction: str | None = None,
) -> dict[str, Any]:
    """Send a role decision back with a structured correction.

    ``graph_write_hints`` is intentionally absent from this model-facing
    contract. Org feedback is accepted but remains inert until co-signed.
    """
    from .decision_commands import (
        DecisionCommandError,
        _candidate_scope,
        _scoped_decision,
        _scoped_decision_subject,
        _translate_http_error,
    )

    decision = _scoped_decision(db, role, user, decision_id)
    if str(decision.status) not in {"pending", "reverted_for_feedback"}:
        raise DecisionCommandError(
            "decision_not_teachable",
            (
                f"Decision {int(decision.id)} is {decision.status!r}; only pending "
                "or previously taught decisions can be sent back."
            ),
        )
    candidate_scope = _candidate_scope(
        db,
        role,
        organization_id=int(decision.organization_id),
    )
    _scoped_decision_subject(
        db,
        scope=candidate_scope,
        decision=decision,
    )
    payload = normalize_teach_payload(
        failure_mode=failure_mode,
        correction_text=correction_text,
        scope=scope,
        attributed_to=attributed_to,
        direction=direction,
    )

    from ..domains.agentic import hub_feedback_routes

    body = FeedbackBody(
        decision_id=int(decision.id),
        failure_mode=str(payload["failure_mode"]),
        correction_text=str(payload["correction_text"]),
        scope=str(payload["scope"]),
        # Never accept a role id from the model. Decision/role feedback is
        # pinned to the chat role; org feedback is deliberately role-less.
        role_id=int(role.id) if payload["scope"] in {"decision", "role"} else None,
        attributed_to=payload["attributed_to"],
        direction=payload["direction"],
        graph_write_hints=None,
    )
    try:
        result = hub_feedback_routes.create_feedback(
            body=body,
            db=db,
            current_user=user,
        )
    except HTTPException as exc:
        raise _translate_http_error(exc) from exc

    feedback_id, decision_status, cosign_required = _feedback_values(result)
    return {
        "ok": True,
        "operation": "teach_decision",
        "decision_id": int(decision.id),
        "feedback_id": feedback_id,
        "decision_status": decision_status,
        "scope": payload["scope"],
        "cosign_required": cosign_required,
    }


def get_teachable_decision(
    db: Session,
    role: Role,
    user: User,
    decision_id: int,
) -> dict[str, Any]:
    """Return a compact, live snapshot for a teach confirmation preview."""

    from .decision_commands import (
        DecisionCommandError,
        _candidate_scope,
        _scoped_decision,
        _scoped_decision_subject,
    )

    decision = _scoped_decision(db, role, user, decision_id)
    if str(decision.status) not in {"pending", "reverted_for_feedback"}:
        raise DecisionCommandError(
            "decision_not_teachable",
            (
                f"Decision {int(decision.id)} is {decision.status!r}; only pending "
                "or previously taught decisions can be sent back."
            ),
        )
    candidate_scope = _candidate_scope(
        db,
        role,
        organization_id=int(decision.organization_id),
    )
    _application, candidate, _evaluation = _scoped_decision_subject(
        db,
        scope=candidate_scope,
        decision=decision,
    )
    candidate_name = getattr(candidate, "full_name", None)
    return {
        "decision_id": int(decision.id),
        "application_id": int(decision.application_id),
        "candidate_name": candidate_name or "Unnamed candidate",
        "decision_type": str(decision.decision_type),
        "recommendation": str(decision.recommendation),
        "status": str(decision.status),
        "reasoning": str(decision.reasoning or ""),
        "created_at": decision.created_at.isoformat() if decision.created_at else None,
    }


__all__ = ["get_teachable_decision", "normalize_teach_payload", "teach_decision"]
