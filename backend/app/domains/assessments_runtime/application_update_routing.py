"""Compatibility routing for generic application outcome patches."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from ...schemas.role import ApplicationOutcomeUpdate
from .pipeline_service import map_legacy_status_to_pipeline

_OUTCOME_FIELDS = frozenset(
    {
        "application_outcome",
        "status",
        "expected_version",
        "expected_role_family",
        "acting_role_id",
        "reason",
        "idempotency_key",
    }
)


def generic_outcome_update(
    updates: dict[str, Any],
) -> ApplicationOutcomeUpdate | None:
    """Return a canonical outcome request or leave ordinary patches alone."""

    status = updates.get("status")
    mapped_outcome = (
        map_legacy_status_to_pipeline(str(status))[1]
        if status is not None
        else None
    )
    legacy_outcome = mapped_outcome if mapped_outcome != "open" else None
    requested_outcome = updates.get("application_outcome")
    if (
        requested_outcome is not None
        and legacy_outcome is not None
        and requested_outcome != legacy_outcome
    ):
        raise HTTPException(
            status_code=422,
            detail="status and application_outcome request different outcomes",
        )
    if requested_outcome is not None and status is not None and legacy_outcome is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Change application_outcome separately from a pipeline status"
            ),
        )
    target_outcome = requested_outcome or legacy_outcome
    if target_outcome is None:
        return None

    mixed_fields = sorted(set(updates) - _OUTCOME_FIELDS)
    if mixed_fields:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "APPLICATION_OUTCOME_PATCH_MUST_BE_ISOLATED",
                "message": (
                    "Change the candidate outcome separately from profile, "
                    "notes, or pipeline-stage edits."
                ),
                "mixed_fields": mixed_fields,
            },
        )
    return ApplicationOutcomeUpdate(
        application_outcome=target_outcome,
        expected_version=updates.get("expected_version"),
        expected_role_family=updates.get("expected_role_family"),
        acting_role_id=updates.get("acting_role_id"),
        reason=updates.get("reason"),
        idempotency_key=updates.get("idempotency_key"),
    )


def guard_closed_application_status_patch(
    updates: dict[str, Any], *, current_outcome: str | None
) -> None:
    """Prevent legacy stage/status writes from silently reopening an outcome."""

    status = updates.get("status")
    if status is None or str(current_outcome or "open").strip().lower() == "open":
        return
    _stage, target_outcome = map_legacy_status_to_pipeline(str(status))
    if target_outcome == "open":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "APPLICATION_OUTCOME_ENDPOINT_REQUIRED",
                "message": (
                    "Reopen the candidate through the dedicated outcome action, "
                    "then change their pipeline stage separately."
                ),
            },
        )


__all__ = ["generic_outcome_update", "guard_closed_application_status_patch"]
