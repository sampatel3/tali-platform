"""Durable credit holds for expensive provider calls.

Legacy ``usage_metering_service.reserve`` is a soft balance preflight. This
module adds the narrow hard-hold path used by autonomous task authoring: hold
credits in the existing ledger before the SDK call, reconcile to actual usage,
or release when no billable response was produced.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..models.billing_credit_ledger import BillingCreditLedger
from ..models.organization import Organization
from ..platform.config import settings
from .credit_reservation_contract import CreditReservation, reservation_from_payload
from .credit_reservation_identity import (
    build_v2_reservation,
    normalize_new_reservation_inputs,
    reuse_exact_v2_hold,
    v2_identity_metadata,
)
from .credit_reservation_ledger import release_credit_reservation, settle_credit_reservation
from .pricing_service import Feature


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
    user_id: int | None = None,
    entity_id: str | None = None,
    candidate_id: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    request_sha256: str | None = None,
    enforce_role_budget: bool = False,
) -> CreditReservation:
    """Atomically hold credits before one provider call.

    The caller must commit before invoking the provider so the metering
    client's independent session can see and settle the hold.
    """
    feature_enum, held, ref = normalize_new_reservation_inputs(
        feature=feature,
        amount=amount,
        external_ref=external_ref,
    )
    if type(organization_id) is not int or organization_id <= 0:
        raise ValueError("organization_id must be a positive integer")
    if role_id is not None and (type(role_id) is not int or role_id <= 0):
        raise ValueError("role_id must be a positive integer")
    reservation = build_v2_reservation(
        organization_id=organization_id,
        feature=feature_enum.value,
        amount=held,
        external_ref=ref,
        live=_is_live(),
        role_id=role_id,
        user_id=user_id,
        entity_id=entity_id,
        candidate_id=candidate_id,
        provider=provider,
        model=model,
        request_sha256=request_sha256,
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
        return reuse_exact_v2_hold(db, hold=existing, expected=reservation)

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
                "role_id": role_id,
                **v2_identity_metadata(reservation),
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
    from .usage_role_capacity import require_role_capacity

    require_role_capacity(
        db,
        organization_id=organization_id,
        role_id=role_id,
        required=required,
        include_active_score_commitments=include_active_score_commitments,
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
