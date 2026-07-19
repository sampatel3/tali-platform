"""Exact ledger release and settlement for provider credit holds."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..models.billing_credit_ledger import BillingCreditLedger
from ..models.organization import Organization
from ..models.usage_event import UsageEvent
from .credit_reservation_contract import CreditReservation, reservation_from_payload

logger = logging.getLogger("taali.usage_credit_reservations")


def _same_exact_scalar(actual: Any, expected: Any) -> bool:
    return type(actual) is type(expected) and actual == expected


def _event_matches_v2_identity(
    event: UsageEvent,
    reservation: CreditReservation,
) -> bool:
    metadata = event.event_metadata if isinstance(event.event_metadata, dict) else {}
    if not {"candidate_id", "provider", "request_sha256"}.issubset(metadata):
        return False
    return bool(
        _same_exact_scalar(event.role_id, reservation.role_id)
        and _same_exact_scalar(event.user_id, reservation.user_id)
        and _same_exact_scalar(event.entity_id, reservation.entity_id)
        and _same_exact_scalar(event.model, reservation.model)
        and _same_exact_scalar(
            metadata["candidate_id"], reservation.candidate_id
        )
        and _same_exact_scalar(metadata["provider"], reservation.provider)
        and _same_exact_scalar(
            metadata["request_sha256"], reservation.request_sha256
        )
    )


def hold_matches_reservation(
    hold: BillingCreditLedger,
    reservation: CreditReservation,
    *,
    allowed_states: set[str],
) -> bool:
    """Verify the immutable ledger contract before consuming a supplied ref."""

    metadata = hold.entry_metadata if isinstance(hold.entry_metadata, dict) else {}
    held_role_id = metadata.get("role_id")
    if held_role_id is not None and type(held_role_id) is not int:
        return False
    if reservation.version == 2:
        v2_fields = {
            "reservation_version",
            "reservation_user_id",
            "reservation_entity_id",
            "reservation_candidate_id",
            "reservation_provider",
            "reservation_model",
            "reservation_request_sha256",
        }
        if not v2_fields.issubset(metadata):
            return False
        for field in ("user_id", "candidate_id"):
            value = metadata.get(f"reservation_{field}")
            if value is not None and (type(value) is not int or value <= 0):
                return False
        for field in ("entity_id", "provider", "model", "request_sha256"):
            value = metadata.get(f"reservation_{field}")
            if value is not None and (type(value) is not str or not value):
                return False
        identity_matches = bool(
            metadata.get("reservation_version") == 2
            and metadata.get("reservation_user_id") == reservation.user_id
            and metadata.get("reservation_entity_id") == reservation.entity_id
            and metadata.get("reservation_candidate_id") == reservation.candidate_id
            and metadata.get("reservation_provider") == reservation.provider
            and metadata.get("reservation_model") == reservation.model
            and metadata.get("reservation_request_sha256")
            == reservation.request_sha256
        )
    else:
        # Historical v1 holds remain settleable/releasable, but a v1 payload
        # can never consume a v2 hold or authorize a new provider attempt.
        identity_matches = metadata.get("reservation_version") in (None, 1)
    return bool(
        int(hold.organization_id) == reservation.organization_id
        and str(hold.external_ref) == reservation.external_ref
        and str(hold.reason) == f"reservation:{reservation.feature}"
        and int(hold.delta) == -reservation.amount
        and metadata.get("feature") == reservation.feature
        and type(metadata.get("reserved")) is int
        and metadata.get("reserved") == reservation.amount
        and held_role_id == reservation.role_id
        and identity_matches
        and metadata.get("state") in allowed_states
    )


def release_credit_reservation(
    db: Session,
    *,
    reservation: CreditReservation | dict[str, Any],
    reason: str = "provider_call_failed",
    allow_started: bool = False,
) -> int:
    """Idempotently refund an exact live hold when no billable result exists."""

    parsed = reservation_from_payload(reservation)
    if parsed is None or not parsed.live:
        return 0
    settlement_ref = f"{parsed.external_ref}:settled"
    org = (
        db.query(Organization)
        .filter(Organization.id == parsed.organization_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if org is None:
        return 0
    held_row = (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref == parsed.external_ref,
            BillingCreditLedger.organization_id == parsed.organization_id,
        )
        .one_or_none()
    )
    allowed_states = {"held"}
    if allow_started:
        allowed_states.add("provider_attempt_started")
    if held_row is None or not hold_matches_reservation(
        held_row,
        parsed,
        allowed_states=allowed_states,
    ):
        return 0
    if (
        db.query(BillingCreditLedger.id)
        .filter(
            BillingCreditLedger.organization_id == parsed.organization_id,
            BillingCreditLedger.external_ref == settlement_ref,
        )
        .first()
        is not None
    ):
        return 0
    held = parsed.amount
    new_balance = int(org.credits_balance or 0) + held
    org.credits_balance = new_balance
    db.add(
        BillingCreditLedger(
            organization_id=parsed.organization_id,
            delta=held,
            balance_after=new_balance,
            reason=f"reservation_release:{parsed.feature}",
            external_ref=settlement_ref,
            entry_metadata={
                "reservation_ref": parsed.external_ref,
                "reserved": held,
                "state": "released",
                "release_reason": str(reason)[:200],
            },
        )
    )
    db.flush()
    return held


def _debit_without_reservation(
    db: Session,
    *,
    organization_id: int,
    event: UsageEvent,
    reservation_ref: str,
) -> None:
    from .usage_metering_service import _debit_ledger

    logger.error(
        "usage reservation contract mismatch org=%s ref=%s",
        organization_id,
        reservation_ref,
    )
    _debit_ledger(db, organization_id=organization_id, event=event)


def settle_credit_reservation(
    db: Session,
    *,
    organization_id: int,
    event: UsageEvent,
    reservation: CreditReservation,
) -> None:
    """Reconcile an exact hold to actual charge without overdrawing."""

    parsed = reservation_from_payload(reservation)
    if (
        parsed is None
        or not parsed.live
        or parsed.organization_id != int(organization_id)
        or parsed.feature != str(event.feature)
        or (parsed.version == 2 and not _event_matches_v2_identity(event, parsed))
    ):
        _debit_without_reservation(
            db,
            organization_id=organization_id,
            event=event,
            reservation_ref=str(getattr(reservation, "external_ref", "invalid")),
        )
        return
    settlement_ref = f"{parsed.external_ref}:settled"
    org = (
        db.query(Organization)
        .filter(Organization.id == int(organization_id))
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    held_row = (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref == parsed.external_ref,
            BillingCreditLedger.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    if org is None or held_row is None or not hold_matches_reservation(
        held_row,
        parsed,
        allowed_states={
            "held",
            "provider_attempt_started",
            "provider_succeeded_metering_pending",
            "provider_succeeded_usage_unknown",
        },
    ):
        _debit_without_reservation(
            db,
            organization_id=organization_id,
            event=event,
            reservation_ref=parsed.external_ref,
        )
        return
    held = parsed.amount
    charged = max(int(event.credits_charged or 0), 0)
    current = int(org.credits_balance or 0)
    settlement_row = (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.organization_id == int(organization_id),
            BillingCreditLedger.external_ref == settlement_ref,
        )
        .one_or_none()
    )
    if settlement_row is not None:
        if str(settlement_row.reason).startswith("reservation_release:"):
            late_ref = f"{parsed.external_ref}:late-settled"
            if (
                db.query(BillingCreditLedger.id)
                .filter(
                    BillingCreditLedger.organization_id == int(organization_id),
                    BillingCreditLedger.external_ref == late_ref,
                )
                .first()
                is None
            ):
                actual_debit = min(charged, max(current, 0))
                shortfall = charged - actual_debit
                new_balance = max(current - actual_debit, 0)
                org.credits_balance = new_balance
                late_meta = {
                    "reservation_ref": parsed.external_ref,
                    "reserved": held,
                    "charged": charged,
                    "adjustment": -actual_debit,
                    "shortfall": shortfall,
                    "state": "late_settled_after_release",
                }
                event.event_metadata = {
                    **dict(event.event_metadata or {}),
                    "credit_reservation": late_meta,
                }
                db.add(
                    BillingCreditLedger(
                        organization_id=int(organization_id),
                        delta=-actual_debit,
                        balance_after=new_balance,
                        reason=f"reservation_late_settle:{event.feature}",
                        external_ref=late_ref,
                        entry_metadata={**late_meta, "event_id": int(event.id)},
                    )
                )
            return

        logger.error("usage reservation duplicate settlement ignored ref=%s", settlement_ref)
        event.event_metadata = {
            **dict(event.event_metadata or {}),
            "credit_reservation": {
                "reservation_ref": parsed.external_ref,
                "reserved": held,
                "charged": charged,
                "adjustment": 0,
                "shortfall": 0,
                "state": "duplicate_settlement_ignored",
            },
        }
        return

    if charged <= held:
        adjustment = held - charged
        shortfall = 0
    else:
        extra_required = charged - held
        extra_debit = min(extra_required, max(current, 0))
        adjustment = -extra_debit
        shortfall = extra_required - extra_debit
    new_balance = max(current + adjustment, 0)
    org.credits_balance = new_balance
    reservation_meta = {
        "reservation_ref": parsed.external_ref,
        "reserved": held,
        "charged": charged,
        "adjustment": adjustment,
        "shortfall": shortfall,
        "state": "settled",
    }
    event.event_metadata = {
        **dict(event.event_metadata or {}),
        "credit_reservation": reservation_meta,
    }
    db.add(
        BillingCreditLedger(
            organization_id=int(organization_id),
            delta=adjustment,
            balance_after=new_balance,
            reason=f"reservation_settle:{event.feature}",
            external_ref=settlement_ref,
            entry_metadata={**reservation_meta, "event_id": int(event.id)},
        )
    )


__all__ = [
    "hold_matches_reservation",
    "release_credit_reservation",
    "settle_credit_reservation",
]
