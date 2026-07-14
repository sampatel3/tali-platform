"""Provider-neutral ATS job linkage and lifecycle metadata.

Role rows retain provider-specific columns for backwards compatibility.  This
module is the single read contract for callers that should not need to know
whether the linked external job lives in Workable or Bullhorn.

Workable deliberately wins when both links are present, matching
``components.integrations.resolver.resolve_ats_provider``.  A dual-linked role
is a migration edge; exposing a different provider here would make the API's
job state disagree with the provider that receives reads and writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models.role import Role
from .workable_actions_service import workable_job_state, workable_job_syncable


@dataclass(frozen=True, slots=True)
class AtsJobLifecycle:
    provider: str | None = None
    external_job_id: str | None = None
    external_job_state: str | None = None
    external_job_live: bool | None = None


def _normalized_text(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _remote_boolean(value: Any) -> bool | None:
    """Parse an optional ATS boolean without treating absence as false."""

    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def ats_job_lifecycle(role: Role | None) -> AtsJobLifecycle:
    """Resolve a role's active ATS job using the platform's provider precedence.

    ``external_job_live`` is ``None`` only when there is no linked ATS job.  For
    old linked rows whose remote payload predates a lifecycle field, the value
    remains permissive (``True``), matching the existing sync contract.  An
    explicitly closed remote job or a soft-deleted local mirror is never live.
    """

    if role is None:
        return AtsJobLifecycle()

    deleted = getattr(role, "deleted_at", None) is not None

    workable_id = _normalized_text(getattr(role, "workable_job_id", None))
    if workable_id:
        state = workable_job_state(role)
        return AtsJobLifecycle(
            provider="workable",
            external_job_id=str(getattr(role, "workable_job_id")).strip(),
            external_job_state=state,
            external_job_live=bool(not deleted and workable_job_syncable(role)),
        )

    bullhorn_id = _normalized_text(getattr(role, "bullhorn_job_order_id", None))
    if bullhorn_id:
        payload = getattr(role, "bullhorn_job_data", None)
        payload = payload if isinstance(payload, dict) else {}
        explicit_live = _remote_boolean(payload.get("isOpen"))
        state = _normalized_text(payload.get("status"))
        if state is None and explicit_live is not None:
            state = "open" if explicit_live else "closed"
        return AtsJobLifecycle(
            provider="bullhorn",
            external_job_id=str(getattr(role, "bullhorn_job_order_id")).strip(),
            external_job_state=state,
            # Bullhorn's sync historically accepted payloads without isOpen.
            # Keep that compatibility, but fail closed on an explicit false or
            # on the soft-delete written by close/delete event handling.
            external_job_live=bool(not deleted and explicit_live is not False),
        )

    return AtsJobLifecycle()


__all__ = ["AtsJobLifecycle", "ats_job_lifecycle"]
