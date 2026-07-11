from datetime import datetime
from typing import Literal, Optional
from pydantic import AliasChoices, BaseModel, EmailStr, Field


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
    # The ORM stores this as FastAPI-Users' `is_verified`; keep the public
    # field name but read whichever attribute is present.
    is_email_verified: bool = Field(
        default=False,
        validation_alias=AliasChoices("is_email_verified", "is_verified"),
    )
    organization_id: Optional[int] = None
    role: str = "member"
    created_at: datetime

    model_config = {"from_attributes": True}


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


class TeamRoleUpdateRequest(BaseModel):
    role: Literal["owner", "member"]
