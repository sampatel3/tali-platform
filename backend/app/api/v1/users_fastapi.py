"""
FastAPI-Users configuration: user manager, auth backend, schemas, Resend hooks.
"""

import logging
from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin, InvalidPasswordException
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...models.organization import Organization
from ...platform.config import settings
from ...platform.database import get_async_db
from ...components.notifications.email_client import EmailService

logger = logging.getLogger("tali.auth")


# ---- Schemas (extend FastAPI-Users base) ----
from fastapi_users import schemas


class UserRead(schemas.BaseUser[int]):
    full_name: Optional[str] = None
    organization_id: Optional[int] = None
    created_at: Optional[str] = None


class UserCreate(schemas.BaseUserCreate):
    full_name: Optional[str] = None
    organization_name: Optional[str] = None


class UserUpdate(schemas.BaseUserUpdate):
    full_name: Optional[str] = None


# ---- User Manager ----
class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    reset_password_token_secret = settings.SECRET_KEY
    verification_token_secret = settings.SECRET_KEY
    reset_password_token_lifetime_seconds = 3600
    verification_token_lifetime_seconds = 86400  # 24 hours

    async def validate_password(self, password: str, user) -> None:
        if len(password) < 8:
            raise InvalidPasswordException(reason="Password should be at least 8 characters")

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
            slug = organization_name.lower().replace(" ", "-")
            from sqlalchemy import select

            result = await session.execute(select(Organization).where(Organization.slug == slug))
            org = result.scalar_one_or_none()
            if not org:
                org = Organization(name=organization_name, slug=slug)
                session.add(org)
                await session.flush()
            org_id = org.id
        user_dict["organization_id"] = org_id

        created_user = await self.user_db.create(user_dict)
        await self.on_after_register(created_user, request)
        return created_user

    async def on_after_register(self, user: User, request: Optional[Request] = None) -> None:
        if not settings.RESEND_API_KEY:
            logger.warning("RESEND_API_KEY not set — skipping verification email for %s", user.email)
            return
        try:
            token_data = {"sub": str(user.id), "email": user.email, "aud": self.verification_token_audience}
            from fastapi_users.jwt import generate_jwt

            token = generate_jwt(
                token_data,
                self.verification_token_secret,
                self.verification_token_lifetime_seconds,
            )
            verification_link = f"{settings.FRONTEND_URL}/#/verify-email?token={token}"
            email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
            email_svc.send_email_verification(
                to_email=user.email,
                full_name=user.full_name or user.email,
                verification_link=verification_link,
            )
        except Exception:
            logger.exception("Failed to send verification email to %s", user.email)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        key = (settings.RESEND_API_KEY or "").strip()
        if not key or key.lower() == "skip":
            logger.warning("RESEND_API_KEY not set or 'skip' — not sending password reset email to %s", user.email)
            return
        try:
            reset_link = f"{settings.FRONTEND_URL}/#/reset-password?token={token}"
            logger.info("Sending password reset email to %s (FRONTEND_URL=%s)", user.email, settings.FRONTEND_URL)
            email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
            result = email_svc.send_password_reset(to_email=user.email, reset_link=reset_link)
            if not result.get("success"):
                logger.error("Resend rejected password reset email for %s — check Resend dashboard and domain verification", user.email)
        except Exception:
            logger.exception("Failed to send password reset email to %s", user.email)

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        if not settings.RESEND_API_KEY:
            logger.warning("RESEND_API_KEY not set — skipping verification email for %s", user.email)
            return
        try:
            verification_link = f"{settings.FRONTEND_URL}/#/verify-email?token={token}"
            email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
            email_svc.send_email_verification(
                to_email=user.email,
                full_name=user.full_name or user.email,
                verification_link=verification_link,
            )
        except Exception:
            logger.exception("Failed to send verification email to %s", user.email)


async def get_user_db(session: AsyncSession = Depends(get_async_db)):
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


# ---- Auth Backend ----
bearer_transport = BearerTransport(tokenUrl="/api/v1/auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
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
