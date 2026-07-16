"""Shared dispatcher context for Agent Chat tool families."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role


UNHANDLED = object()


@dataclass(frozen=True)
class ToolContext:
    arguments: dict[str, Any]
    db: Session
    role: Role
    user: Any
    conversation: Any
    organization_id: int
    confirmation_binding: dict[str, int]
    expected_role_version: int | None

def _confirmation_binding(*, role: Role, user: Any, conversation: Any) -> dict[str, int]:
    """Authorization boundary persisted into every server preview receipt."""
    binding = {"organization_id": int(role.organization_id)}
    # Production Agent Chat is authenticated. Keeping read/helper dispatches
    # compatible with a missing synthetic user lets lower-level tests and
    # offline evaluators exercise read tools without manufacturing an actor;
    # command services themselves still enforce the real actor boundary.
    user_id = getattr(user, "id", None)
    if user_id is not None:
        binding["requested_by_user_id"] = int(user_id)
    if conversation is not None:
        binding["conversation_id"] = int(conversation.id)
    return binding

__all__ = ["ToolContext", "UNHANDLED", "_confirmation_binding"]
