"""Terminal completion policy for durable ATS move operations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .workable_actions_service import WorkableWritebackError


def terminalize_skipped_move_result(
    db: Session,
    organization_id: int,
    op_type: str,
    payload: dict,
    result: dict,
    job_run_id: int,
) -> dict | None:
    """Fail an accepted move that later resolved to a provider no-op.

    Move handlers normally raise a terminal error when their application scope
    disappears. This shell-level policy keeps future provider adapters from
    accidentally recording a skipped mutation as provider success.
    """

    if op_type != "move_stage" or result.get("status") != "skipped":
        return None

    from . import background_job_runs
    from . import workable_op_runner

    reason = str(result.get("reason") or "application_unavailable")
    error = WorkableWritebackError(
        action=op_type,
        code=reason,
        message="The ATS move could not run because its application changed",
        retriable=False,
    )
    workable_op_runner.surface_op_failure(
        db,
        organization_id=int(organization_id),
        op_type=op_type,
        payload=payload,
        error=error,
    )
    failed_result = {**result, "status": "failed", "op_type": op_type, "code": reason}
    background_job_runs.update_run(
        job_run_id,
        status="failed",
        counters=failed_result,
        error=error.message,
        finished=True,
    )
    return failed_result


__all__ = ["terminalize_skipped_move_result"]
