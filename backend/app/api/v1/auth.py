from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta, datetime, timezone
import logging
import secrets

from ...core.database import get_db
from ...core.security import verify_password, create_access_token, get_password_hash, get_current_user
from ...core.config import settings
from ...models.user import User
from ...models.organization import Organization
from ...schemas.user import UserCreate, UserResponse, Token, ForgotPasswordRequest, ResetPasswordRequest, ResendVerificationRequest
from ...services.email_service import EmailService

logger = logging.getLogger("tali.auth")

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _send_verification_email(user: User) -> None:
    """Send a verification email. Failures are logged but don't block registration."""
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping verification email for %s", user.email)
        return
    try:
        verification_link = f"{settings.FRONTEND_URL}/#/verify-email?token={user.email_verification_token}"
        email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
        email_svc.send_email_verification(
            to_email=user.email,
            full_name=user.full_name or user.email,
            verification_link=verification_link,
        )
    except Exception:
        logger.exception("Failed to send verification email to %s", user.email)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    """Register a new user, create org, and send a verification email."""
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

    verification_token = secrets.token_urlsafe(32)
    db_user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        organization_id=org.id if org else None,
        is_email_verified=False,
        email_verification_token=verification_token,
        email_verification_sent_at=datetime.now(timezone.utc),
    )
    try:
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to register user")

    _send_verification_email(db_user)
    return db_user


@router.get("/verify-email")
def verify_email(token: str = Query(..., min_length=16, max_length=500), db: Session = Depends(get_db)):
    """Verify user email via the token sent during registration."""
    users = db.query(User).filter(
        User.email_verification_token != None,
        User.is_email_verified == False,
    ).all()
    matched_user = None
    for u in users:
        if secrets.compare_digest(u.email_verification_token, token):
            matched_user = u
            break
    if not matched_user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")

    # Check if token is older than 24 hours
    if matched_user.email_verification_sent_at:
        age = datetime.now(timezone.utc) - matched_user.email_verification_sent_at.replace(tzinfo=timezone.utc) if matched_user.email_verification_sent_at.tzinfo is None else datetime.now(timezone.utc) - matched_user.email_verification_sent_at
        if age > timedelta(hours=24):
            raise HTTPException(status_code=400, detail="Verification link has expired. Please request a new one.")

    matched_user.is_email_verified = True
    matched_user.email_verification_token = None
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to verify email")
    return {"detail": "Email verified successfully. You can now sign in."}


@router.post("/resend-verification")
def resend_verification(body: ResendVerificationRequest, db: Session = Depends(get_db)):
    """Resend verification email. Always returns 200 to avoid email enumeration."""
    user = db.query(User).filter(User.email == body.email).first()
    if user and not user.is_email_verified and user.is_active:
        # Rate limit: don't resend if last email was < 60 seconds ago
        if user.email_verification_sent_at:
            sent_at = user.email_verification_sent_at
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - sent_at).total_seconds()
            if age < 60:
                return {"detail": "If an unverified account exists, a new verification email has been sent."}

        new_token = secrets.token_urlsafe(32)
        user.email_verification_token = new_token
        user.email_verification_sent_at = datetime.now(timezone.utc)
        try:
            db.commit()
        except Exception:
            db.rollback()
        _send_verification_email(user)
    return {"detail": "If an unverified account exists, a new verification email has been sent."}


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
    if not user.is_email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address before signing in. Check your inbox for the verification link.",
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
        user.password_reset_token = raw_token
        user.password_reset_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Failed to create reset token")
        reset_link = f"{settings.FRONTEND_URL}/#/reset-password?token={raw_token}"
        email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
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
        if secrets.compare_digest(body.token, u.password_reset_token):
            user = u
            break
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user.hashed_password = get_password_hash(body.new_password)
    user.password_reset_token = None
    user.password_reset_expires = None
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to reset password")
    return {"detail": "Password has been reset. You can now sign in."}
