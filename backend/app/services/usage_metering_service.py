"""Records billable Claude calls and (optionally) debits the credit ledger.

Two modes, switched by ``settings.USAGE_METER_LIVE``:

- **Shadow mode** (default — Phase 2): every Claude call writes a
  ``usage_events`` row but the ledger is **not** debited. Lets us validate
  attribution + cost numbers against Anthropic's own dashboard for ~1 week
  before flipping the meter on.

- **Live mode** (Phase 6): every Claude call writes a ``usage_events`` row
  AND a paired ``billing_credit_ledger`` debit. Pre-flight ``reserve()``
  gates block calls when the org's balance is insufficient.

All accounting is in micro-credits (see ``pricing_service``).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.billing_credit_ledger import BillingCreditLedger
from ..models.organization import Organization
from ..models.usage_event import UsageEvent
from ..platform.config import settings
from .pricing_service import (
    Feature,
    credits_charged,
    estimate_reservation,
    raw_cost_usd_micro,
    feature_pricing,
)

logger = logging.getLogger("taali.usage_metering")


class InsufficientCreditsError(Exception):
    """Raised when an org tries to act with insufficient balance.

    Routes should catch this and return HTTP 402.
    """

    def __init__(self, *, organization_id: int, required: int, available: int):
        self.organization_id = organization_id
        self.required = required
        self.available = available
        super().__init__(
            f"organization_id={organization_id} needs {required} credits, has {available}"
        )


def _is_live() -> bool:
    return bool(getattr(settings, "USAGE_METER_LIVE", False))


def reserve(
    db: Session,
    *,
    organization_id: int,
    feature: Feature | str,
) -> int:
    """Pre-flight balance check. Returns the reservation amount in
    micro-credits. In shadow mode this is a no-op (returns the estimate
    without checking balance). In live mode, raises
    ``InsufficientCreditsError`` if the org can't cover the estimate.

    The reservation is **not** held — actual charging happens in
    ``record_event()`` after the Claude call. This is a soft gate, not a
    transactional hold; under heavy concurrency two calls can both pass
    the gate and leave the balance slightly negative. Acceptable for v1
    because the worst case is a few thousand credits float for seconds.
    """
    estimate = estimate_reservation(feature)
    if not _is_live():
        return estimate

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        raise InsufficientCreditsError(
            organization_id=organization_id, required=estimate, available=0
        )
    available = int(org.credits_balance or 0)
    if available < estimate:
        raise InsufficientCreditsError(
            organization_id=organization_id, required=estimate, available=available
        )
    return estimate


def record_event(
    db: Session,
    *,
    organization_id: int,
    feature: Feature | str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_hit: bool = False,
    user_id: Optional[int] = None,
    entity_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> UsageEvent:
    """Record a billable Claude call. Always writes a ``usage_events`` row.
    In live mode also writes a paired ledger debit and decrements the org's
    ``credits_balance`` atomically.

    Returns the created ``UsageEvent``.

    The caller is responsible for committing the session — this function
    only adds rows. Keeping commit at the caller lets the metering live
    inside the same transaction as the underlying scoring/assessment
    work, so a Claude call that succeeded but failed to write its result
    also rolls back its meter event.
    """
    if isinstance(feature, str):
        feature_enum = Feature(feature)
    else:
        feature_enum = feature

    cost_usd_micro = raw_cost_usd_micro(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )
    charged = credits_charged(
        feature=feature_enum, cost_usd_micro=cost_usd_micro, cache_hit=cache_hit
    )
    pricing = feature_pricing(feature_enum)
    multiplier = (
        pricing.cache_hit_multiplier if cache_hit else pricing.markup_multiplier
    )

    event = UsageEvent(
        organization_id=organization_id,
        user_id=user_id,
        feature=feature_enum.value,
        entity_id=entity_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cost_usd_micro=cost_usd_micro,
        markup_multiplier=multiplier,
        credits_charged=charged,
        cache_hit=1 if cache_hit else 0,
        event_metadata=metadata or None,
    )
    db.add(event)
    db.flush()  # populate event.id

    if _is_live():
        _debit_ledger(db, organization_id=organization_id, event=event)
    else:
        logger.debug(
            "usage_metering[shadow] org=%s feature=%s tokens=%s/%s charged=%s",
            organization_id, feature_enum.value, input_tokens, output_tokens, charged,
        )

    return event


def _debit_ledger(db: Session, *, organization_id: int, event: UsageEvent) -> None:
    """Atomically decrement org balance and append a ledger row.

    Uses ``SELECT ... FOR UPDATE`` on the org row to serialize concurrent
    debits and prevent overdraft races. SQLite (used in tests) ignores
    ``with_for_update()`` silently — fine, tests aren't concurrent.
    """
    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .with_for_update()
        .first()
    )
    if org is None:
        logger.error("Cannot debit: organization_id=%s not found", organization_id)
        return

    current = int(org.credits_balance or 0)
    new_balance = current - int(event.credits_charged)
    org.credits_balance = new_balance

    ledger = BillingCreditLedger(
        organization_id=organization_id,
        delta=-int(event.credits_charged),
        balance_after=new_balance,
        reason=f"usage:{event.feature}",
        external_ref=f"usage:{event.id}",
        entry_metadata={
            "event_id": event.id,
            "feature": event.feature,
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "cache_hit": bool(event.cache_hit),
        },
    )
    db.add(ledger)


def grant_credits(
    db: Session,
    *,
    organization_id: int,
    grant_type: str,
    credits: int,
    external_ref: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[BillingCreditLedger]:
    """Add credits to an org. Idempotent on ``external_ref`` — passing the
    same ref twice is a no-op (returns ``None`` on the second call).

    Used for: free tier grant on signup, Stripe top-up webhook, manual
    promo grants, refunds.
    """
    from ..models.usage_grant import UsageGrant

    if external_ref:
        existing = (
            db.query(UsageGrant)
            .filter(UsageGrant.external_ref == external_ref)
            .first()
        )
        if existing is not None:
            return None

    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .with_for_update()
        .first()
    )
    if org is None:
        raise ValueError(f"organization_id={organization_id} not found")

    current = int(org.credits_balance or 0)
    new_balance = current + int(credits)
    org.credits_balance = new_balance

    grant = UsageGrant(
        organization_id=organization_id,
        grant_type=grant_type,
        credits_granted=int(credits),
        external_ref=external_ref,
        grant_metadata=metadata or None,
    )
    db.add(grant)

    ledger = BillingCreditLedger(
        organization_id=organization_id,
        delta=int(credits),
        balance_after=new_balance,
        reason=f"grant:{grant_type}",
        external_ref=external_ref or f"grant:{grant_type}:{organization_id}",
        entry_metadata=metadata or None,
    )
    db.add(ledger)
    return ledger


def usage_summary(
    db: Session,
    *,
    organization_id: int,
    since=None,
) -> dict:
    """Aggregate usage for an org. Used by the settings billing tab.

    Returns counts/tokens/credits per feature plus org's current balance.
    """
    q = db.query(
        UsageEvent.feature,
        func.count(UsageEvent.id).label("event_count"),
        func.sum(UsageEvent.input_tokens).label("input_tokens"),
        func.sum(UsageEvent.output_tokens).label("output_tokens"),
        func.sum(UsageEvent.cost_usd_micro).label("cost_usd_micro"),
        func.sum(UsageEvent.credits_charged).label("credits_charged"),
    ).filter(UsageEvent.organization_id == organization_id)
    if since is not None:
        q = q.filter(UsageEvent.created_at >= since)
    rows = q.group_by(UsageEvent.feature).all()

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    balance = int(org.credits_balance or 0) if org else 0

    return {
        "balance_credits": balance,
        "by_feature": [
            {
                "feature": r.feature,
                "event_count": int(r.event_count or 0),
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "cost_usd_micro": int(r.cost_usd_micro or 0),
                "credits_charged": int(r.credits_charged or 0),
            }
            for r in rows
        ],
    }
