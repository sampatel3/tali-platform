from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ...models.organization import Organization
from ...models.user import User
from ...platform.database import get_db
from .access_policy import email_domain, normalize_allowed_domains

router = APIRouter(prefix="/auth", tags=["Auth"])


class SsoCheckRequest(BaseModel):
    email: EmailStr


def _resolve_org_for_email(db: Session, email: str) -> Organization | None:
    normalized_email = str(email or "").strip().lower()
    if not normalized_email:
        return None

    user = db.query(User).filter(User.email == normalized_email).first()
    if user and user.organization_id:
        org = db.query(Organization).filter(Organization.id == user.organization_id).first()
        if org:
            return org

    domain = email_domain(normalized_email)
    if not domain:
        return None

    orgs = db.query(Organization).filter(Organization.saml_enabled == True).all()  # noqa: E712
    for org in orgs:
        allowed_domains = normalize_allowed_domains(getattr(org, "allowed_email_domains", None))
        if not allowed_domains:
            continue
        if domain in allowed_domains:
            return org
    return None


def _build_sso_payload(org: Organization | None) -> dict:
    if not org:
        return {
            "sso_enabled": False,
            "redirect_url": None,
            "organization_id": None,
            "message": "No SSO configured for this domain. Use email/password instead.",
        }

    saml_enabled = bool(getattr(org, "saml_enabled", False))
    redirect_url = str(getattr(org, "saml_metadata_url", "") or "").strip() or None
    if not saml_enabled or not redirect_url:
        return {
            "sso_enabled": False,
            "redirect_url": None,
            "organization_id": org.id,
            "message": "No SSO configured for this domain. Use email/password instead.",
        }
    return {
        "sso_enabled": True,
        "redirect_url": redirect_url,
        "organization_id": org.id,
        "message": "SSO is configured for this domain.",
    }


@router.post("/sso-check")
def sso_check(
    body: SsoCheckRequest,
    db: Session = Depends(get_db),
):
    org = _resolve_org_for_email(db, str(body.email))
    return _build_sso_payload(org)


@router.get("/sso-redirect")
def sso_redirect(
    email: EmailStr = Query(...),
    db: Session = Depends(get_db),
):
    org = _resolve_org_for_email(db, str(email))
    payload = _build_sso_payload(org)
    if not payload.get("sso_enabled"):
        raise HTTPException(status_code=404, detail=payload["message"])
    return payload

