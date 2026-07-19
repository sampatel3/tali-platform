"""Hard admission for one model-graded rubric dimension."""

from __future__ import annotations

import uuid
from typing import Any

from ...platform.database import SessionLocal
from ...services.pricing_service import Feature
from ...services.provider_request_identity import provider_request_sha256
from ...services.usage_credit_reservations import CreditReservation, reserve_credits


def reserve_rubric_call(
    *,
    organization_id: int,
    assessment_id: int | None,
    role_id: int | None,
    trace_id: str,
    dimension_id: str,
    model: str,
    provider_request: dict[str, Any],
) -> CreditReservation:
    dimension_trace = f"{trace_id}:{dimension_id}"
    with SessionLocal() as meter_db:
        reservation = reserve_credits(
            meter_db,
            organization_id=organization_id,
            feature=Feature.ASSESSMENT,
            external_ref=f"usage-hold:{dimension_trace}:{uuid.uuid4().hex}",
            metadata={
                "sub_feature": "rubric_scoring",
                "dimension": dimension_id,
                "assessment_id": assessment_id,
                "trace_id": dimension_trace,
            },
            role_id=role_id,
            entity_id=(
                f"assessment:{assessment_id}" if assessment_id is not None else None
            ),
            provider="anthropic",
            model=model,
            request_sha256=provider_request_sha256(provider_request),
            enforce_role_budget=role_id is not None,
        )
        meter_db.commit()
        return reservation


__all__ = ["reserve_rubric_call"]
