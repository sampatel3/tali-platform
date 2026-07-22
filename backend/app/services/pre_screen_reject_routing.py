"""Role-local and human-review routing for pre-screen rejections.

The provider write-back flow remains in ``application_automation_service``.
This module owns the alternatives that must never fall through to that flow:
an independent related-role outcome and a Decision Hub review card.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from .agent_policy_settings import role_is_related
from .pre_screen_decision_emitter import queue_pre_screen_reject
from .pre_screening_service import mark_auto_reject_state


def try_related_role_local_pre_screen_reject(
    db,
    *,
    app: CandidateApplication,
    role: Role,
    decision: dict[str, Any],
    actor_type: str,
    actor_id: int | None,
) -> dict[str, Any] | None:
    """Resolve a deterministic reject only inside one explicit membership."""

    if not role_is_related(role):
        return None
    from .related_role_action_service import (
        lock_related_role_membership,
        transition_related_role_outcome_action,
    )

    try:
        locked = lock_related_role_membership(
            db,
            application=app,
            acting_role_id=int(role.id),
        )
        if locked is None:
            return None
        current_outcome = str(
            locked[1].application_outcome or "open"
        ).strip().lower()
        if current_outcome != "open":
            return {
                **decision,
                "performed": current_outcome == "rejected",
                "state": current_outcome,
                "reason": "Candidate has already left this role's active flow",
                "role_id": int(role.id),
                "role_local": True,
            }
        result = transition_related_role_outcome_action(
            db,
            application=app,
            acting_role_id=int(role.id),
            to_outcome="rejected",
            source="agent",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=str(
                decision.get("reason") or "Deterministic pre-screen reject"
            ),
            metadata={
                "acting_role_id": int(role.id),
                "source": "deterministic_pre_screen",
            },
            idempotency_key=f"related_pre_screen_reject:{role.id}:{app.id}",
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        if exc.status_code == 409:
            return {
                **decision,
                "performed": False,
                "state": "skipped",
                "reason": "Candidate has already left this role's active flow",
                "role_id": int(role.id),
                "role_local": True,
            }
        raise
    if result is None:
        return None
    if int(app.role_id or 0) == int(role.id):
        mark_auto_reject_state(
            app,
            state="rejected",
            reason=decision.get("reason"),
            triggered=True,
        )
    return {
        **decision,
        "performed": True,
        "state": "rejected",
        "workable_synced": False,
        "role_id": int(role.id),
        "role_local": True,
    }


def reject_related_role_for_cv_gap(
    db,
    *,
    app: CandidateApplication,
    role: Role | None,
    actor_type: str,
    actor_id: int | None,
    reason: str,
    trigger: str,
) -> dict[str, Any] | None:
    """Reject one related membership without mutating optional ATS transport."""

    if role is None or not role_is_related(role):
        return None
    from .related_role_action_service import transition_related_role_outcome_action

    result = transition_related_role_outcome_action(
        db,
        application=app,
        acting_role_id=int(role.id),
        to_outcome="rejected",
        source="recruiter" if actor_type == "recruiter" else "agent",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        metadata={"acting_role_id": int(role.id), "trigger": trigger},
        idempotency_key=f"{trigger}:{int(role.id)}:{int(app.id)}",
    )
    if result is None:
        return {
            "performed": False,
            "reason": "Candidate is not in this role's candidate pool",
            "role_id": int(role.id),
            "role_local": True,
        }
    resolved = (
        str(result.evaluation.application_outcome or "open").strip().lower()
        == "rejected"
    )
    return {
        "performed": resolved,
        "reason": reason,
        "role_id": int(role.id),
        "role_local": True,
        "ats_owner_unchanged": True,
    }


def divert_pre_screen_reject_to_card(
    db,
    *,
    app: CandidateApplication,
    role: Role | None,
    decision: dict[str, Any],
    carded_reason: str,
    fallback_state: str,
    fallback_reason: str,
) -> dict[str, Any]:
    """Create a review card when an automatic rejection cannot run safely."""

    snapshot = (
        decision.get("snapshot")
        if isinstance(decision.get("snapshot"), dict)
        else {}
    )
    config = (
        decision.get("config")
        if isinstance(decision.get("config"), dict)
        else {}
    )
    queued = (
        queue_pre_screen_reject(
            db,
            organization_id=int(app.organization_id),
            role=role,
            application=app,
            pre_screen_score=snapshot.get("pre_screen_score"),
            threshold=config.get("threshold_100"),
            evidence={
                "cv_fit_score": snapshot.get("cv_fit_score"),
                "requirements_fit_score": snapshot.get("requirements_fit_score"),
            },
        )
        if role is not None
        else None
    )
    if queued is None:
        mark_auto_reject_state(
            app,
            state=fallback_state,
            reason=fallback_reason,
            triggered=False,
        )
        return {
            **decision,
            "performed": False,
            "state": fallback_state,
            "reason": fallback_reason,
        }
    mark_auto_reject_state(
        app,
        state="awaiting_recruiter_approval",
        reason=carded_reason,
        triggered=False,
    )
    return {
        **decision,
        "performed": False,
        "state": "awaiting_recruiter_approval",
        "reason": carded_reason,
    }


__all__ = [
    "divert_pre_screen_reject_to_card",
    "reject_related_role_for_cv_gap",
    "try_related_role_local_pre_screen_reject",
]
