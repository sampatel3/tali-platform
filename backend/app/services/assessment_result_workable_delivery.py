"""Compatibility facade for durable Workable assessment-result delivery."""

from __future__ import annotations

from ..components.assessments.result_delivery_contracts import (
    DELIVERY_CONFIRMED,
    DELIVERY_DISPATCHING,
    DELIVERY_PENDING,
    DELIVERY_PROVIDER_STARTED,
    DELIVERY_RECONCILIATION_REQUIRED,
    DELIVERY_RETRY_WAIT,
    AssessmentResultDispatch,
)
from ..components.assessments.result_delivery_outbox import (
    authorize_assessment_result_delivery,
    enqueue_assessment_result_delivery,
    publish_assessment_result_delivery,
    sweep_assessment_result_deliveries,
)
from .assessment_result_delivery_executor import (
    deliver_assessment_result,
    run_assessment_result_delivery_task,
)

__all__ = [
    "AssessmentResultDispatch",
    "DELIVERY_CONFIRMED",
    "DELIVERY_DISPATCHING",
    "DELIVERY_PENDING",
    "DELIVERY_PROVIDER_STARTED",
    "DELIVERY_RECONCILIATION_REQUIRED",
    "DELIVERY_RETRY_WAIT",
    "authorize_assessment_result_delivery",
    "deliver_assessment_result",
    "enqueue_assessment_result_delivery",
    "publish_assessment_result_delivery",
    "run_assessment_result_delivery_task",
    "sweep_assessment_result_deliveries",
]
