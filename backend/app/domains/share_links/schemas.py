"""Transport schemas for recruiter and public candidate share links."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateShareLinkPayload(BaseModel):
    mode: str = Field(
        ...,
        description="Share mode: 'recruiter' | 'client' | 'single-view'.",
    )
    expiry: str = Field(
        ...,
        description="Expiry preset key: '24h' | '7d' | '30d' | 'single-view'.",
    )
    view_role_id: int | None = Field(
        default=None,
        ge=1,
        description="Logical role whose candidate standing report is shared.",
    )


class ShareLinkResponse(BaseModel):
    id: int
    application_id: int
    view_role_id: int | None = None
    token: str
    mode: str
    expiry_preset: str | None
    expires_at: str | None
    revoked_at: str | None
    view_count: int
    last_viewed_at: str | None
    created_at: str | None
    active: bool
    revoked: bool
    expired: bool
    single_view_consumed: bool


class ShareLinkListResponse(BaseModel):
    links: list[ShareLinkResponse]


class PublicShareViewResponse(BaseModel):
    """Single-shot response for share-link recipients."""

    application_id: int
    view_role_id: int | None = None
    mode: str
    view: str
    expires_at: str | None
    application: dict[str, Any]


__all__ = [
    "CreateShareLinkPayload",
    "PublicShareViewResponse",
    "ShareLinkListResponse",
    "ShareLinkResponse",
]
