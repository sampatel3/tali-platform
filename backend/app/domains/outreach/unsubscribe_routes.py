"""Public one-click unsubscribe — the CAN-SPAM/GDPR opt-out surface.

A signed token (HMAC over ``org_id:email``) is embedded in each outreach email's
unsubscribe link. The public page:
- GET  validates the token and returns the org name + a masked email so the
  recipient sees who they'd be unsubscribing from. GET NEVER writes — email
  prefetchers and link scanners follow GET links, and a suppress-on-GET would
  opt people out without intent.
- POST records the org-scoped suppression (reason=unsubscribed, source=link).
  Idempotent — a second POST returns 200 without erroring.

No auth (recipients have no account). Mounted at the app root under the
``/api/v1/public`` prefix, mirroring the client-intake public router.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...models.email_suppression import SUPPRESSION_REASON_UNSUBSCRIBED
from ...models.organization import Organization
from ...platform.database import get_db
from ...services.email_suppression_service import (
    suppress,
    verify_unsubscribe_token,
)


public_router = APIRouter(prefix="/api/v1/public", tags=["Unsubscribe (public)"])


def _mask_email(email: str) -> str:
    """``jane@acme.com`` → ``j***@acme.com``; keeps the domain, hides the local part."""
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _resolve_token(token: str) -> tuple[int, str]:
    parsed = verify_unsubscribe_token(token)
    if parsed is None:
        raise HTTPException(status_code=404, detail="Invalid unsubscribe link")
    return parsed


@public_router.get("/unsubscribe/{token}")
def get_unsubscribe(token: str, db: Session = Depends(get_db)):
    """Validate the token; return org name + masked email. Does NOT suppress."""
    org_id, email = _resolve_token(token)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return {
        "organization_name": org.name if org is not None else None,
        "email_masked": _mask_email(email),
    }


@public_router.post("/unsubscribe/{token}")
def post_unsubscribe(token: str, db: Session = Depends(get_db)):
    """Record the org-scoped unsubscribe. Idempotent 200."""
    org_id, email = _resolve_token(token)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    suppress(
        db,
        email=email,
        reason=SUPPRESSION_REASON_UNSUBSCRIBED,
        source="link",
        organization_id=org_id,
    )
    return {
        "status": "unsubscribed",
        "organization_name": org.name if org is not None else None,
        "email_masked": _mask_email(email),
    }
