from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


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
    organization_id: Optional[int] = None
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
