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

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..models.billing_credit_ledger import BillingCreditLedger
from ..models.organization import Organization
from ..models.usage_event import UsageEvent
from ..platform.config import settings
from .pricing_service import (
    Feature,
    credits_charged,
    estimate_reservation,
    feature_pricing,
    is_voyage_model,
    voyage_cost_micro,
)
from vendor.mainspring_metering.pricing import cost_for as seam_cost_for

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

    # ``record_event`` intentionally commits debits in a fresh session at
    # several call sites (notably the candidate assessment SDK).  A long-lived
    # request session may therefore already have an Organization instance in
    # its identity map with the pre-debit balance.  ``populate_existing`` makes
    # every paid-call gate read the current database value instead of allowing
    # a second call on stale credits.
    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .populate_existing()
        .first()
    )
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
    cache_creation_1h_tokens: Optional[int] = None,
    cache_hit: bool = False,
    service_tier: str = "standard",
    user_id: Optional[int] = None,
    role_id: Optional[int] = None,
    entity_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    provider_cost_usd_micro: Optional[int] = None,
    credit_reservation: object | None = None,
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

    # ADR-0010 metering CUTOVER: the vendored mainspring seam is now the single
    # source of truth for the raw Anthropic call cost. It prices bit-for-bit
    # identically to tali's former ``raw_cost_usd_micro`` (proven exact over the
    # full model x usage x {standard,batch} corpus — see
    # tests/test_metering_pricing_parity.py), so this is a zero-behaviour-change
    # swap. The per-feature markup below stays tali business logic.
    if provider_cost_usd_micro is not None:
        # Some provider-owned agent loops expose a trustworthy aggregate cost
        # for the completed invocation but not the individual wire calls.  Let
        # those callers feed the reported total through this canonical path so
        # the UsageEvent and live ledger debit remain atomic.  ``None`` means
        # "price from tokens"; an explicit zero remains zero and is never
        # replaced with an invented estimate.
        raw_anthropic_cost_micro = max(int(provider_cost_usd_micro), 0)
    elif is_voyage_model(model):
        # Voyage embeddings (Graphiti's vector layer) are a non-Anthropic
        # provider: input tokens only, no output/cache/tier. Price via the
        # Voyage rate table instead of the Anthropic seam so the spend still
        # flows into credits + the org budget. ``model="voyage-*"`` never
        # matches an Anthropic model family, so these rows are naturally
        # excluded from the Anthropic Admin-API reconciliation.
        raw_anthropic_cost_micro = voyage_cost_micro(
            model=model, input_tokens=input_tokens
        )
    else:
        raw_anthropic_cost_micro = seam_cost_for(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_creation_1h_tokens=cache_creation_1h_tokens,
            service_tier=service_tier,
        )
    # ``credits_charged`` (what the customer pays) is still derived from the
    # full cost × the cache-hit markup — preserving the small fee that stops
    # unlimited free re-scoring. ``cost_usd_micro`` and the token columns,
    # however, must reflect what Anthropic ACTUALLY billed us for THIS event.
    charged = credits_charged(
        feature=feature_enum,
        cost_usd_micro=raw_anthropic_cost_micro,
        cache_hit=cache_hit,
    )
    pricing = feature_pricing(feature_enum)
    multiplier = (
        pricing.cache_hit_multiplier if cache_hit else pricing.markup_multiplier
    )

    # Cache-hit events make NO Anthropic call (the result is served from
    # cv_score_cache), so ``claude_call_log`` — the reconciliation oracle —
    # records nothing for them. Persisting the cached result's token counts
    # and full cost on the usage_event therefore fabricates Anthropic spend
    # that never happened: it inflates naive SUM(usage_events) reconciliation
    # (the haiku output over-count), and double-counts against the role's
    # monthly USD budget (budget_guard sums cost_usd_micro). A single open
    # application re-scored off cache 25-29× per day booked 25-29× phantom
    # Anthropic spend. Zero the Anthropic-attributable columns so usage_events
    # agrees with claude_call_log (no call ⇒ no spend); the customer-facing
    # ``credits_charged`` cache fee above is unaffected.
    if cache_hit:
        recorded_input = 0
        recorded_output = 0
        recorded_cache_read = 0
        recorded_cache_creation = 0
        recorded_cache_creation_1h = 0 if cache_creation_1h_tokens is not None else None
        recorded_cost_micro = 0
    else:
        recorded_input = input_tokens
        recorded_output = output_tokens
        recorded_cache_read = cache_read_tokens
        recorded_cache_creation = cache_creation_tokens
        recorded_cache_creation_1h = cache_creation_1h_tokens
        recorded_cost_micro = raw_anthropic_cost_micro

    # Persist the tier in metadata (there is no dedicated column yet) so batch
    # spend stays queryable and reconciliation can tell standard vs batch apart.
    meta = dict(metadata or {})
    if provider_cost_usd_micro is not None:
        route = meta.get("ai_routing")
        route_cost_authority = (
            route.get("cost_authority") if isinstance(route, dict) else None
        )
        meta.setdefault(
            "cost_source",
            str(route_cost_authority or "provider_reported"),
        )
    if service_tier and service_tier != "standard":
        meta.setdefault("service_tier", service_tier)

    event = UsageEvent(
        organization_id=organization_id,
        user_id=user_id,
        role_id=role_id,
        feature=feature_enum.value,
        entity_id=entity_id,
        model=model,
        input_tokens=recorded_input,
        output_tokens=recorded_output,
        cache_read_tokens=recorded_cache_read,
        cache_creation_tokens=recorded_cache_creation,
        cache_creation_1h_tokens=recorded_cache_creation_1h,
        cost_usd_micro=recorded_cost_micro,
        markup_multiplier=multiplier,
        credits_charged=charged,
        cache_hit=1 if cache_hit else 0,
        event_metadata=meta or None,
    )
    db.add(event)
    db.flush()  # populate event.id

    if _is_live():
        from .usage_credit_reservations import (
            reservation_from_payload,
            settle_credit_reservation,
        )

        reservation = reservation_from_payload(credit_reservation)
        if (
            reservation is not None
            and reservation.live
            and int(reservation.organization_id) == int(organization_id)
            and reservation.feature == feature_enum.value
        ):
            settle_credit_reservation(
                db,
                organization_id=organization_id,
                event=event,
                reservation=reservation,
            )
        else:
            _debit_ledger(db, organization_id=organization_id, event=event)
    else:
        logger.debug(
            "usage_metering[shadow] org=%s feature=%s tokens=%s/%s charged=%s",
            organization_id,
            feature_enum.value,
            input_tokens,
            output_tokens,
            charged,
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
            db.query(UsageGrant).filter(UsageGrant.external_ref == external_ref).first()
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
        # cost_usd_micro = real Anthropic cost → exclude cache hits (no call);
        # credits_charged keeps the cache fee (what the customer pays).
        func.sum(
            case((UsageEvent.cache_hit == 0, UsageEvent.cost_usd_micro), else_=0)
        ).label("cost_usd_micro"),
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
