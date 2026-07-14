"""Fail-closed lifecycle policy for native public job pages.

Job pages are snapshots; the linked :class:`Role` is the live authority for
whether Taali may advertise the role and accept a new application.  Keep this
policy independent of ``Role.source`` because a requisition role can later be
adopted by Workable, which legitimately changes its source while retaining the
same native page.
"""

from __future__ import annotations

from typing import Any

from ..models.role import JOB_STATUS_OPEN, ROLE_KIND_STANDARD, Role
from .ats_role_lifecycle import ats_job_lifecycle


INTAKE_READY = "ready"
INTAKE_ROLE_MISSING = "role_missing"
INTAKE_ROLE_DELETED = "role_deleted"
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

    if getattr(role, "deleted_at", None) is not None:
        return {"ready": False, "reason": INTAKE_ROLE_DELETED}

    ats = ats_job_lifecycle(role)
    if ats.external_job_id and ats.external_job_live is False:
        result = {
            "ready": False,
            "reason": INTAKE_ATS_JOB_NOT_LIVE,
            "ats_provider": ats.provider,
            "external_job_state": ats.external_job_state,
        }
        # Preserve the Workable-specific diagnostic key consumed by existing
        # clients while adding the provider-neutral contract for Bullhorn.
        if ats.provider == "workable":
            result["workable_job_state"] = ats.external_job_state
        return result

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


def role_allows_new_paid_ats_work(role: Role | None) -> bool:
    """Whether an ATS import may launch new model-backed parse/score work.

    ``starred_for_auto_sync`` deliberately is *not* an execution grant.  The
    star is sticky adoption/cadence metadata, whereas Turn on/Pause/Turn off is
    the live authority for autonomous spend.  Sync may therefore continue to
    refresh ATS metadata for a starred role while this returns ``False``.

    Provider lifecycle is fail-closed for explicit terminal states but remains
    permissive when an older payload has no state field, matching the existing
    Workable and Bullhorn import compatibility rules.
    """

    if role is None or getattr(role, "deleted_at", None) is not None:
        return False
    if (getattr(role, "role_kind", None) or ROLE_KIND_STANDARD) != ROLE_KIND_STANDARD:
        return False
    if not bool(getattr(role, "agentic_mode_enabled", False)):
        return False
    if getattr(role, "agent_paused_at", None) is not None:
        return False

    job_status = getattr(role, "job_status", None)
    if job_status is not None and job_status != JOB_STATUS_OPEN:
        return False

    ats = ats_job_lifecycle(role)
    if ats.external_job_id and ats.external_job_live is False:
        return False

    return True


__all__ = [
    "INTAKE_AGENT_OFF",
    "INTAKE_AGENT_PAUSED",
    "INTAKE_ATS_JOB_NOT_LIVE",
    "INTAKE_JOB_NOT_OPEN",
    "INTAKE_READY",
    "INTAKE_ROLE_DELETED",
    "native_intake_state",
    "role_accepts_native_applications",
    "role_allows_new_paid_ats_work",
    "role_uses_managed_native_lifecycle",
]
