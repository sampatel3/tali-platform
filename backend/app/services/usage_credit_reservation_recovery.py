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

from ..models.ai_routing import AIRoutingAttempt
from ..models.billing_credit_ledger import BillingCreditLedger
from ..models.usage_event import UsageEvent
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


def load_credit_reservation(
    db: Session,
    *,
    external_ref: str | None,
    for_update: bool = True,
) -> CreditReservation | None:
    """Resolve a durable hold identity without trusting caller payloads."""

    normalized_ref = str(external_ref or "").strip()
    if not normalized_ref:
        return None
    query = db.query(BillingCreditLedger).filter(
        BillingCreditLedger.external_ref == normalized_ref,
        BillingCreditLedger.reason.like("reservation:%"),
    )
    if for_update:
        query = query.with_for_update()
    hold = query.one_or_none()
    if hold is None:
        return None
    feature = str(hold.reason).split(":", 1)[-1]
    if not feature:
        return None
    return CreditReservation(
        organization_id=int(hold.organization_id),
        feature=feature,
        amount=max(-int(hold.delta or 0), 0),
        external_ref=normalized_ref,
        live=True,
    )


def _settlement_row(
    db: Session, reservation: CreditReservation
) -> BillingCreditLedger | None:
    return (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref == f"{reservation.external_ref}:settled"
        )
        .one_or_none()
    )


def _validate_existing_event(
    event: UsageEvent,
    *,
    reservation: CreditReservation,
    payload: dict,
) -> None:
    expected = {
        "organization_id": int(reservation.organization_id),
        "feature": str(reservation.feature),
        "model": str(payload["model"]),
        "input_tokens": max(int(payload.get("input_tokens") or 0), 0),
        "output_tokens": max(int(payload.get("output_tokens") or 0), 0),
        "cache_read_tokens": max(int(payload.get("cache_read_tokens") or 0), 0),
        "cache_creation_tokens": max(int(payload.get("cache_creation_tokens") or 0), 0),
        "cache_hit": 1 if bool(payload.get("cache_hit", False)) else 0,
        "user_id": _optional_int(payload.get("user_id")),
        "role_id": _optional_int(payload.get("role_id")),
        "entity_id": (
            str(payload["entity_id"]) if payload.get("entity_id") is not None else None
        ),
    }
    if payload.get("provider_cost_usd_micro") is not None:
        expected["cost_usd_micro"] = max(int(payload["provider_cost_usd_micro"]), 0)
    if payload.get("cache_creation_1h_tokens") is not None:
        expected["cache_creation_1h_tokens"] = max(
            int(payload["cache_creation_1h_tokens"]), 0
        )
    for field, value in expected.items():
        if getattr(event, field) != value:
            raise ValueError(
                f"existing usage event {event.id} disagrees with provider "
                f"receipt field {field}"
            )


def reconcile_usage_event_receipt(
    db: Session,
    *,
    reservation: CreditReservation,
    payload: dict,
    existing_event_id: int | None = None,
) -> UsageEvent:
    """Idempotently materialize one trusted receipt and settle its hold.

    The reservation settlement row is the idempotency anchor. A recovery retry
    reuses its event instead of inserting duplicate customer-visible usage.
    """

    organization_id = int(payload["organization_id"])
    feature = str(payload["feature"])
    if organization_id != int(reservation.organization_id):
        raise ValueError("deferred usage organization does not match reservation")
    if feature != str(reservation.feature):
        raise ValueError("deferred usage feature does not match reservation")

    settlement = _settlement_row(db, reservation)
    event: UsageEvent | None = None
    if existing_event_id is not None:
        event = db.get(UsageEvent, int(existing_event_id))
        if event is None:
            raise ValueError("provider evidence references a missing usage event")
    elif settlement is not None and not str(settlement.reason).startswith(
        "reservation_release:"
    ):
        settlement_metadata = (
            settlement.entry_metadata
            if isinstance(settlement.entry_metadata, dict)
            else {}
        )
        settled_event_id = settlement_metadata.get("event_id")
        if settled_event_id is None:
            raise ValueError("settled reservation is missing its usage event identity")
        event = db.get(UsageEvent, int(settled_event_id))
        if event is None:
            raise ValueError("settled reservation references a missing usage event")

    if event is not None:
        _validate_existing_event(
            event,
            reservation=reservation,
            payload=payload,
        )
        if settlement is None or str(settlement.reason).startswith(
            "reservation_release:"
        ):
            settle_credit_reservation(
                db,
                organization_id=organization_id,
                event=event,
                reservation=reservation,
            )
            db.flush()
        return event

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
        current_settlement = _settlement_row(db, reservation)
        if current_settlement is None or (
            settlement is not None
            and str(settlement.reason).startswith("reservation_release:")
        ):
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


def _reconcile_deferred_usage_event(
    db: Session,
    *,
    reservation: CreditReservation,
    payload: dict,
) -> UsageEvent:
    """Backward-compatible private entry point for stale-hold recovery."""

    return reconcile_usage_event_receipt(
        db,
        reservation=reservation,
        payload=payload,
    )


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
    cutoff = effective_now - timedelta(minutes=max(int(stale_after_minutes), 1))
    batch_limit = max(min(int(limit), 5_000), 1)
    settlement = aliased(BillingCreditLedger)
    holds = (
        db.query(BillingCreditLedger)
        .outerjoin(
            settlement,
            settlement.external_ref == (BillingCreditLedger.external_ref + ":settled"),
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
        routed_attempt = (
            db.query(AIRoutingAttempt)
            .filter(AIRoutingAttempt.credit_reservation_ref == reservation.external_ref)
            .one_or_none()
        )
        if routed_attempt is not None and routed_attempt.status == "failed":
            # A routed physical attempt reaches ``failed`` only with proof that
            # provider work was non-billable (an adapter-side pre-call failure
            # or an explicit rejection). Its immediate best-effort refund can
            # lose a race with a transient database outage; use the durable
            # attempt/reservation link to retry that refund instead of
            # protecting the started hold forever.
            refunded = release_credit_reservation(
                db,
                reservation=reservation,
                reason=(
                    "stale_routed_nonbillable_attempt_reaper:"
                    f"older_than_{max(int(stale_after_minutes), 1)}m"
                ),
                allow_started=True,
            )
            if refunded > 0:
                released += 1
                released_credits += int(refunded)
                by_sub_feature[sub_feature] += 1
            else:
                already_settled += 1
            continue
        if state in {
            PROVIDER_ATTEMPT_STARTED_STATE,
            PROVIDER_SUCCEEDED_PENDING_STATE,
            PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
        }:
            deferred = metadata.get("deferred_usage_event")
            if state == PROVIDER_SUCCEEDED_PENDING_STATE and isinstance(deferred, dict):
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
    "load_credit_reservation",
    "reconcile_usage_event_receipt",
    "release_stale_credit_reservations",
]
