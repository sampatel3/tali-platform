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

from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session, aliased

from ..models.billing_credit_ledger import BillingCreditLedger
from .provider_usage_admission import (
    PROVIDER_ATTEMPT_STARTED_STATE,
    PROVIDER_SUCCEEDED_PENDING_STATE,
    PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
)
from .credit_reservation_identity import reservation_from_ledger_hold
from .usage_credit_reservations import (
    CreditReservation,
    release_credit_reservation,
    settle_credit_reservation,
)
from .usage_metering_service import record_event


logger = logging.getLogger("taali.usage_credit_reservation_recovery")


DEFAULT_STALE_RESERVATION_MINUTES = 120
DEFAULT_STALE_RESERVATION_BATCH_SIZE = 500
_RECOVERY_EVIDENCE_KEY = "_stale_recovery"
_INVALID_RESERVATION_IDENTITY_STATE = "invalid_reservation_identity"
_INVALID_DEFERRED_USAGE_STATE = "invalid_deferred_usage"
_AUTOMATIC_RECOVERY_QUARANTINE_STATES = (
    _INVALID_RESERVATION_IDENTITY_STATE,
    _INVALID_DEFERRED_USAGE_STATE,
)
def _optional_int(value) -> int | None:
    return int(value) if value is not None else None


def _quarantine_recovery_row(
    hold: BillingCreditLedger,
    *,
    state: str,
) -> bool:
    """Preserve malformed evidence while removing it from automatic pages."""

    metadata = hold.entry_metadata
    evidence = {
        "version": 1,
        "state": state,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if isinstance(metadata, dict):
        updated = dict(metadata)
    else:
        evidence["original_entry_metadata"] = metadata
        updated = {}
    updated[_RECOVERY_EVIDENCE_KEY] = evidence
    hold.entry_metadata = updated
    return True


def _restore_historical_held_state(
    hold: BillingCreditLedger,
    *,
    reservation: CreditReservation,
) -> bool:
    """Normalize an exact pre-state v1 hold without guessing malformed rows."""

    metadata = hold.entry_metadata
    if (
        reservation.version != 1
        or not isinstance(metadata, dict)
        or metadata.get("state") not in (None, "")
        or metadata.get("feature") != reservation.feature
        or type(metadata.get("reserved")) is not int
        or metadata.get("reserved") != reservation.amount
        or metadata.get("role_id") != reservation.role_id
        or any(
            key in metadata
            for key in (
                "deferred_usage_event",
                "provider_attempt_started_at",
                "provider_request_id",
                "provider_succeeded_at",
            )
        )
    ):
        return False
    hold.entry_metadata = {**metadata, "state": "held"}
    return True


def _reconcile_deferred_usage_event(
    db: Session,
    *,
    reservation: CreditReservation,
    payload: dict,
):
    """Rebuild one trusted internal UsageEvent receipt and settle its hold."""
    raw_organization_id = payload.get("organization_id")
    raw_feature = payload.get("feature")
    if type(raw_organization_id) is not int or raw_organization_id <= 0:
        raise ValueError("deferred usage organization identity is malformed")
    if type(raw_feature) is not str or not raw_feature:
        raise ValueError("deferred usage feature identity is malformed")
    organization_id = raw_organization_id
    feature = raw_feature
    if organization_id != int(reservation.organization_id):
        raise ValueError("deferred usage organization does not match reservation")
    if feature != str(reservation.feature):
        raise ValueError("deferred usage feature does not match reservation")
    payload_role_id = payload.get("role_id")
    if payload_role_id is not None and (
        type(payload_role_id) is not int or payload_role_id <= 0
    ):
        raise ValueError("deferred usage role identity is malformed")
    if payload_role_id != reservation.role_id:
        raise ValueError("deferred usage role does not match reservation")
    if reservation.version == 2:
        expected_identity = {
            "user_id": reservation.user_id,
            "role_id": reservation.role_id,
            "entity_id": reservation.entity_id,
            "candidate_id": reservation.candidate_id,
            "provider": reservation.provider,
            "model": reservation.model,
            "request_sha256": reservation.request_sha256,
        }
        if not set(expected_identity).issubset(payload):
            raise ValueError("deferred usage attribution is incomplete")
        for field, expected in expected_identity.items():
            actual = payload[field]
            if type(actual) is not type(expected) or actual != expected:
                raise ValueError(
                    f"deferred usage {field} does not match reservation"
                )
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
                **(
                    {
                        "candidate_id": reservation.candidate_id,
                        "provider": reservation.provider,
                        "request_sha256": reservation.request_sha256,
                    }
                    if reservation.version == 2
                    else {}
                ),
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
    provider_state = BillingCreditLedger.entry_metadata["state"].as_string()
    deferred_usage = BillingCreditLedger.entry_metadata[
        "deferred_usage_event"
    ].as_string()
    recovery_state = BillingCreditLedger.entry_metadata[
        _RECOVERY_EVIDENCE_KEY
    ]["state"].as_string()
    permanently_protected = or_(
        provider_state.in_(
            (
                PROVIDER_ATTEMPT_STARTED_STATE,
                PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
            )
        ),
        and_(
            provider_state == PROVIDER_SUCCEEDED_PENDING_STATE,
            deferred_usage.is_(None),
        ),
    )
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
            or_(
                recovery_state.is_(None),
                recovery_state.notin_(_AUTOMATIC_RECOVERY_QUARANTINE_STATES),
            ),
        )
        # Rows with a local, automatically resolvable state must not sit behind
        # an arbitrary number of intentionally retained ambiguous attempts.
        # The latter remain untouched and visible when capacity remains.
        .order_by(
            case(
                (permanently_protected, 1),
                else_=0,
            ),
            BillingCreditLedger.created_at.asc(),
            BillingCreditLedger.id.asc(),
        )
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
        if not isinstance(hold.entry_metadata, dict):
            logger.error(
                "stale reservation has malformed metadata ref=%s",
                hold.external_ref,
            )
            _quarantine_recovery_row(
                hold,
                state=_INVALID_RESERVATION_IDENTITY_STATE,
            )
            protected_billable += 1
            continue
        metadata = hold.entry_metadata
        sub_feature = str(metadata.get("sub_feature") or "unknown")
        feature = str(hold.reason).split(":", 1)[-1] or "other"
        amount = max(-int(hold.delta or 0), 0)
        try:
            reservation = reservation_from_ledger_hold(
                hold,
                feature=feature,
                amount=amount,
            )
        except ValueError:
            # An inexact identity must never be interpreted as an org-only
            # reservation and refunded. Preserve the hold and quarantine only
            # its automatic-recovery eligibility for operator repair.
            logger.error(
                "stale reservation has malformed identity metadata ref=%s",
                hold.external_ref,
            )
            _quarantine_recovery_row(
                hold,
                state=_INVALID_RESERVATION_IDENTITY_STATE,
            )
            protected_billable += 1
            continue
        state = str(metadata.get("state") or "")
        if not state:
            if not _restore_historical_held_state(
                hold,
                reservation=reservation,
            ):
                _quarantine_recovery_row(
                    hold,
                    state=_INVALID_RESERVATION_IDENTITY_STATE,
                )
                protected_billable += 1
                continue
            metadata = hold.entry_metadata
            state = "held"
        known_states = {
            "held",
            PROVIDER_ATTEMPT_STARTED_STATE,
            PROVIDER_SUCCEEDED_PENDING_STATE,
            PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
        }
        if state not in known_states:
            _quarantine_recovery_row(
                hold,
                state=_INVALID_RESERVATION_IDENTITY_STATE,
            )
            protected_billable += 1
            continue
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
                except (KeyError, TypeError, ValueError):
                    logger.error(
                        "deferred provider usage receipt is malformed ref=%s",
                        reservation.external_ref,
                    )
                    _quarantine_recovery_row(
                        hold,
                        state=_INVALID_DEFERRED_USAGE_STATE,
                    )
                    protected_billable += 1
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
            if state == PROVIDER_SUCCEEDED_PENDING_STATE:
                _quarantine_recovery_row(
                    hold,
                    state=_INVALID_DEFERRED_USAGE_STATE,
                )
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
