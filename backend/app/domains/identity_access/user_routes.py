from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import secrets

from ...platform.database import get_db
from ...deps import get_current_user, require_org_owner
from ...platform.security import get_password_hash
from ...models.user import User
from ...models.organization import Organization
from ...schemas.user import UserResponse, TeamInviteRequest, TeamRoleUpdateRequest
from ...domains.integrations_notifications.adapters import build_email_adapter
from ...platform.config import settings
from .access_policy import (
    is_email_allowed_for_domains,
    normalize_allowed_domains,
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/", response_model=list[UserResponse])
def list_team_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.organization_id:
        return []
    return db.query(User).filter(User.organization_id == current_user.organization_id).order_by(User.created_at.asc()).all()


@router.post("/invite", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def invite_team_user(
    data: TeamInviteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
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
        raise HTTPException(status_code=400, detail="Email already exists")

    temp_password = secrets.token_urlsafe(16)
    invited = User(
        email=data.email,
        full_name=data.full_name,
        hashed_password=get_password_hash(temp_password),
        organization_id=current_user.organization_id,
        role="member",
    )
    db.add(invited)
    try:
        db.commit()
        db.refresh(invited)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to invite team member")

    if settings.RESEND_API_KEY:
        email_svc = build_email_adapter()
        reset_link = f"{settings.FRONTEND_URL}/forgot-password"
        try:
            email_svc.send_password_reset(invited.email, reset_link)
        except Exception:
            pass
    return invited


@router.patch("/{user_id}/role", response_model=UserResponse)
def update_team_user_role(
    user_id: int,
    data: TeamRoleUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    if not current_user.organization_id:
        raise HTTPException(status_code=400, detail="You are not in an organization")
    target = (
        db.query(User)
        .filter(User.id == user_id, User.organization_id == current_user.organization_id)
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == data.role:
        return target
    # Never leave the org without an owner. Lock the org's owner rows so two
    # concurrent demotions can't both observe "another owner exists" and
    # commit a zero-owner workspace (FOR UPDATE on Postgres; no-op on SQLite).
    if target.role == "owner" and data.role == "member":
        owners = (
            db.query(User)
            .filter(
                User.organization_id == current_user.organization_id,
                User.role == "owner",
            )
            .with_for_update()
            .all()
        )
        if not any(owner.id != target.id for owner in owners):
            raise HTTPException(status_code=400, detail="An organization needs at least one owner")
    target.role = data.role
    try:
        db.commit()
        db.refresh(target)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update member role")
    return target
