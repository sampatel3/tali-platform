"""Short database phases around hosted Stripe Checkout and Portal calls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...components.integrations.stripe.topup_service import StripeTopupError
from ...models.organization import Organization
from ...models.user import User
from ...platform.config import settings
from ...platform.frontend_origins import trusted_frontend_redirect_url
from ...services.pricing_service import resolve_pack

logger = logging.getLogger("taali.billing")


@dataclass(frozen=True)
class _BillingActorSnapshot:
    user_id: int
    organization_id: int
    email: str
    stripe_customer_id: str | None = None


def _trusted_redirect(value: str) -> str:
    try:
        return trusted_frontend_redirect_url(
            value,
            frontend_url=settings.FRONTEND_URL,
            extra_origins=getattr(settings, "CORS_EXTRA_ORIGINS", None),
            origin_regex=getattr(settings, "CORS_ALLOW_ORIGIN_REGEX", None),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Redirect URL must use a trusted frontend origin",
        ) from exc


def _snapshot_actor(
    db: Session,
    *,
    user_id: int,
    organization_id: int | None,
    require_customer: bool,
) -> _BillingActorSnapshot:
    if organization_id is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    row = (
        db.query(User.email, Organization.stripe_customer_id)
        .join(Organization, Organization.id == User.organization_id)
        .filter(
            User.id == int(user_id),
            User.organization_id == int(organization_id),
            User.is_active.is_(True),
            Organization.id == int(organization_id),
        )
        .one_or_none()
    )
    if row is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Organization not found")
    customer_id = str(row.stripe_customer_id or "").strip() or None
    if require_customer and customer_id is None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="No billing account yet. Add credits first to set up billing.",
        )
    snapshot = _BillingActorSnapshot(
        user_id=int(user_id),
        organization_id=int(organization_id),
        email=str(row.email),
        stripe_customer_id=customer_id,
    )
    db.rollback()
    assert not db.in_transaction(), "Stripe provider call must not retain an ORM transaction"
    return snapshot


def _reauthorize_actor(
    db: Session,
    snapshot: _BillingActorSnapshot,
    *,
    for_update: bool,
) -> Organization:
    query = (
        db.query(Organization)
        .join(User, User.organization_id == Organization.id)
        .filter(
            Organization.id == snapshot.organization_id,
            User.id == snapshot.user_id,
            User.organization_id == snapshot.organization_id,
            User.email == snapshot.email,
            User.is_active.is_(True),
        )
    )
    if snapshot.stripe_customer_id is not None:
        query = query.filter(
            Organization.stripe_customer_id == snapshot.stripe_customer_id
        )
    if for_update:
        query = query.with_for_update()
    organization = query.one_or_none()
    if organization is None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Billing authorization changed while contacting the payment service",
        )
    return organization


def create_topup_response(
    db: Session,
    *,
    user_id: int,
    organization_id: int | None,
    pack_id: str,
    success_url: str,
    cancel_url: str,
    provider: Callable[..., str],
) -> dict[str, str]:
    if resolve_pack(pack_id) is None:
        raise HTTPException(status_code=400, detail="Invalid pack_id")
    trusted_success_url = _trusted_redirect(success_url)
    trusted_cancel_url = _trusted_redirect(cancel_url)
    snapshot = _snapshot_actor(
        db,
        user_id=user_id,
        organization_id=organization_id,
        require_customer=False,
    )
    try:
        url = provider(
            org_id=snapshot.organization_id,
            customer_email=snapshot.email,
            pack_id=pack_id,
            success_url=trusted_success_url,
            cancel_url=trusted_cancel_url,
        )
    except StripeTopupError as exc:
        logger.warning("Stripe top-up session creation failed")
        raise HTTPException(
            status_code=502,
            detail="Payment service error. Please try again.",
        ) from exc

    # The hosted session is detached. Re-check the exact actor/org relation in
    # the transaction that performs the only local write.
    db.rollback()
    organization = _reauthorize_actor(db, snapshot, for_update=True)
    organization.billing_provider = "stripe"
    db.commit()
    return {"url": str(url)}


def create_portal_response(
    db: Session,
    *,
    user_id: int,
    organization_id: int | None,
    return_url: str,
    provider: Callable[..., str],
) -> dict[str, str]:
    trusted_return_url = _trusted_redirect(return_url)
    snapshot = _snapshot_actor(
        db,
        user_id=user_id,
        organization_id=organization_id,
        require_customer=True,
    )
    try:
        url = provider(
            customer_id=str(snapshot.stripe_customer_id),
            return_url=trusted_return_url,
        )
    except StripeTopupError as exc:
        logger.warning("Stripe portal session creation failed")
        raise HTTPException(
            status_code=502,
            detail="Payment service error. Please try again.",
        ) from exc

    db.rollback()
    _reauthorize_actor(db, snapshot, for_update=False)
    db.rollback()
    return {"url": str(url)}


__all__ = ["create_portal_response", "create_topup_response"]
