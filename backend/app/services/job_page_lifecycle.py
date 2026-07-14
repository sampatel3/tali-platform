"""Fail-closed lifecycle policy for native public job pages.

Job pages are snapshots; the linked :class:`Role` is the live authority for
whether Taali may advertise the role and accept a new application.  Keep this
policy independent of ``Role.source`` because a requisition role can later be
adopted by Workable, which legitimately changes its source while retaining the
same native page.
"""

from __future__ import annotations

from typing import Any

from ..models.role import JOB_STATUS_OPEN, Role
from .workable_actions_service import WORKABLE_NON_LIVE_JOB_STATES, workable_job_state


INTAKE_READY = "ready"
INTAKE_ROLE_MISSING = "role_missing"
INTAKE_JOB_NOT_OPEN = "job_not_open"
INTAKE_AGENT_OFF = "agent_off"
INTAKE_AGENT_PAUSED = "agent_paused"
INTAKE_ATS_JOB_NOT_LIVE = "ats_job_not_live"


def role_uses_managed_native_lifecycle(role: Role | None) -> bool:
    """Whether this role carries the requisition/native-page lifecycle.

    ``job_status`` is the durable marker.  It survives the optional Workable
    adoption path even though that path changes ``source`` to ``workable``.
    Legacy pages whose roles pre-date this lifecycle keep their prior behaviour.
    """

    return role is not None and getattr(role, "job_status", None) is not None


def native_intake_state(role: Role | None) -> dict[str, Any]:
    """Return a public-safe, machine-readable native-intake decision.

    Managed pages fail closed unless the job is open and its agent is enabled
    and unpaused.  This makes Turn off/Pause a real stop for new intake and its
    model-backed parsing/scoring work.  A linked Workable job in a non-live
    state closes the native mirror as well, regardless of the role's current
    ``source`` value or stale local ``job_status``.
    """

    if role is None:
        return {"ready": False, "reason": INTAKE_ROLE_MISSING}

    state = workable_job_state(role)
    if getattr(role, "workable_job_id", None) and state in WORKABLE_NON_LIVE_JOB_STATES:
        return {
            "ready": False,
            "reason": INTAKE_ATS_JOB_NOT_LIVE,
            "workable_job_state": state,
        }

    if not role_uses_managed_native_lifecycle(role):
        # Compatibility for legacy/manual JobPage rows. New pages are created
        # only through requisition publish and always carry ``job_status``.
        return {"ready": True, "reason": INTAKE_READY}

    if getattr(role, "job_status", None) != JOB_STATUS_OPEN:
        return {
            "ready": False,
            "reason": INTAKE_JOB_NOT_OPEN,
            "job_status": getattr(role, "job_status", None),
        }
    if not bool(getattr(role, "agentic_mode_enabled", False)):
        return {"ready": False, "reason": INTAKE_AGENT_OFF}
    if getattr(role, "agent_paused_at", None) is not None:
        return {"ready": False, "reason": INTAKE_AGENT_PAUSED}
    return {"ready": True, "reason": INTAKE_READY}


def role_accepts_native_applications(role: Role | None) -> bool:
    return bool(native_intake_state(role).get("ready"))


__all__ = [
    "INTAKE_AGENT_OFF",
    "INTAKE_AGENT_PAUSED",
    "INTAKE_ATS_JOB_NOT_LIVE",
    "INTAKE_JOB_NOT_OPEN",
    "INTAKE_READY",
    "native_intake_state",
    "role_accepts_native_applications",
    "role_uses_managed_native_lifecycle",
]
