"""Multi-link candidate report share contract.

HANDOFF v2 §3 — replaces "Download PDF" / "Save as PDF" everywhere on
the candidate file. Recruiters mint multiple active links per
application from the share modal; each carries a mode + expiry, and
the report footer renders an "Active links" list with revoke + new-
link controls.

Endpoints:
- ``POST   /api/v1/applications/{application_id}/share-links``
- ``GET    /api/v1/applications/{application_id}/share-links``
- ``DELETE /api/v1/share-links/{link_id}``
- ``GET    /share/{token}``  (public, no auth, gated by expiry +
  view count for single-view links)

The legacy single-link
``/applications/{application_id}/share-link`` (ensure-only,
single-token-on-application) endpoint stays untouched for
back-compat with already-shared URLs; new shares mint a row in
``share_links`` instead.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user, get_optional_current_user
from ...domains.assessments_runtime.role_support import get_application
from ...models.candidate_application import CandidateApplication
from ...models.share_link import (
    SHARE_LINK_MODE_CLIENT,
    SHARE_LINK_MODE_SINGLE_VIEW,
    SHARE_LINK_MODES,
    ShareLink,
)
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(tags=["Share links"])


# Map the share-modal expiry preset → seconds from now. ``single-view``
# stores a 30-day expiry as a hard ceiling but the actual gate is a
# view-count check on first GET.
_EXPIRY_PRESETS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "single-view": timedelta(days=30),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """Round-trip naive datetimes (SQLite default) back to UTC-aware
    so comparisons with ``_utcnow()`` don't raise. Postgres returns
    aware datetimes already, so this is a no-op there.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _generate_token() -> str:
    return f"shr_{secrets.token_urlsafe(24)}"


def _serialize_link(link: ShareLink) -> dict[str, Any]:
    now = _utcnow()
    expires_at = _as_aware(link.expires_at)
    revoked = link.revoked_at is not None
    consumed = link.mode == SHARE_LINK_MODE_SINGLE_VIEW and link.view_count > 0
    expired = expires_at is not None and expires_at <= now
    active = not (revoked or consumed or expired)
    return {
        "id": link.id,
        "application_id": link.application_id,
        "token": link.token,
        "mode": link.mode,
        "expiry_preset": link.expiry_preset,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "revoked_at": link.revoked_at.isoformat() if link.revoked_at else None,
        "view_count": link.view_count,
        "last_viewed_at": link.last_viewed_at.isoformat() if link.last_viewed_at else None,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "active": active,
        "revoked": revoked,
        "expired": expired,
        "single_view_consumed": consumed,
    }


class CreateShareLinkPayload(BaseModel):
    mode: str = Field(
        ...,
        description="Share mode: 'recruiter' | 'client' | 'single-view'.",
    )
    expiry: str = Field(
        ...,
        description="Expiry preset key: '24h' | '7d' | '30d' | 'single-view'.",
    )


class ShareLinkResponse(BaseModel):
    id: int
    application_id: int
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
    application_id: int
    mode: str
    view: str
    expires_at: str | None


@router.post(
    "/applications/{application_id}/share-links",
    response_model=ShareLinkResponse,
)
def create_share_link(
    application_id: int,
    payload: CreateShareLinkPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.mode not in SHARE_LINK_MODES:
        raise HTTPException(status_code=400, detail="Invalid share mode")
    if payload.expiry not in _EXPIRY_PRESETS:
        raise HTTPException(status_code=400, detail="Invalid expiry preset")

    app = get_application(application_id, current_user.organization_id, db)
    expires_at = _utcnow() + _EXPIRY_PRESETS[payload.expiry]

    link = ShareLink(
        organization_id=app.organization_id,
        application_id=app.id,
        created_by_user_id=current_user.id,
        token=_generate_token(),
        mode=payload.mode,
        expiry_preset=payload.expiry,
        expires_at=expires_at,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return _serialize_link(link)


@router.get(
    "/applications/{application_id}/share-links",
    response_model=ShareLinkListResponse,
)
def list_share_links(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all share links for an application — active and inactive.

    The report footer's "Active links" panel uses the ``active`` flag on
    each row to decide what to render; revoked / expired / consumed
    links are surfaced too so recruiters can see audit history.
    """
    # Ensure the recruiter can read this application before exposing
    # links — get_application already raises 404 otherwise.
    app = get_application(application_id, current_user.organization_id, db)
    links = (
        db.query(ShareLink)
        .filter(
            ShareLink.application_id == app.id,
            ShareLink.organization_id == app.organization_id,
        )
        .order_by(ShareLink.created_at.desc())
        .all()
    )
    return {"links": [_serialize_link(link) for link in links]}


@router.delete("/share-links/{link_id}", response_model=ShareLinkResponse)
def revoke_share_link(
    link_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    link = (
        db.query(ShareLink)
        .filter(
            ShareLink.id == link_id,
            ShareLink.organization_id == current_user.organization_id,
        )
        .first()
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Share link not found")
    if link.revoked_at is None:
        link.revoked_at = _utcnow()
        db.commit()
        db.refresh(link)
    return _serialize_link(link)


# Public route — no auth, no /api/v1 prefix. Mounted from main.py at
# the app root so visiting share URLs doesn't require a recruiter
# session. Single-view links short-circuit on first view.
public_router = APIRouter(tags=["Share links (public)"])


@public_router.get("/share/{token}", response_model=PublicShareViewResponse)
def view_share_link(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    link = (
        db.query(ShareLink)
        .filter(ShareLink.token == token)
        .first()
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Share link not found")

    now = _utcnow()
    if link.revoked_at is not None:
        raise HTTPException(status_code=410, detail="Share link has been revoked")
    expires_at = _as_aware(link.expires_at)
    if expires_at is not None and expires_at <= now:
        raise HTTPException(status_code=410, detail="Share link has expired")
    if link.mode == SHARE_LINK_MODE_SINGLE_VIEW and link.view_count > 0:
        raise HTTPException(
            status_code=410,
            detail="Single-view link has already been consumed",
        )

    link.view_count = (link.view_count or 0) + 1
    link.last_viewed_at = now
    db.commit()

    # The actual share view (recruiter / client / single-view) is
    # rendered client-side from CandidateStandingReportPage with the
    # right `mode` flag. We just return the metadata the frontend
    # needs to know which mode to render in.
    view = "client" if link.mode == SHARE_LINK_MODE_CLIENT else "recruiter"
    return {
        "application_id": link.application_id,
        "mode": link.mode,
        "view": view,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
