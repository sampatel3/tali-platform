"""Public, no-auth resolver for shareable top-candidate reports.

Mirrors the share-links public route: token in the path, optional-auth (so a
stray 401 doesn't bounce), revoked/expired gating, view-count bump, and the
stored snapshot returned in one round-trip. Mounted at app root (no prefix).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...deps import get_optional_current_user
from ...models.top_candidates_report import TopCandidatesReport
from ...models.user import User
from ...platform.database import get_db
from .service import scrub_public_query, scrub_public_snapshot

public_router = APIRouter(tags=["Top candidate reports"])


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@public_router.get("/report/{token}")
def view_top_report(
    token: str,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    report = (
        db.query(TopCandidatesReport)
        .filter(TopCandidatesReport.token == token)
        .first()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.revoked_at is not None:
        raise HTTPException(status_code=410, detail="Report has been revoked")
    expires_at = _as_aware(report.expires_at)
    now = datetime.now(timezone.utc)
    if expires_at is not None and expires_at <= now:
        raise HTTPException(status_code=410, detail="Report has expired")

    report.view_count = (report.view_count or 0) + 1
    report.last_viewed_at = now
    db.commit()

    return {
        "token": report.token,
        # Re-scrub on read so links minted before the stricter persistence
        # policy cannot leak contact details, credentials, or internal ATS URLs.
        "query": scrub_public_query(report.query),
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "snapshot": scrub_public_snapshot(report.snapshot),
    }
