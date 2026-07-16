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
    dispatch_key: str | None = None,
):
    """Publish one role Agent kick while preserving role-specific semantics."""

    if str(role.role_kind or "") == ROLE_KIND_SISTER:
        from ..tasks.sister_role_tasks import score_sister_role

        kwargs = {}
        if dispatch_key is not None:
            kwargs["dispatch_key"] = dispatch_key
            kwargs["organization_id"] = int(role.organization_id)
        if application_id is not None:
            kwargs["application_id"] = int(application_id)
        return score_sister_role.apply_async(
            args=[int(role.id)],
            **({"kwargs": kwargs} if kwargs else {}),
            queue="scoring",
        )
    if manual:
        from ..tasks.agent_tasks import agent_manual_run

        task_kwargs = {
            "role_id": int(role.id),
            "application_id": application_id,
        }
        if dispatch_key is not None:
            task_kwargs["dispatch_key"] = dispatch_key
            task_kwargs["organization_id"] = int(role.organization_id)
        return agent_manual_run.delay(**task_kwargs)

    from ..tasks.agent_tasks import agent_cohort_tick_role

    kwargs = {
        "activation": bool(activation),
        "dispatch_role_version": int(role_version or role.version or 1),
    }
    if workspace_version is not None:
        kwargs["dispatch_workspace_version"] = int(workspace_version)
    return agent_cohort_tick_role.delay(int(role.id), **kwargs)


__all__ = ["dispatch_role_agent_cycle"]
