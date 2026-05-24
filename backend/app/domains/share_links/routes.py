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
    """Single-shot response for share-link recipients.

    Returns the full application detail payload (scrubbed when the link's
    mode is ``client``) so the SPA can render the standing report in one
    round-trip — no separate auth-bypassed application fetch needed.

    Metadata fields (``application_id``, ``mode``, ``view``, ``expires_at``)
    are kept top-level for the test surface and so the SPA can inspect
    them without unpacking ``application``.
    """

    application_id: int
    mode: str
    view: str
    expires_at: str | None
    application: dict[str, Any]


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


def _recruiter_notes_timeline(
    db: Session,
    app: CandidateApplication,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Recruiter notes for the share view's "Hiring team notes" column.

    Notes are appended to ``assessment.timeline`` by
    ``POST /assessments/{id}/notes`` (not the application_events table), so we
    resolve the same assessment the recruiter detail view uses and return its
    note-type entries. Mirrors ``resolveAssessmentId`` on the frontend.
    """
    from ...models.assessment import Assessment

    score_summary = payload.get("score_summary")
    assessment_id = None
    if isinstance(score_summary, dict):
        assessment_id = score_summary.get("assessment_id")
    assessment_id = assessment_id or payload.get("valid_assessment_id")
    if not assessment_id:
        return []

    assessment = (
        db.query(Assessment)
        .filter(
            Assessment.id == int(assessment_id),
            Assessment.organization_id == app.organization_id,
        )
        .first()
    )
    timeline = assessment.timeline if assessment and isinstance(assessment.timeline, list) else []
    notes: list[dict[str, Any]] = []
    for entry in timeline:
        if not isinstance(entry, dict):
            continue
        event_type = str(entry.get("event_type") or entry.get("type") or "").lower()
        if event_type not in ("note", "recruiter_note"):
            continue
        if not str(entry.get("text") or entry.get("prompt") or "").strip():
            continue
        notes.append(entry)
    return notes


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

    view = "client" if link.mode == SHARE_LINK_MODE_CLIENT else "recruiter"

    # Resolve the application with the same joinedload set the recruiter
    # detail view uses, then serialize via application_detail_payload so
    # the SPA gets every field it normally renders. ``client_safe=True``
    # scrubs recruiter notes / transcripts and switches in the client
    # share summary, matching the rendering rules in CandidateStandingReportPage.
    from ..assessments_runtime.applications_routes import _load_application_for_detail
    from ..assessments_runtime.role_support import application_detail_payload

    app = _load_application_for_detail(db=db, application_id=link.application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Candidate report unavailable.")
    application_payload = application_detail_payload(
        app,
        include_cv_text=False,
        client_safe=(view == "client"),
    )

    # Recruiter shares are the "full report" — surface the same recruiter
    # notes + audit timeline the authenticated detail view fetches via
    # auth-only endpoints (assessment timeline + /events), which the unauth
    # share page can't call itself. Client shares stay scrubbed.
    if view == "recruiter":
        from ..assessments_runtime.pipeline_service import list_application_events

        application_payload["application_events"] = list_application_events(
            db,
            organization_id=app.organization_id,
            application_id=app.id,
            limit=100,
        )
        application_payload["recruiter_notes_timeline"] = _recruiter_notes_timeline(
            db, app, application_payload
        )

    return {
        "application_id": link.application_id,
        "mode": link.mode,
        "view": view,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "application": application_payload,
    }
