from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta, datetime, timezone
import secrets

from ...core.database import get_db
from ...core.security import verify_password, create_access_token, get_password_hash, get_current_user
from ...core.config import settings
from ...models.user import User
from ...models.organization import Organization
from ...schemas.user import UserCreate, UserResponse, Token, ForgotPasswordRequest, ResetPasswordRequest
from ...services.email_service import EmailService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    """Register a new user and optionally create an organization."""
    existing = db.query(User).filter(User.email == user_in.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create organization if name provided
    org = None
    if user_in.organization_name:
        slug = user_in.organization_name.lower().replace(" ", "-")
        org = Organization(name=user_in.organization_name, slug=slug)
        db.add(org)
        db.flush()  # get org.id before creating user
    
    db_user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        organization_id=org.id if org else None,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Authenticate and return a JWT access token."""
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(
        data={"sub": user.email, "user_id": user.id, "org_id": user.organization_id},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return current_user


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Request a password reset email. Always returns 200 to avoid email enumeration."""
    user = db.query(User).filter(User.email == body.email).first()
    if user and user.is_active:
        raw_token = secrets.token_urlsafe(32)
        user.password_reset_token = get_password_hash(raw_token)
        user.password_reset_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db.commit()
        reset_link = f"{settings.FRONTEND_URL}/#/reset-password?token={raw_token}"
        email_svc = EmailService(api_key=settings.RESEND_API_KEY)
        email_svc.send_password_reset(to_email=user.email, reset_link=reset_link)
    return {"detail": "If an account exists with that email, you will receive a password reset link."}


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Set a new password using the token from the reset email."""
    now = datetime.now(timezone.utc)
    users_with_reset = db.query(User).filter(
        User.password_reset_token != None,
        User.password_reset_expires != None,
        User.password_reset_expires > now,
    ).all()
    user = None
    for u in users_with_reset:
        if verify_password(body.token, u.password_reset_token):
            user = u
            break
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user.hashed_password = get_password_hash(body.new_password)
    user.password_reset_token = None
    user.password_reset_expires = None
    db.commit()
    return {"detail": "Password has been reset. You can now sign in."}
