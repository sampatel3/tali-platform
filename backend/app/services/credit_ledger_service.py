from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.billing_credit_ledger import BillingCreditLedger
from ..models.organization import Organization


def append_credit_ledger_entry(
    db: Session,
    *,
    organization: Organization,
    delta: int,
    reason: str,
    external_ref: str | None = None,
    assessment_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[BillingCreditLedger, bool]:
    if external_ref:
        existing = (
            db.query(BillingCreditLedger)
            .filter(BillingCreditLedger.external_ref == external_ref)
            .first()
        )
        if existing:
            return existing, False

    current = int(organization.credits_balance or 0)
    next_balance = current + int(delta)
    if next_balance < 0:
        raise ValueError("insufficient_credits")
    organization.credits_balance = next_balance

    entry = BillingCreditLedger(
        organization_id=organization.id,
        delta=int(delta),
        balance_after=next_balance,
        reason=reason,
        external_ref=external_ref,
        assessment_id=assessment_id,
        entry_metadata=metadata or {},
    )
    db.add(entry)
    db.flush()
    return entry, True
