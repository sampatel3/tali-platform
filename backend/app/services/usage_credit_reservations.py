"""Durable credit holds for expensive provider calls.

Legacy ``usage_metering_service.reserve`` is a soft balance preflight. This
module adds the narrow hard-hold path used by autonomous task authoring: hold
credits in the existing ledger before the SDK call, reconcile to actual usage,
or release when no billable response was produced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models.billing_credit_ledger import BillingCreditLedger
from ..models.organization import Organization
from ..models.usage_event import UsageEvent
from ..platform.config import settings
from .pricing_service import Feature, estimate_reservation

logger = logging.getLogger("taali.usage_credit_reservations")


class InsufficientRoleBudgetError(Exception):
    """Raised when a hard hold would exceed the role's monthly ceiling."""

    def __init__(self, *, role_id: int, required: int, available: int):
        self.role_id = int(role_id)
        self.required = int(required)
        self.available = int(available)
        super().__init__(
            f"role_id={role_id} needs {required} monthly-budget credits, "
            f"has {available} remaining"
        )


@dataclass(frozen=True)
class CreditReservation:
    """One ledger-backed hold, serializable into a metering payload."""

    organization_id: int
    feature: str
    amount: int
    external_ref: str
    live: bool

    def as_metering_payload(self) -> dict[str, Any]:
        return {
            "organization_id": int(self.organization_id),
            "feature": str(self.feature),
            "amount": int(self.amount),
            "external_ref": str(self.external_ref),
            "live": bool(self.live),
        }


def _is_live() -> bool:
    return bool(getattr(settings, "USAGE_METER_LIVE", False))


def reserve_credits(
    db: Session,
    *,
    organization_id: int,
    feature: Feature | str,
    external_ref: str,
    amount: int | None = None,
    metadata: Optional[dict] = None,
    role_id: int | None = None,
    enforce_role_budget: bool = False,
) -> CreditReservation:
    """Atomically hold credits before one provider call.

    The caller must commit before invoking the provider so the metering
    client's independent session can see and settle the hold.
    """
    feature_enum = Feature(feature) if isinstance(feature, str) else feature
    held = max(
        int(estimate_reservation(feature_enum) if amount is None else amount),
        0,
    )
    ref = str(external_ref or "").strip()
    if not ref:
        raise ValueError("external_ref is required for a credit reservation")
    reservation = CreditReservation(
        organization_id=int(organization_id),
        feature=feature_enum.value,
        amount=held,
        external_ref=ref,
        live=_is_live(),
    )
    # The role ceiling is a product budget, not a ledger-mode toggle.  Usage
    # events are written in both shadow and live modes, so callers with role
    # context must still fail closed while the ledger debit is shadowed.
    if enforce_role_budget and role_id is not None and not reservation.live:
        ensure_role_capacity(
            db,
            organization_id=int(organization_id),
            role_id=int(role_id),
            required=held,
            include_active_score_commitments=False,
        )
    if not reservation.live:
        return reservation

    org = (
        db.query(Organization)
        .filter(Organization.id == int(organization_id))
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if org is None:
        from .usage_metering_service import InsufficientCreditsError

        raise InsufficientCreditsError(
            organization_id=int(organization_id), required=held, available=0
        )

    existing = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == ref)
        .one_or_none()
    )
    if existing is not None:
        if (
            int(existing.organization_id) != int(organization_id)
            or not str(existing.reason).startswith("reservation:")
        ):
            raise ValueError(f"credit reservation ref already used: {ref}")
        if (
            db.query(BillingCreditLedger.id)
            .filter(BillingCreditLedger.external_ref == f"{ref}:settled")
            .first()
            is not None
        ):
            raise ValueError(f"credit reservation already settled: {ref}")
        return CreditReservation(
            organization_id=int(existing.organization_id),
            feature=feature_enum.value,
            amount=max(-int(existing.delta), 0),
            external_ref=ref,
            live=True,
        )

    if enforce_role_budget and role_id is not None:
        ensure_role_capacity(
            db,
            organization_id=int(organization_id),
            role_id=int(role_id),
            required=held,
            include_active_score_commitments=False,
        )

    available = int(org.credits_balance or 0)
    if available < held:
        from .usage_metering_service import InsufficientCreditsError

        raise InsufficientCreditsError(
            organization_id=int(organization_id),
            required=held,
            available=available,
        )
    new_balance = available - held
    org.credits_balance = new_balance
    db.add(
        BillingCreditLedger(
            organization_id=int(organization_id),
            delta=-held,
            balance_after=new_balance,
            reason=f"reservation:{feature_enum.value}",
            external_ref=ref,
            entry_metadata={
                **dict(metadata or {}),
                "feature": feature_enum.value,
                "reserved": held,
                "role_id": int(role_id) if role_id is not None else None,
                "state": "held",
            },
        )
    )
    db.flush()
    return reservation


def ensure_role_capacity(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    required: int,
    include_active_score_commitments: bool = True,
) -> None:
    from ..agent_runtime.budget_guard import (
        remaining_role_admission_microcredits,
    )
    from ..models.role import Role

    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if role is None:
        raise InsufficientRoleBudgetError(
            role_id=int(role_id), required=required, available=0
        )
    remaining = remaining_role_admission_microcredits(
        db,
        role=role,
        # Soft producer gates use active score jobs as conservative pending
        # commitments. A real ledger-backed provider hold must not count that
        # same running job again: the hold itself is now the authoritative
        # commitment and settlement rail for each actual SDK attempt.
        per_active_score_job=(
            estimate_reservation(Feature.SCORE)
            if include_active_score_commitments
            else 0
        ),
    )
    if remaining is None:
        return

    held_rows = (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.organization_id == int(organization_id),
            BillingCreditLedger.reason.like("reservation:%"),
        )
        .all()
    )
    settlement_refs = {
        str(value)
        for (value,) in db.query(BillingCreditLedger.external_ref)
        .filter(
            BillingCreditLedger.organization_id == int(organization_id),
            BillingCreditLedger.external_ref.isnot(None),
        )
        .all()
    }
    outstanding = 0
    for row in held_rows:
        row_meta = row.entry_metadata if isinstance(row.entry_metadata, dict) else {}
        try:
            row_role_id = int(row_meta.get("role_id"))
        except (TypeError, ValueError):
            continue
        if row_role_id != int(role_id):
            continue
        if f"{row.external_ref}:settled" in settlement_refs:
            continue
        outstanding += max(-int(row.delta), 0)
    available_role = max(int(remaining) - outstanding, 0)
    if available_role < required:
        raise InsufficientRoleBudgetError(
            role_id=int(role_id),
            required=required,
            available=available_role,
        )


def reservation_from_payload(
    value: CreditReservation | dict[str, Any] | None,
) -> CreditReservation | None:
    if isinstance(value, CreditReservation):
        return value
    if not isinstance(value, dict):
        return None
    try:
        return CreditReservation(
            organization_id=int(value["organization_id"]),
            feature=str(value["feature"]),
            amount=max(int(value["amount"]), 0),
            external_ref=str(value["external_ref"]),
            live=bool(value.get("live", True)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def release_credit_reservation(
    db: Session,
    *,
    reservation: CreditReservation | dict[str, Any],
    reason: str = "provider_call_failed",
    allow_started: bool = False,
) -> int:
    """Idempotently refund a live hold when no billable attempt began.

    A broad caller ``except`` must never undo a provider-attempt/success marker
    after the paid response path has started. Only the shared provider-error
    classifier may set ``allow_started=True`` for an explicit non-billable
    rejection (for example, a concrete non-timeout HTTP 4xx response).
    """
    parsed = reservation_from_payload(reservation)
    # Existing live holds must remain recoverable even if operators temporarily
    # switch the usage meter back to shadow mode after an incident.
    if parsed is None or not parsed.live:
        return 0
    settlement_ref = f"{parsed.external_ref}:settled"
    org = (
        db.query(Organization)
        .filter(Organization.id == int(parsed.organization_id))
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if org is None:
        return 0
    if (
        db.query(BillingCreditLedger.id)
        .filter(BillingCreditLedger.external_ref == settlement_ref)
        .first()
        is not None
    ):
        return 0
    held_row = (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref == parsed.external_ref,
            BillingCreditLedger.organization_id == int(parsed.organization_id),
        )
        .one_or_none()
    )
    if held_row is None or not str(held_row.reason).startswith("reservation:"):
        return 0
    held_metadata = (
        held_row.entry_metadata
        if isinstance(held_row.entry_metadata, dict)
        else {}
    )
    if not allow_started and str(held_metadata.get("state") or "") in {
        "provider_attempt_started",
        "provider_succeeded_metering_pending",
        "provider_succeeded_usage_unknown",
    }:
        logger.warning(
            "refusing unsafe provider hold release ref=%s state=%s reason=%s",
            parsed.external_ref,
            held_metadata.get("state"),
            reason,
        )
        return 0
    held = max(-int(held_row.delta), 0)
    new_balance = int(org.credits_balance or 0) + held
    org.credits_balance = new_balance
    db.add(
        BillingCreditLedger(
            organization_id=int(parsed.organization_id),
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


def settle_credit_reservation(
    db: Session,
    *,
    organization_id: int,
    event: UsageEvent,
    reservation: CreditReservation,
) -> None:
    """Reconcile a hold to the actual charge without overdrawing."""
    settlement_ref = f"{reservation.external_ref}:settled"
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
            BillingCreditLedger.external_ref == reservation.external_ref,
            BillingCreditLedger.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    if org is None or held_row is None or not str(held_row.reason).startswith(
        "reservation:"
    ):
        from .usage_metering_service import _debit_ledger

        logger.error(
            "usage reservation missing during settlement org=%s ref=%s",
            organization_id,
            reservation.external_ref,
        )
        _debit_ledger(db, organization_id=organization_id, event=event)
        return
    held = max(-int(held_row.delta), 0)
    charged = max(int(event.credits_charged or 0), 0)
    current = int(org.credits_balance or 0)
    settlement_row = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == settlement_ref)
        .one_or_none()
    )
    if settlement_row is not None:
        # A crash reaper can release a stale hold shortly before a very late
        # provider result lands. Charge that result from the current balance
        # exactly once; never re-consume the already-refunded hold and never
        # drive the org negative. A normal already-settled reservation is a
        # duplicate payload and therefore makes no second debit.
        if str(settlement_row.reason).startswith("reservation_release:"):
            late_ref = f"{reservation.external_ref}:late-settled"
            if (
                db.query(BillingCreditLedger.id)
                .filter(BillingCreditLedger.external_ref == late_ref)
                .first()
                is None
            ):
                actual_debit = min(charged, max(current, 0))
                shortfall = charged - actual_debit
                new_balance = max(current - actual_debit, 0)
                org.credits_balance = new_balance
                late_meta = {
                    "reservation_ref": reservation.external_ref,
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

        logger.error(
            "usage reservation duplicate settlement ignored ref=%s",
            settlement_ref,
        )
        event.event_metadata = {
            **dict(event.event_metadata or {}),
            "credit_reservation": {
                "reservation_ref": reservation.external_ref,
                "reserved": held,
                "charged": charged,
                "adjustment": 0,
                "shortfall": 0,
                "state": "duplicate_settlement_ignored",
            },
        }
        return

    shortfall = 0
    if charged <= held:
        adjustment = held - charged
    else:
        extra_required = charged - held
        extra_debit = min(extra_required, max(current, 0))
        adjustment = -extra_debit
        shortfall = extra_required - extra_debit
    new_balance = max(current + adjustment, 0)
    org.credits_balance = new_balance
    reservation_meta = {
        "reservation_ref": reservation.external_ref,
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
    "CreditReservation",
    "InsufficientRoleBudgetError",
    "ensure_role_capacity",
    "release_credit_reservation",
    "reservation_from_payload",
    "reserve_credits",
    "settle_credit_reservation",
]
