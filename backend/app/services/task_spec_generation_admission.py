"""Durable credit admission for one task-spec provider attempt."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ..platform.database import SessionLocal
from .pricing_service import Feature, credits_charged, raw_cost_usd_micro
from .provider_error_evidence import safe_provider_error_code
from .provider_request_identity import provider_request_sha256
from .usage_credit_reservations import (
    CreditReservation,
    release_credit_reservation,
    reserve_credits,
)

logger = logging.getLogger("taali.task_spec_generator")

# The system contract, JD, and prior invalid attempts can produce a large input.
# Reserving against this ceiling keeps each provider attempt fail-closed.
_RESERVATION_INPUT_TOKENS = 60_000


def _reservation_amount(*, model: str, max_output_tokens: int) -> int:
    raw = raw_cost_usd_micro(
        input_tokens=_RESERVATION_INPUT_TOKENS,
        output_tokens=max_output_tokens,
        model=model,
    )
    return credits_charged(
        feature=Feature.ASSESSMENT,
        cost_usd_micro=raw,
        cache_hit=False,
    )


def reserve_generation_attempt(
    *,
    metering: dict[str, Any],
    model: str,
    attempt: int,
    provider_request: dict[str, Any],
    max_output_tokens: int,
) -> CreditReservation:
    """Land an attribution-bound hold before one Anthropic request."""
    trace_id = str(metering["trace_id"])
    external_ref = (
        f"usage-reservation:task-spec:{trace_id}:attempt:{attempt}:"
        f"{uuid.uuid4().hex[:12]}"
    )
    with SessionLocal() as meter_db:
        reservation = reserve_credits(
            meter_db,
            organization_id=int(metering["organization_id"]),
            feature=Feature.ASSESSMENT,
            external_ref=external_ref,
            amount=_reservation_amount(
                model=model,
                max_output_tokens=max_output_tokens,
            ),
            metadata={
                "sub_feature": "task_spec_generation",
                "role_id": metering.get("role_id"),
                "entity_id": metering.get("entity_id"),
                "trace_id": trace_id,
                "attempt": int(attempt),
            },
            role_id=(
                int(metering["role_id"])
                if metering.get("role_id") is not None
                else None
            ),
            user_id=metering.get("user_id"),
            entity_id=metering.get("entity_id"),
            candidate_id=metering.get("candidate_id"),
            provider="anthropic",
            model=model,
            request_sha256=provider_request_sha256(provider_request),
            enforce_role_budget=metering.get("role_id") is not None,
        )
        meter_db.commit()
        return reservation


def release_generation_attempt(
    reservation: CreditReservation, *, reason: str
) -> None:
    """Best-effort compensation when an attempt returns no model usage."""
    try:
        with SessionLocal() as meter_db:
            release_credit_reservation(
                meter_db,
                reservation=reservation,
                reason=reason,
            )
            meter_db.commit()
    except Exception as exc:
        # A durable hold is safer than an optimistic refund: recovery can
        # reconcile this trace without risking a double credit.
        logger.warning(
            "task_spec failed to release credit reservation ref=%s error_code=%s",
            reservation.external_ref,
            safe_provider_error_code(
                exc,
                operation="credit_reservation_release_failed",
            ),
        )


__all__ = ["release_generation_attempt", "reserve_generation_attempt"]
