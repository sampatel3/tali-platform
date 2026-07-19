"""Durable outcome reconstruction for confirmed Decision Hub commands."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.user import User


def recover_confirmed_action(
    db: Session,
    role: Role,
    user: User,
    *,
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Close a pending chat receipt from the canonical domain's durable state.

    Decision routes commit before returning because their workers need to see
    processing/feedback rows. A killed chat worker can therefore leave a
    pending command receipt after the mutation is already durable. Potentially
    non-idempotent provider actions are never invoked again from this path.
    """

    from . import decision_commands

    decision_id = int(arguments["decision_id"])
    decision = decision_commands._scoped_decision(db, role, user, decision_id)
    if action == "teach_decision":
        from ..models.decision_feedback import DecisionFeedback

        feedback = (
            db.query(DecisionFeedback)
            .filter(
                DecisionFeedback.decision_id == decision_id,
                DecisionFeedback.organization_id == int(role.organization_id),
                DecisionFeedback.reviewer_id == int(user.id),
                DecisionFeedback.failure_mode == arguments.get("failure_mode"),
                DecisionFeedback.correction_text == arguments.get("correction_text"),
                DecisionFeedback.scope == arguments.get("scope"),
                DecisionFeedback.attributed_to == arguments.get("attributed_to"),
                DecisionFeedback.direction == arguments.get("direction"),
            )
            .order_by(DecisionFeedback.id.desc())
            .first()
        )
        if feedback is None:
            return {
                "ok": False,
                "operation": action,
                "decision_id": decision_id,
                "status": "review_required",
                "detail": "No matching durable feedback row was found; no replay was attempted.",
            }
        return {
            "ok": True,
            "operation": action,
            "decision_id": decision_id,
            "feedback_id": int(feedback.id),
            "decision_status": str(decision.status),
            "scope": str(feedback.scope),
            "cosign_required": bool(feedback.cosign_required),
            "status": "recorded",
            "recovered": True,
        }

    if action == "re_evaluate_decision":
        reevaluation_status = str(decision.reevaluation_status or "") or None
        return {
            "ok": True,
            "operation": action,
            "decision_id": decision_id,
            "role_id": int(decision.role_id),
            "application_id": int(decision.application_id),
            "status": reevaluation_status or str(decision.status),
            "queued": reevaluation_status in {"pending", "running"},
            "recovered": True,
        }

    actionable = str(decision.status) in {"pending", "reverted_for_feedback"}
    return {
        "ok": not actionable,
        "operation": action,
        "decision_id": decision_id,
        "role_id": int(decision.role_id),
        "application_id": int(decision.application_id),
        "status": str(decision.status) if not actionable else "review_required",
        "queued": str(decision.status) == "processing",
        "detail": (
            None
            if not actionable
            else (
                "The earlier dispatch no longer has a live processing receipt. "
                "It was not replayed because the provider outcome may be ambiguous; "
                "review the decision before trying again."
            )
        ),
        "recovered": True,
    }


__all__ = ["recover_confirmed_action"]
