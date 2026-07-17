"""Durable assessment-task provisioning intent and best-effort kick."""

from __future__ import annotations

import logging
from typing import Any


def request_autogenerate_assessment_task(
    role: Any,
    *,
    reason: str,
    supersede_generated_drafts: bool = False,
    defer_until_activation: bool = False,
) -> bool:
    """Stamp durable generation intent in the caller-owned transaction."""

    from ...platform.config import settings

    if not getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False):
        return False
    from ...services.task_provisioning_service import (
        request_assessment_task_provisioning,
    )

    return request_assessment_task_provisioning(
        role,
        reason=reason,
        supersede_generated_drafts=supersede_generated_drafts,
        defer_until_activation=defer_until_activation,
    )


def maybe_autogenerate_assessment_task(role: Any) -> None:
    """Kick generation after commit; Beat recovers a failed broker kick."""

    try:
        from ...platform.config import settings

        if not getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False):
            return
        from ...services.task_provisioning_service import (
            PROVISIONING_RECOVERABLE_STATUSES,
            task_provisioning_state,
        )

        state = task_provisioning_state(role)
        if state and str(state.get("status") or "") not in PROVISIONING_RECOVERABLE_STATUSES:
            return
        from ...tasks.assessment_tasks import generate_assessment_task_for_role

        generate_assessment_task_for_role.delay(
            int(role.id), int(role.organization_id)
        )
    except Exception:  # pragma: no cover - provisioning remains recoverable
        logging.getLogger("taali.roles").warning(
            "auto-generate enqueue failed for role %s",
            getattr(role, "id", "?"),
            exc_info=True,
        )


__all__ = [
    "maybe_autogenerate_assessment_task",
    "request_autogenerate_assessment_task",
]
