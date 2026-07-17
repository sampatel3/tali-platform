"""Celery consumer for exact assessment-result delivery receipts."""

from __future__ import annotations

from typing import Any

from ..services.assessment_result_delivery_executor import (
    run_assessment_result_delivery_task,
)
from .celery_app import celery_app


@celery_app.task(
    bind=True,
    max_retries=0,
    name="app.tasks.assessment_tasks.post_results_to_workable",
)
def post_results_to_workable(
    self,
    access_token: str | None = None,
    subdomain: str | None = None,
    candidate_id: str | None = None,
    assessment_data: dict[str, Any] | None = None,
    member_id: str | None = None,
    request_id: str | None = None,
    assessment_id: int | None = None,
    organization_id: int | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Deliver an exact receipt; legacy secret-bearing messages bind safely."""

    return run_assessment_result_delivery_task(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=operation_id,
        access_token=access_token,
        subdomain=subdomain,
        candidate_id=candidate_id,
        assessment_data=assessment_data,
        member_id=member_id,
        request_id=request_id or self.request.id,
    )


__all__ = ["post_results_to_workable"]
