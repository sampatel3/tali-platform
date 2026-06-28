"""Requisition PUBLISH surface — snapshot a brief into a shareable PUBLIC job page.

Split out of ``requisition_routes`` and re-composed there via
``router.include_router``; the paths/prefix are unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.user import User
from ...platform.database import get_db
from ...services.role_brief_service import publish_job_page
from .requisition_shared import _get_brief, _job_page_url

router = APIRouter(tags=["Requisitions"])


class PublishRequisition(BaseModel):
    jd_markdown: str = ""


@router.post("/requisitions/{brief_id}/publish")
def publish_requisition(
    brief_id: int,
    data: PublishRequisition,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Publish the brief as a shareable PUBLIC job page.

    Takes the FE-rendered ``jd_markdown`` and snapshots the brief's public-safe
    fields onto a JobPage (idempotent — one per brief; re-publish refreshes it
    and reuses the token). Does NOT materialize an internal role and does NOT
    change the brief's status, so the brief stays editable for a re-publish.
    """
    brief = _get_brief(db, current_user.organization_id, brief_id)
    page = publish_job_page(db, brief, jd_markdown=data.jd_markdown)
    db.commit()
    db.refresh(page)
    return {
        "job_page_id": page.id,
        "token": page.token,
        "url": _job_page_url(page.token),
        "status": page.status,
        "published_at": page.published_at.isoformat() if page.published_at else None,
    }
