from datetime import datetime
from typing import Literal, Optional
from fastapi_users import schemas as fastapi_user_schemas
from pydantic import AliasChoices, BaseModel, EmailStr, Field, computed_field


class UserCreate(fastapi_user_schemas.BaseUserCreate):
    """Shared registration body used by the live FastAPI-Users route."""

    full_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    organization_name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200,
    )


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    is_active: bool
    # The ORM stores this as FastAPI-Users' `is_verified`; keep the public
    # field name but read whichever attribute is present.
    is_email_verified: bool = Field(
        default=False,
        validation_alias=AliasChoices("is_email_verified", "is_verified"),
    )
    # Mirrors the FastAPI-Users ``is_verified`` column; drives ``status``.
    is_verified: bool = False
    organization_id: Optional[int] = None
    role: str = "member"
    created_at: datetime

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def status(self) -> str:
        """Invite lifecycle: an unverified member has a pending invite.

        Owners are never "pending" — they created the workspace or were
        explicitly promoted, so they always read as active even if their
        email was never verified (e.g. legacy accounts predating email
        verification). Only a non-owner who hasn't accepted is "invited"."""
        if self.is_verified or self.role == "owner":
            return "active"
        return "invited"


class TeamInviteResponse(UserResponse):
    """Invite/resend response: the user plus delivery outcome.

    ``email_sent`` defaults so the model can be built from an ORM user via
    ``model_validate`` and the flag set afterward."""

    email_sent: bool = False


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class Token(BaseModel):
    access_token: str
    token_type: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Compatibility representation of FastAPI-Users' generated request body."""

    token: str
    password: str


class TeamInviteRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)


class TeamRoleUpdateRequest(BaseModel):
    role: Literal["owner", "member"]


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=1, max_length=2000)
    # Password rules (min 8 / max 72 bytes) are enforced in the route to
    # return the same 422 shape as FastAPI-Users, so no bounds here.
    password: str


class ResendInviteResponse(BaseModel):
    email_sent: bool


class InviteLinkResponse(BaseModel):
    accept_link: str
