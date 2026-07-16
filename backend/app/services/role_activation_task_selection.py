"""Assessment-task selection for durable role activation commands."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models.role import Role
from .task_provisioning_service import (
    authorize_assessment_task_provisioning,
    task_provisioning_state,
)


def prepare_activation_task(
    role: Role,
    *,
    now: datetime,
) -> tuple[dict[str, Any], int | None, str | None]:
    """Authorize missing generation and select one safe activation task."""

    try:
        linked = list(getattr(role, "tasks", None) or [])
    except Exception:
        linked = []
    if not linked:
        authorize_assessment_task_provisioning(
            role,
            reason="agent_turn_on",
            now=now,
        )

    provisioning = task_provisioning_state(role)
    exact_task_id = None
    task_selection_error = None
    if len(linked) == 1:
        task = linked[0]
        extra = task.extra_data if isinstance(task.extra_data, dict) else {}
        generated_review_draft = bool(
            not bool(task.is_active)
            and extra.get("generated")
            and extra.get("needs_review", True)
        )
        if bool(task.is_active) or generated_review_draft:
            # A preserved/manual active task needs no automatic content
            # approval. A generated draft follows battle-test → auto-approval.
            exact_task_id = int(task.id)
        else:
            task_selection_error = (
                "The linked assessment task is inactive and cannot be approved "
                "automatically because it is not a generated review draft. "
                "Approve or replace the task, then press Turn on again."
            )
    elif len(linked) > 1:
        task_selection_error = (
            "Turn on cannot choose safely between multiple linked assessment "
            "tasks. Keep one intended task (or configure the task experiment) "
            "and press Turn on again."
        )
    return provisioning, exact_task_id, task_selection_error


__all__ = ["prepare_activation_task"]
