from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_users.jwt import generate_jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ...models.organization import Organization
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...platform.security import get_password_hash
from ...schemas.user import AcceptInviteRequest, Token
from .access_policy import email_domain, normalize_allowed_domains
from .password_policy import check_password_strength
from .user_routes import decode_invite_token
from .users_fastapi import auth_backend

router = APIRouter(prefix="/auth", tags=["Auth"])


def _mint_login_token(user: User) -> str:
    """Mint a JWT identical to the one the FastAPI-Users login endpoint
    returns, so the frontend can log the user straight in after accepting."""
    data = {"sub": str(user.id), "aud": auth_backend.get_strategy().token_audience}
    return generate_jwt(
        data,
        settings.SECRET_KEY,
        settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


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


@router.post("/accept-invite", response_model=Token)
def accept_invite(
    body: AcceptInviteRequest,
    db: Session = Depends(get_db),
):
    claims = decode_invite_token(body.token)
    if not claims:
        raise HTTPException(status_code=400, detail="INVITE_TOKEN_INVALID")

    user = db.query(User).filter(User.id == int(claims.get("sub") or 0)).first()
    # Bind the token to the exact user it was issued for: sub AND email must
    # both match, so a re-emailed/changed address can't reuse an old token.
    if not user or user.email != claims.get("email"):
        raise HTTPException(status_code=400, detail="INVITE_TOKEN_INVALID")
    if user.is_verified:
        raise HTTPException(status_code=400, detail="INVITE_ALREADY_ACCEPTED")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="INVITE_REVOKED")

    # The org may have enforced SSO after the invite was sent — accepting
    # must not become a password-auth bypass. Provision via the IdP instead.
    if user.organization_id:
        org = db.query(Organization).filter(Organization.id == user.organization_id).first()
        if org and getattr(org, "sso_enforced", False):
            raise HTTPException(status_code=400, detail="INVITE_SSO_REQUIRED")

    # Same strength policy as the FastAPI-Users config (length + blocklist +
    # email-similarity). Reuses the single source of truth in password_policy.
    reason = check_password_strength(body.password, email=user.email)
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)

    user.hashed_password = get_password_hash(body.password)
    user.is_verified = True
    db.commit()
    db.refresh(user)

    return Token(access_token=_mint_login_token(user), token_type="bearer")

