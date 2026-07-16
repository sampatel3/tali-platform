"""Dispatch the correct Agent worker for native and related roles."""

from __future__ import annotations

from ..models.role import ROLE_KIND_SISTER, Role


def dispatch_role_agent_cycle(
    role: Role,
    *,
    activation: bool = False,
    manual: bool = False,
    application_id: int | None = None,
    role_version: int | None = None,
    workspace_version: int | None = None,
):
    """Publish one role Agent kick while preserving role-specific semantics."""

    if str(role.role_kind or "") == ROLE_KIND_SISTER:
        from ..tasks.sister_role_tasks import score_sister_role

        return score_sister_role.apply_async(args=[int(role.id)], queue="scoring")
    if manual:
        from ..tasks.agent_tasks import agent_manual_run

        return agent_manual_run.delay(
            role_id=int(role.id), application_id=application_id
        )

    from ..tasks.agent_tasks import agent_cohort_tick_role

    kwargs = {
        "activation": bool(activation),
        "dispatch_role_version": int(role_version or role.version or 1),
    }
    if workspace_version is not None:
        kwargs["dispatch_workspace_version"] = int(workspace_version)
    return agent_cohort_tick_role.delay(int(role.id), **kwargs)


__all__ = ["dispatch_role_agent_cycle"]
