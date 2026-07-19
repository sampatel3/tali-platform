"""Role-budget capacity calculation for durable provider reservations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists
from sqlalchemy.orm import aliased

from ..models.billing_credit_ledger import BillingCreditLedger
from .pricing_service import Feature, estimate_reservation


def require_role_capacity(
    db: Any,
    *,
    organization_id: int,
    role_id: int,
    required: int,
    include_active_score_commitments: bool,
) -> None:
    from ..agent_runtime.budget_guard import remaining_role_admission_microcredits
    from ..models.role import Role
    from .usage_credit_reservations import InsufficientRoleBudgetError

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
        per_active_score_job=(
            estimate_reservation(Feature.SCORE)
            if include_active_score_commitments
            else 0
        ),
    )
    if remaining is None:
        return
    settlement = aliased(BillingCreditLedger)
    held_rows = (
        db.query(
            BillingCreditLedger.delta,
            BillingCreditLedger.entry_metadata,
        )
        .filter(
            BillingCreditLedger.organization_id == int(organization_id),
            BillingCreditLedger.reason.like("reservation:%"),
            ~exists().where(
                settlement.organization_id == int(organization_id),
                settlement.external_ref
                == BillingCreditLedger.external_ref + ":settled",
            ),
        )
        .all()
    )
    outstanding = 0
    for delta, entry_metadata in held_rows:
        metadata = entry_metadata if isinstance(entry_metadata, dict) else {}
        try:
            row_role_id = int(metadata.get("role_id"))
        except (TypeError, ValueError):
            continue
        if row_role_id == int(role_id):
            outstanding += max(-int(delta), 0)
    available = max(int(remaining) - outstanding, 0)
    if available < required:
        raise InsufficientRoleBudgetError(
            role_id=int(role_id), required=required, available=available
        )


__all__ = ["require_role_capacity"]
