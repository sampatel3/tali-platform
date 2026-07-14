"""Crash recovery for ledger-backed provider-call credit holds.

A worker can die after committing a hold but before the provider wrapper
settles or releases it.  Holds are intentionally conservative, but must not
strand org balance and role capacity forever.  This module reaps only holds
older than a long provider-call safety window; settlement refs make every
release idempotent and serialize safely with a late provider result.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, aliased

from ..models.billing_credit_ledger import BillingCreditLedger
from .provider_usage_admission import (
    PROVIDER_ATTEMPT_STARTED_STATE,
    PROVIDER_SUCCEEDED_PENDING_STATE,
    PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
)
from .usage_credit_reservations import (
    CreditReservation,
    release_credit_reservation,
    settle_credit_reservation,
)
from .usage_metering_service import record_event


logger = logging.getLogger("taali.usage_credit_reservation_recovery")


DEFAULT_STALE_RESERVATION_MINUTES = 120
DEFAULT_STALE_RESERVATION_BATCH_SIZE = 500


def _optional_int(value) -> int | None:
    return int(value) if value is not None else None


def _reconcile_deferred_usage_event(
    db: Session,
    *,
    reservation: CreditReservation,
    payload: dict,
):
    """Rebuild one trusted internal UsageEvent receipt and settle its hold."""
    organization_id = int(payload["organization_id"])
    feature = str(payload["feature"])
    if organization_id != int(reservation.organization_id):
        raise ValueError("deferred usage organization does not match reservation")
    if feature != str(reservation.feature):
        raise ValueError("deferred usage feature does not match reservation")
    metadata = payload.get("metadata")
    with db.begin_nested():
        event = record_event(
            db,
            organization_id=organization_id,
            feature=feature,
            model=str(payload["model"]),
            input_tokens=max(int(payload.get("input_tokens") or 0), 0),
            output_tokens=max(int(payload.get("output_tokens") or 0), 0),
            cache_read_tokens=max(int(payload.get("cache_read_tokens") or 0), 0),
            cache_creation_tokens=max(
                int(payload.get("cache_creation_tokens") or 0), 0
            ),
            cache_creation_1h_tokens=_optional_int(
                payload.get("cache_creation_1h_tokens")
            ),
            cache_hit=bool(payload.get("cache_hit", False)),
            service_tier=str(payload.get("service_tier") or "standard"),
            user_id=_optional_int(payload.get("user_id")),
            role_id=_optional_int(payload.get("role_id")),
            entity_id=(
                str(payload["entity_id"])
                if payload.get("entity_id") is not None
                else None
            ),
            provider_cost_usd_micro=_optional_int(
                payload.get("provider_cost_usd_micro")
            ),
            metadata={
                **(dict(metadata) if isinstance(metadata, dict) else {}),
                "deferred_metering_recovery": True,
            },
            credit_reservation=reservation.as_metering_payload(),
        )
        # ``settle_credit_reservation`` may have added its ledger row without
        # an immediate flush. Materialize that pending row before deciding a
        # shadow-mode fallback settlement is needed, otherwise we can enqueue
        # the same unique ``:settled`` ref twice in one unit of work.
        db.flush()
        settlement_ref = f"{reservation.external_ref}:settled"
        settled = (
            db.query(BillingCreditLedger.id)
            .filter(BillingCreditLedger.external_ref == settlement_ref)
            .first()
        )
        if settled is None:
            # A live hold can outlast an operational switch to shadow mode.
            # Settle it explicitly so recovery never creates an event while
            # leaving the original hold to be refunded on the next sweep.
            settle_credit_reservation(
                db,
                organization_id=organization_id,
                event=event,
                reservation=reservation,
            )
        db.flush()
        return event


def release_stale_credit_reservations(
    db: Session,
    *,
    stale_after_minutes: int = DEFAULT_STALE_RESERVATION_MINUTES,
    limit: int = DEFAULT_STALE_RESERVATION_BATCH_SIZE,
    now: datetime | None = None,
) -> dict:
    """Release abandoned holds older than the provider safety window.

    All ``reservation:*`` reasons are included deliberately. Holds explicitly
    marked provider-succeeded are reconciled from their durable usage receipt,
    or retained when usage is unknown / metering is still unavailable. Only a
    hold with no evidence that a billable response existed is refundable.
    """
    effective_now = now or datetime.now(timezone.utc)
    cutoff = effective_now - timedelta(
        minutes=max(int(stale_after_minutes), 1)
    )
    batch_limit = max(min(int(limit), 5_000), 1)
    settlement = aliased(BillingCreditLedger)
    holds = (
        db.query(BillingCreditLedger)
        .outerjoin(
            settlement,
            settlement.external_ref
            == (BillingCreditLedger.external_ref + ":settled"),
        )
        .filter(
            BillingCreditLedger.reason.like("reservation:%"),
            BillingCreditLedger.created_at <= cutoff,
            BillingCreditLedger.external_ref.isnot(None),
            settlement.id.is_(None),
        )
        .order_by(BillingCreditLedger.created_at.asc(), BillingCreditLedger.id.asc())
        .limit(batch_limit)
        # The settlement anti-join has a nullable side, which PostgreSQL will
        # not lock.  Lease only the base hold rows selected for recovery.
        .with_for_update(of=BillingCreditLedger, skip_locked=True)
        .all()
    )
    # Concurrent Beat redeliveries can lease disjoint hold rows for the same
    # organizations. Acquire the downstream Organization locks in one global
    # order so two recovery batches cannot form an A->B / B->A deadlock.
    holds.sort(key=lambda hold: (int(hold.organization_id), int(hold.id)))

    released = 0
    released_credits = 0
    already_settled = 0
    reconciled = 0
    reconciled_credits = 0
    protected_billable = 0
    by_sub_feature: Counter[str] = Counter()
    for hold in holds:
        metadata = hold.entry_metadata if isinstance(hold.entry_metadata, dict) else {}
        sub_feature = str(metadata.get("sub_feature") or "unknown")
        feature = str(hold.reason).split(":", 1)[-1] or "other"
        amount = max(-int(hold.delta or 0), 0)
        reservation = CreditReservation(
            organization_id=int(hold.organization_id),
            feature=feature,
            amount=amount,
            external_ref=str(hold.external_ref),
            live=True,
        )
        state = str(metadata.get("state") or "")
        if state in {
            PROVIDER_ATTEMPT_STARTED_STATE,
            PROVIDER_SUCCEEDED_PENDING_STATE,
            PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
        }:
            deferred = metadata.get("deferred_usage_event")
            if state == PROVIDER_SUCCEEDED_PENDING_STATE and isinstance(
                deferred, dict
            ):
                try:
                    event = _reconcile_deferred_usage_event(
                        db,
                        reservation=reservation,
                        payload=deferred,
                    )
                except Exception:
                    # A nested transaction contains the failed reconstruction,
                    # so the sweep can continue and retry this receipt on the
                    # next health tick. Never refund known billable work.
                    logger.exception(
                        "deferred provider usage reconciliation failed ref=%s",
                        reservation.external_ref,
                    )
                    protected_billable += 1
                else:
                    reconciled += 1
                    reconciled_credits += int(event.credits_charged or 0)
                continue
            protected_billable += 1
            continue
        refunded = release_credit_reservation(
            db,
            reservation=reservation,
            reason=(
                "stale_provider_hold_reaper:"
                f"older_than_{max(int(stale_after_minutes), 1)}m"
            ),
        )
        if refunded > 0:
            released += 1
            released_credits += int(refunded)
            by_sub_feature[sub_feature] += 1
        else:
            already_settled += 1

    return {
        "scanned": len(holds),
        "released": released,
        "released_credits": released_credits,
        "already_settled": already_settled,
        "reconciled": reconciled,
        "reconciled_credits": reconciled_credits,
        "protected_billable": protected_billable,
        "by_sub_feature": dict(sorted(by_sub_feature.items())),
        "stale_after_minutes": max(int(stale_after_minutes), 1),
    }


__all__ = [
    "DEFAULT_STALE_RESERVATION_BATCH_SIZE",
    "DEFAULT_STALE_RESERVATION_MINUTES",
    "release_stale_credit_reservations",
]
