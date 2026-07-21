"""Concurrency-safe usage admission for paid pre-screen calls and cache fees."""

from __future__ import annotations

import uuid
from typing import Any, Callable

from ..platform.database import SessionLocal
from .pricing_service import Feature
from .provider_usage_admission import reserve_provider_usage
from .usage_credit_reservations import (
    CreditReservation,
    release_credit_reservation,
)


def reserve_pre_screen_usage(
    metering_context: dict[str, Any] | None,
    *,
    trace_id: str,
) -> CreditReservation | None:
    """Hold one pre-screen charge when org and role attribution are known."""
    context = metering_context if isinstance(metering_context, dict) else {}
    organization_id = context.get("organization_id")
    role_id = context.get("role_id")
    if organization_id is None or role_id is None:
        return None
    return reserve_provider_usage(
        organization_id=int(organization_id),
        role_id=int(role_id),
        feature=Feature.PRESCREEN,
        trace_id=str(trace_id or uuid.uuid4().hex),
        entity_id=(
            str(context["entity_id"])
            if context.get("entity_id") is not None
            else None
        ),
        sub_feature="pre_screen",
        metadata={"admission_source": "pre_screen"},
        require_role_authority=bool(
            context.get("require_role_authority", False)
        ),
    )


def release_pre_screen_usage(
    reservation: CreditReservation | None,
    *,
    reason: str,
) -> None:
    """Best-effort idempotent compensation before trustworthy usage exists."""
    if reservation is None:
        return
    with SessionLocal() as meter_db:
        release_credit_reservation(
            meter_db,
            reservation=reservation,
            reason=reason,
        )
        meter_db.commit()


def run_with_pre_screen_admission(
    call: Callable[[dict[str, Any] | None], Any],
    *,
    metering_context: dict[str, Any] | None,
    trace_id: str,
) -> tuple[Any, CreditReservation | None]:
    """Reserve before cache/provider work and thread the hold into metering.

    ``execute_pre_screen_only`` bills cache hits, so it reserves before the
    runner's cache lookup and later settles the same hold through record_event.
    Direct runner callers reserve only after a cache miss at the provider edge.
    """
    reservation = reserve_pre_screen_usage(
        metering_context,
        trace_id=trace_id,
    )
    admitted_context = (
        dict(metering_context) if isinstance(metering_context, dict) else None
    )
    if reservation is not None and admitted_context is not None:
        admitted_context["credit_reservation"] = (
            reservation.as_metering_payload()
        )
    try:
        result = call(admitted_context)
    except Exception:
        release_pre_screen_usage(reservation, reason="pre_screen_call_failed")
        raise
    if (
        reservation is not None
        and not bool(getattr(result, "cache_hit", False))
        and str(getattr(result, "decision", "")) == "error"
    ):
        # Wrapper success/error settlement wins; this is the fallback for a
        # pre-provider client/setup error and is intentionally idempotent.
        release_pre_screen_usage(
            reservation,
            reason="pre_screen_no_billable_result",
        )
    return result, reservation


__all__ = [
    "release_pre_screen_usage",
    "reserve_pre_screen_usage",
    "run_with_pre_screen_admission",
]
