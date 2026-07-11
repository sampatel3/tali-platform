from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, computed_field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    organization_name: Optional[str] = Field(default=None, max_length=200)


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    is_active: bool
    is_email_verified: bool = False
    # Mirrors the FastAPI-Users ``is_verified`` column; drives ``status``.
    is_verified: bool = False
    organization_id: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def status(self) -> str:
        """Invite lifecycle: an unverified user has a pending invite."""
        return "active" if self.is_verified else "invited"


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
    token: str = Field(min_length=16, max_length=500)
    new_password: str = Field(min_length=8, max_length=200)


class TeamInviteRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=1, max_length=2000)
    # Password rules (min 8 / max 72 bytes) are enforced in the route to
    # return the same 422 shape as FastAPI-Users, so no bounds here.
    password: str


class ResendInviteResponse(BaseModel):
    email_sent: bool
