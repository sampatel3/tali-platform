import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_users.jwt import decode_jwt, generate_jwt
from sqlalchemy.orm import Session

from ...platform.database import get_db
from ...deps import get_current_user
from ...platform.security import get_password_hash
from ...models.user import User
from ...models.organization import Organization
from ...schemas.user import (
    UserResponse,
    TeamInviteRequest,
    TeamInviteResponse,
    ResendInviteResponse,
)
from ...domains.integrations_notifications.adapters import build_email_adapter
from ...platform.config import settings
from .access_policy import (
    is_email_allowed_for_domains,
    normalize_allowed_domains,
)

logger = logging.getLogger("taali.users")

router = APIRouter(prefix="/users", tags=["Users"])

# --- Invite token (JWT, 7-day) ---------------------------------------------
# Distinct audience from the FastAPI-Users reset/verify tokens so an invite
# token can't be replayed against those flows (and vice versa).
INVITE_TOKEN_AUDIENCE = "invite"
INVITE_TOKEN_LIFETIME_SECONDS = 7 * 24 * 3600


def generate_invite_token(user: User) -> str:
    data = {"sub": str(user.id), "email": user.email, "aud": INVITE_TOKEN_AUDIENCE}
    return generate_jwt(data, settings.SECRET_KEY, INVITE_TOKEN_LIFETIME_SECONDS)


def decode_invite_token(token: str) -> dict | None:
    """Return the decoded claims for a valid invite token, else None
    (expired, wrong audience, tampered, or otherwise malformed)."""
    try:
        return decode_jwt(token, settings.SECRET_KEY, [INVITE_TOKEN_AUDIENCE])
    except Exception:
        return None


def _send_invite_email(invited: User, inviter: User, org: Organization) -> bool:
    """Send the team-invite email. Returns whether it was delivered. Never
    raises — a send failure must not fail the invite; the caller surfaces the
    ``email_sent`` flag so the recruiter can resend."""
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping team invite email for %s", invited.email)
        return False
    try:
        token = generate_invite_token(invited)
        accept_link = f"{settings.FRONTEND_URL}/accept-invite?token={token}"
        email_svc = build_email_adapter()
        result = email_svc.send_team_invite(
            to_email=invited.email,
            inviter_name=inviter.full_name or inviter.email,
            org_name=org.name,
            accept_link=accept_link,
        )
        return bool(result.get("success"))
    except Exception:
        logger.exception("Failed to send team invite email to %s", invited.email)
        return False


@router.get("/", response_model=list[UserResponse])
def list_team_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.organization_id:
        return []
    return (
        db.query(User)
        .filter(
            User.organization_id == current_user.organization_id,
            User.is_active == True,  # noqa: E712 — soft-removed members are hidden
        )
        .order_by(User.created_at.asc())
        .all()
    )


@router.post("/invite", response_model=TeamInviteResponse, status_code=status.HTTP_201_CREATED)
def invite_team_user(
    data: TeamInviteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.organization_id:
        raise HTTPException(status_code=400, detail="You are not in an organization")
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if getattr(org, "sso_enforced", False):
        raise HTTPException(
            status_code=403,
            detail="Organization enforces SSO. Provision users through your identity provider.",
        )
    allowed_domains = normalize_allowed_domains(getattr(org, "allowed_email_domains", None))
    if not is_email_allowed_for_domains(data.email, allowed_domains):
        raise HTTPException(status_code=400, detail="Email domain is not allowed for this organization")

    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        # A revoked invite in this same org (soft-removed, never accepted) can
        # be re-invited: re-activate the row and resend rather than erroring.
        is_revoked_invite = (
            existing.organization_id == current_user.organization_id
            and not existing.is_active
            and not existing.is_verified
        )
        if not is_revoked_invite:
            raise HTTPException(status_code=400, detail="Email already exists")
        existing.is_active = True
        existing.full_name = data.full_name
        invited = existing
    else:
        temp_password = secrets.token_urlsafe(16)
        invited = User(
            email=data.email,
            full_name=data.full_name,
            hashed_password=get_password_hash(temp_password),
            organization_id=current_user.organization_id,
        )
        db.add(invited)

    try:
        db.commit()
        db.refresh(invited)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to invite team member")

    email_sent = _send_invite_email(invited, current_user, org)
    return _invite_response(invited, email_sent)


@router.post("/{user_id}/resend-invite", response_model=ResendInviteResponse)
def resend_team_invite(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target = _get_org_member(db, user_id, current_user)
    if target.is_verified or not target.is_active:
        raise HTTPException(status_code=400, detail="NOT_PENDING_INVITE")
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    email_sent = _send_invite_email(target, current_user, org)
    return ResendInviteResponse(email_sent=email_sent)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_team_member(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="CANNOT_REMOVE_SELF")
    target = _get_org_member(db, user_id, current_user)
    # Soft-remove only — other tables reference users; never hard-delete.
    target.is_active = False
    db.commit()
    return None


def _get_org_member(db: Session, user_id: int, current_user: User) -> User:
    """Fetch a user in the caller's org. Cross-org or missing → 404 (don't
    leak whether the id exists in another org)."""
    if not current_user.organization_id:
        raise HTTPException(status_code=404, detail="User not found")
    target = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return target


def _invite_response(user: User, email_sent: bool) -> TeamInviteResponse:
    # ``status`` is a computed field — build straight from the ORM object and
    # attach the delivery flag rather than round-tripping through a dict.
    resp = TeamInviteResponse.model_validate(user)
    resp.email_sent = email_sent
    return resp
