"""
FastAPI-Users configuration: user manager, auth backend, schemas, Resend hooks.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin, InvalidPasswordException
from fastapi_users import exceptions as fu_exceptions
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.jwt import generate_jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, case, null, select, update as sa_update

from ...models.auth_event import (
    AUTH_EVENT_ACCOUNT_LOCKED,
    AUTH_EVENT_LOGIN_FAILED,
    AUTH_EVENT_LOGIN_SUCCESS,
    AUTH_EVENT_PASSWORD_RESET_COMPLETED,
    AUTH_EVENT_PASSWORD_RESET_REQUESTED,
)
from ...models.billing_credit_ledger import BillingCreditLedger
from ...models.user import User
from ...models.organization import Organization
from ...models.usage_grant import GRANT_FREE_TIER, UsageGrant
from ...platform.config import settings
from ...platform.database import get_async_db
from ...domains.integrations_notifications.adapters import build_email_adapter
from ...services.pricing_service import FREE_TIER
from ...schemas.user import UserCreate as SharedUserCreate
from .auth_events import record_auth_event_async
from .password_policy import check_password_strength

logger = logging.getLogger("taali.auth")


# ---- Schemas (extend FastAPI-Users base) ----
from fastapi_users import schemas


class UserRead(schemas.BaseUser[int]):
    full_name: Optional[str] = None
    organization_id: Optional[int] = None
    role: str = "member"
    created_at: Optional[datetime] = None  # serializes to ISO string in JSON

    model_config = {"from_attributes": True}


class UserCreate(SharedUserCreate):
    """Runtime registration schema, anchored to the shared public contract."""


class UserUpdate(schemas.BaseUserUpdate):
    full_name: Optional[str] = None


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Postgres returns tz-aware datetimes, SQLite (tests) naive UTC — normalize."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---- User Manager ----
class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    reset_password_token_secret = settings.SECRET_KEY
    verification_token_secret = settings.SECRET_KEY
    reset_password_token_lifetime_seconds = 3600
    verification_token_lifetime_seconds = 86400  # 24 hours

    async def validate_password(self, password: str, user) -> None:
        # FastAPI-Users passes the user / UserCreate as the second arg on both
        # register and reset-password. Use its email for the similarity check.
        email = getattr(user, "email", None)
        reason = check_password_strength(password, email=email)
        if reason is not None:
            raise InvalidPasswordException(reason=reason)

    async def authenticate(self, credentials: OAuth2PasswordRequestForm) -> Optional[User]:
        """Base authenticate + per-account lockout + audit trail.

        Lockout: AUTH_LOCKOUT_THRESHOLD consecutive failures locks the account
        for AUTH_LOCKOUT_MINUTES (429, checked BEFORE password verification so
        a locked account can't be brute-forced during the window). Counter
        resets on successful login or first failure after the lock expires.
        """
        session: AsyncSession = self.user_db.session
        try:
            user = await self.get_by_email(credentials.username)
        except fu_exceptions.UserNotExists:
            # Run the hasher to mitigate timing attack (same as base class)
            self.password_helper.hash(credentials.password)
            await record_auth_event_async(
                session,
                AUTH_EVENT_LOGIN_FAILED,
                email=credentials.username,
                metadata={"reason": "unknown_email"},
            )
            return None

        now = datetime.now(timezone.utc)
        locked_until = _as_utc(user.locked_until)
        if locked_until and locked_until > now:
            remaining_min = max(1, int((locked_until - now).total_seconds() // 60) + 1)
            await record_auth_event_async(
                session,
                AUTH_EVENT_LOGIN_FAILED,
                user_id=user.id,
                organization_id=user.organization_id,
                email=user.email,
                metadata={"reason": "account_locked"},
            )
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed login attempts. Try again in {remaining_min} minute(s).",
            )

        verified, updated_password_hash = self.password_helper.verify_and_update(
            credentials.password, user.hashed_password
        )
        if not verified:
            # ONE atomic statement — concurrent bad logins must not lose
            # counts to a read-modify-write race, in either branch: an
            # expired lock starts a fresh count of 1 (and clears the lock);
            # otherwise increment, setting the lock in the same statement
            # the moment the threshold is hit.
            lock_at = now + timedelta(minutes=settings.AUTH_LOCKOUT_MINUTES)
            expired_lock = and_(User.locked_until.is_not(None), User.locked_until <= now)
            result = await session.execute(
                sa_update(User)
                .where(User.id == user.id)
                .values(
                    failed_login_attempts=case(
                        (expired_lock, 1),
                        else_=User.failed_login_attempts + 1,
                    ),
                    locked_until=case(
                        (expired_lock, null()),
                        (
                            User.failed_login_attempts + 1
                            >= settings.AUTH_LOCKOUT_THRESHOLD,
                            lock_at,
                        ),
                        else_=User.locked_until,
                    ),
                )
                .returning(User.failed_login_attempts)
            )
            attempts = result.scalar_one()
            await session.commit()
            event_type = AUTH_EVENT_LOGIN_FAILED
            metadata = {"reason": "bad_password", "failed_attempts": attempts}
            if attempts >= settings.AUTH_LOCKOUT_THRESHOLD:
                event_type = AUTH_EVENT_ACCOUNT_LOCKED
                metadata["lock_minutes"] = settings.AUTH_LOCKOUT_MINUTES
            await record_auth_event_async(
                session,
                event_type,
                user_id=user.id,
                organization_id=user.organization_id,
                email=user.email,
                metadata=metadata,
            )
            return None

        update_dict = {}
        if updated_password_hash is not None:
            update_dict["hashed_password"] = updated_password_hash
        if user.failed_login_attempts or user.locked_until is not None:
            update_dict["failed_login_attempts"] = 0
            update_dict["locked_until"] = None
        if update_dict:
            await self.user_db.update(user, update_dict)
        return user

    async def on_after_login(
        self,
        user: User,
        request: Optional[Request] = None,
        response=None,
    ) -> None:
        await record_auth_event_async(
            self.user_db.session,
            AUTH_EVENT_LOGIN_SUCCESS,
            user_id=user.id,
            organization_id=user.organization_id,
            email=user.email,
        )

    async def _create_signup_org(self, session: AsyncSession, organization_name: str) -> Organization:
        """Always create a fresh organization for self-signup with a unique slug."""
        base_slug = re.sub(r"[^a-z0-9]+", "-", organization_name.lower()).strip("-") or "organization"
        slug = base_slug
        suffix = 2

        while True:
            existing = await session.execute(select(Organization.id).where(Organization.slug == slug))
            if existing.scalar_one_or_none() is None:
                break
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        org = Organization(name=organization_name, slug=slug)
        # Free-tier credit grant on signup. Idempotent on external_ref so a
        # retry can't double-grant. The grant + ledger entry write together
        # in this transaction with the org create — if the user creation
        # fails downstream, the grant rolls back too.
        org.credits_balance = FREE_TIER.credits
        session.add(org)
        await session.flush()

        external_ref = f"free_tier:{org.id}"
        grant = UsageGrant(
            organization_id=org.id,
            grant_type=GRANT_FREE_TIER,
            credits_granted=FREE_TIER.credits,
            external_ref=external_ref,
        )
        ledger = BillingCreditLedger(
            organization_id=org.id,
            delta=FREE_TIER.credits,
            balance_after=FREE_TIER.credits,
            reason=f"grant:{GRANT_FREE_TIER}",
            external_ref=external_ref,
        )
        session.add(grant)
        session.add(ledger)
        await session.flush()
        return org

    async def create(self, user_create, safe: bool = False, request: Optional[Request] = None) -> User:
        await self.validate_password(user_create.password, user_create)

        existing_user = await self.user_db.get_by_email(user_create.email)
        if existing_user is not None:
            from fastapi_users import exceptions

            raise exceptions.UserAlreadyExists()

        user_dict = (
            user_create.create_update_dict()
            if safe
            else user_create.create_update_dict_superuser()
        )
        password = user_dict.pop("password")
        user_dict["hashed_password"] = self.password_helper.hash(password)

        organization_name = getattr(user_create, "organization_name", None) or user_dict.pop("organization_name", None)
        full_name = getattr(user_create, "full_name", None) or user_dict.get("full_name")
        user_dict["full_name"] = full_name

        org_id = None
        if organization_name:
            session: AsyncSession = self.user_db.session
            org = await self._create_signup_org(session, organization_name.strip())
            org_id = org.id
        user_dict["organization_id"] = org_id
        # Whoever creates the org at signup owns it; everyone else joins as member.
        user_dict["role"] = "owner" if org_id else "member"
        # Remove any field not on User model (e.g. organization_name) before create
        user_dict.pop("organization_name", None)

        created_user = await self.user_db.create(user_dict)
        await self.on_after_register(created_user, request)
        return created_user

    async def on_after_register(self, user: User, request: Optional[Request] = None) -> None:
        if not settings.RESEND_API_KEY:
            logger.warning(
                "RESEND_API_KEY not set; skipping verification email user_id=%s",
                user.id,
            )
            return
        try:
            token_data = {"sub": str(user.id), "email": user.email, "aud": self.verification_token_audience}
            from fastapi_users.jwt import generate_jwt

            token = generate_jwt(
                token_data,
                self.verification_token_secret,
                self.verification_token_lifetime_seconds,
            )
            verification_link = f"{settings.FRONTEND_URL}/verify-email?token={token}"
            email_svc = build_email_adapter()
            email_svc.send_email_verification(
                to_email=user.email,
                full_name=user.full_name or user.email,
                verification_link=verification_link,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send verification email user_id=%s error_type=%s",
                user.id,
                type(exc).__name__,
            )

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        await record_auth_event_async(
            self.user_db.session,
            AUTH_EVENT_PASSWORD_RESET_REQUESTED,
            user_id=user.id,
            organization_id=user.organization_id,
            email=user.email,
        )
        key = (settings.RESEND_API_KEY or "").strip()
        if not key or key.lower() == "skip":
            logger.warning(
                "RESEND_API_KEY not set or disabled; not sending password reset "
                "email user_id=%s",
                user.id,
            )
            return
        try:
            reset_link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
            logger.info("Sending password reset email user_id=%s", user.id)
            email_svc = build_email_adapter()
            result = email_svc.send_password_reset(to_email=user.email, reset_link=reset_link)
            if not result.get("success"):
                logger.error(
                    "Resend rejected password reset email user_id=%s",
                    user.id,
                )
        except Exception as exc:
            logger.warning(
                "Failed to send password reset email user_id=%s error_type=%s",
                user.id,
                type(exc).__name__,
            )

    async def on_after_reset_password(self, user: User, request: Optional[Request] = None) -> None:
        # A completed reset clears any active lockout (the user just proved
        # account ownership via the emailed token) and stamps
        # password_changed_at so pre-reset tokens can no longer refresh.
        await self.user_db.update(
            user,
            {
                "failed_login_attempts": 0,
                "locked_until": None,
                "password_changed_at": datetime.now(timezone.utc),
            },
        )
        await record_auth_event_async(
            self.user_db.session,
            AUTH_EVENT_PASSWORD_RESET_COMPLETED,
            user_id=user.id,
            organization_id=user.organization_id,
            email=user.email,
        )

    async def on_after_update(
        self, user: User, update_dict: dict, request: Optional[Request] = None
    ) -> None:
        # Password changed via the users router (PATCH /users/me or admin
        # update): stamp the revocation anchor like a reset does.
        if "password" in update_dict or "hashed_password" in update_dict:
            await self.user_db.update(
                user, {"password_changed_at": datetime.now(timezone.utc)}
            )

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        if not settings.RESEND_API_KEY:
            logger.warning(
                "RESEND_API_KEY not set; skipping verification email user_id=%s",
                user.id,
            )
            return
        try:
            verification_link = f"{settings.FRONTEND_URL}/verify-email?token={token}"
            email_svc = build_email_adapter()
            email_svc.send_email_verification(
                to_email=user.email,
                full_name=user.full_name or user.email,
                verification_link=verification_link,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send verification email user_id=%s error_type=%s",
                user.id,
                type(exc).__name__,
            )


async def get_user_db(session: AsyncSession = Depends(get_async_db)):
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


# ---- Auth Backend ----
bearer_transport = BearerTransport(tokenUrl="/api/v1/auth/jwt/login")


class IssuedAtJWTStrategy(JWTStrategy):
    """JWTStrategy that also stamps ``iat``, so /auth/jwt/refresh can refuse
    to slide tokens minted before the user's last password change."""

    async def write_token(self, user) -> str:
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            "iat": int(datetime.now(timezone.utc).timestamp()),
        }
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm
        )


def get_jwt_strategy() -> IssuedAtJWTStrategy:
    return IssuedAtJWTStrategy(
        secret=settings.SECRET_KEY,
        lifetime_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
current_active_user_optional = fastapi_users.current_user(active=True, optional=True)
