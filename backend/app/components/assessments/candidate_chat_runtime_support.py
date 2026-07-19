"""Small, non-provider helpers for the candidate-chat runtime."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ...services.pricing_service import Feature
from ...services.usage_metering_service import InsufficientCreditsError
from .interrogation import derive_interrogation_state

_CLASSIFIER_ERROR_CODES = frozenset(
    {
        "interrogation_classifier_budget_blocked",
        "interrogation_classifier_failed",
        "interrogation_classifier_output_invalid",
        "interrogation_classifier_unconfigured",
    }
)


def reserve_paid_call(db: Session, hooks: Any, organization_id: int) -> None:
    try:
        hooks.reserve(
            db,
            organization_id=int(organization_id),
            feature=Feature.ASSESSMENT,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": "This assessment's AI credit balance has been reached. You can keep working and submit when you're ready."
            },
        ) from exc
    finally:
        db.rollback()


def interrogation_inputs(prepared: Any) -> tuple[list[dict], dict[str, str]]:
    extra = (
        prepared.task.extra_data
        if isinstance(prepared.task.extra_data, dict)
        else {}
    )
    raw_points = extra.get("decision_points")
    points = (
        [item for item in raw_points if isinstance(item, dict)]
        if isinstance(raw_points, list)
        else []
    )
    return points, derive_interrogation_state(
        points,
        prepared.assessment.prompts,
    )


def classifier_error_code(value: object) -> str:
    """Allow controlled classifier outcomes, never injected/provider text."""

    code = str(value or "").strip()
    return code if code in _CLASSIFIER_ERROR_CODES else "interrogation_classifier_failed"


__all__ = [
    "classifier_error_code",
    "interrogation_inputs",
    "reserve_paid_call",
]
