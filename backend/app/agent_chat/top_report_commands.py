"""Agent Chat adapter for confirmed candidate-report publishing."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role
from ..services.candidate_report_command import execute_confirmed_candidate_report
from .confirmations import (
    blocked_confirmation_result,
    require_later_turn_confirmation,
)


def create_top_candidates_report(
    db: Session,
    *,
    role: Role,
    user: Any,
    conversation: Any,
    binding: dict[str, int],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Preview, later-turn confirm, revalidate, then publish one report."""
    if conversation is None:
        return blocked_confirmation_result(
            "create_top_candidates_report",
            "No persisted chat confirmation is available.",
        )

    token = str(arguments.get("confirmation_token") or "") or None
    return execute_confirmed_candidate_report(
        db,
        kind="top_candidates",
        role=role,
        user=user,
        conversation_kind="agent",
        conversation_id=int(conversation.id),
        binding=binding,
        arguments=arguments,
        resolve_confirmation=lambda operation: require_later_turn_confirmation(
            db,
            conversation=conversation,
            operation=operation,
            token=token,
            user=user,
        ),
    )


__all__ = ["create_top_candidates_report"]
