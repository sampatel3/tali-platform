"""Fail-closed provider fence shared by auto-reject and agent controls.

The ATS runner holds its provider mutex from the live policy check through
local reconciliation. Pause/Turn-off acquires both provider namespaces before
locking Role/Organization state. Whichever side wins is therefore observable:
the control commits first and the worker cards, or the provider operation
finishes first and the control waits (or fails visibly on its bounded wait).
"""

from __future__ import annotations

import time

from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.orm import Session

_HANDLES_KEY = "agent_control_ats_fence_handles"
_LISTENER_KEY = "agent_control_ats_fence_listener"
_WAIT_SECONDS = 15.0


class AgentControlAtsFenceUnavailable(RuntimeError):
    def __init__(self, *, busy: bool):
        self.busy = bool(busy)
        super().__init__("ATS operation is in flight" if busy else "ATS fence unavailable")


def _namespaces() -> tuple[str, ...]:
    from ..components.integrations.bullhorn.sync_runner import (
        BULLHORN_ORG_MUTEX_NAMESPACE,
    )
    from ..tasks.workable_mutex import _WORKABLE_ORG_MUTEX_KEY_PREFIX

    return tuple(sorted({_WORKABLE_ORG_MUTEX_KEY_PREFIX, BULLHORN_ORG_MUTEX_NAMESPACE}))


def _release(handles: list[object]) -> None:
    from ..tasks.workable_mutex import _release_workable_org_mutex

    for handle in reversed(handles):
        _release_workable_org_mutex(handle)


def acquire_agent_control_ats_fence(
    organization_id: int, *, wait_seconds: float = _WAIT_SECONDS
) -> list[object]:
    """Acquire both provider mutexes, or fail without changing agent state."""

    from ..tasks.workable_mutex import _acquire_workable_org_mutex

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    saw_busy = False
    while True:
        handles: list[object] = []
        unavailable = False
        for namespace in _namespaces():
            handle = _acquire_workable_org_mutex(
                int(organization_id),
                source="agent_control",
                heartbeat=True,
                namespace=namespace,
            )
            if handle is False:
                unavailable = True
                break
            if handle is None:
                saw_busy = True
                break
            handles.append(handle)
        if len(handles) == len(_namespaces()):
            return handles
        _release(handles)
        if unavailable:
            raise AgentControlAtsFenceUnavailable(busy=False)
        if time.monotonic() >= deadline:
            raise AgentControlAtsFenceUnavailable(busy=saw_busy)
        time.sleep(0.05)


def require_agent_control_transaction_fence(
    db: Session, *, organization_id: int
) -> None:
    """Hold the provider fence until this session's outer transaction ends."""

    existing = db.info.get(_HANDLES_KEY)
    if existing:
        return
    try:
        handles = acquire_agent_control_ats_fence(int(organization_id))
    except AgentControlAtsFenceUnavailable as exc:
        if exc.busy:
            raise HTTPException(
                status_code=409,
                detail=(
                    "An ATS candidate update is still finishing. No agent "
                    "control changed; retry Pause/Turn off in a moment."
                ),
            ) from exc
        raise HTTPException(
            status_code=503,
            detail=(
                "Agent control could not acquire its ATS safety fence. No "
                "agent control changed; retry when the integration runtime is healthy."
            ),
        ) from exc
    db.info[_HANDLES_KEY] = handles
    if db.info.get(_LISTENER_KEY):
        return

    def _release_after_outer_transaction(session: Session, transaction) -> None:
        if getattr(transaction, "parent", None) is not None:
            return
        held = session.info.pop(_HANDLES_KEY, [])
        _release(list(held))

    event.listen(db, "after_transaction_end", _release_after_outer_transaction)
    db.info[_LISTENER_KEY] = True


def require_authorized_agent_control_transaction_fence(
    db: Session, *, current_user, role_id: int
) -> None:
    """Authorize without a row lock, then take the provider control fence."""

    from ..domains.assessments_runtime.job_authorization import (
        JobPermission,
        require_job_permission,
    )

    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=int(role_id),
        permission=JobPermission.CONTROL_AGENT,
        lock_for_update=False,
    )
    require_agent_control_transaction_fence(
        db, organization_id=int(role.organization_id)
    )
    # The mutex wait may outlive the read-only snapshot. Force the caller's
    # later FOR UPDATE authorization to hydrate current role/version state.
    db.expire(role)


def fence_agent_chat_pause_tool(
    db: Session, *, role, user, tool_name: str, arguments: dict
) -> None:
    """Authorize read-only, then fence chat Pause before its Role row lock."""

    if tool_name != "set_agent_state":
        return
    action = str((arguments or {}).get("action") or "").strip().lower()
    if action not in {"pause", "stop", "hold", "suspend"}:
        return
    # Do not let Redis health mask a 403 or let an unauthorized member contend
    # the org's ATS mutex. The dispatcher repeats this check under FOR UPDATE
    # after the fence, closing the team-membership TOCTOU window.
    require_authorized_agent_control_transaction_fence(
        db,
        current_user=user,
        role_id=int(role.id),
    )


__all__ = [
    "AgentControlAtsFenceUnavailable",
    "acquire_agent_control_ats_fence",
    "fence_agent_chat_pause_tool",
    "require_agent_control_transaction_fence",
    "require_authorized_agent_control_transaction_fence",
]
