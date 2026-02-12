from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import secrets

from ...platform.database import get_db
from ...deps import get_current_user
from ...platform.security import get_password_hash
from ...models.user import User
from ...schemas.user import UserResponse, TeamInviteRequest
from ...services.email_service import EmailService
from ...platform.config import settings

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
    current_user: User = Depends(get_current_user),
):
    if not current_user.organization_id:
        raise HTTPException(status_code=400, detail="You are not in an organization")
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")

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

    if settings.RESEND_API_KEY:
        email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
        reset_link = f"{settings.FRONTEND_URL}/#/forgot-password"
        try:
            email_svc.send_password_reset(invited.email, reset_link)
        except Exception:
            pass
    return invited
