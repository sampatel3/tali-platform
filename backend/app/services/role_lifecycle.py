"""Fail-closed lifecycle transitions for ATS-backed roles."""

from __future__ import annotations

from datetime import datetime

from ..models.role import Role
from .role_activation_intent import cancel_role_activation_intent


def _provider_name(provider: str) -> str:
    return str(provider or "ATS").strip()[:80] or "ATS"


def stop_role_for_ats_deletion(
    role: Role,
    *,
    deleted_at: datetime,
    provider: str,
) -> bool:
    """Soft-delete ``role`` and make every agent restart path fail closed.

    The function is deliberately idempotent but also repairs historical rows
    that were already soft-deleted while their agent was still enabled or a
    deferred activation command remained pending.
    """

    before = (
        role.deleted_at,
        bool(role.agentic_mode_enabled),
        role.agent_paused_at,
        role.agent_paused_reason,
        role.assessment_task_provisioning,
    )
    label = _provider_name(provider)
    if role.deleted_at is None:
        role.deleted_at = deleted_at
    role.agentic_mode_enabled = False
    if role.agent_paused_at is None:
        role.agent_paused_at = deleted_at
    role.agent_paused_reason = f"{label} job deleted or closed; agent turned off"
    cancel_role_activation_intent(
        role,
        user_id=None,
        reason=f"{label} job deleted or closed",
        now=deleted_at,
    )
    after = (
        role.deleted_at,
        bool(role.agentic_mode_enabled),
        role.agent_paused_at,
        role.agent_paused_reason,
        role.assessment_task_provisioning,
    )
    return before != after


def restore_role_from_ats(
    role: Role,
    *,
    restored_at: datetime,
    provider: str,
) -> bool:
    """Restore visibility while keeping the role's agent explicitly stopped."""

    if role.deleted_at is None:
        return False
    label = _provider_name(provider)
    role.deleted_at = None
    role.agentic_mode_enabled = False
    role.agent_paused_at = restored_at
    role.agent_paused_reason = (
        f"{label} job restored; turn the agent on explicitly after review"
    )
    cancel_role_activation_intent(
        role,
        user_id=None,
        reason=f"{label} job restored with agent off",
        now=restored_at,
    )
    return True


__all__ = ["restore_role_from_ats", "stop_role_for_ats_deletion"]
