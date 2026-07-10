"""Requisition PUBLISH surface — snapshot a brief into a shareable PUBLIC job page.

Split out of ``requisition_routes`` and re-composed there via
``router.include_router``; the paths/prefix are unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.role import JOB_STATUS_DRAFT
from ...models.user import User
from ...platform.database import get_db
from ...services.requisition_chat_capture import compute_gaps
from ...services.requisition_template_service import resolve_template
from ...services.role_brief_service import (
    ensure_ref_code,
    materialize_brief_to_role,
    publish_job_page,
)
from .requisition_shared import _get_brief, _job_page_url, _org, _workable_spec

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
    """Publish the brief: stand up an INACTIVE Taali job + a shareable spec.

    Three things, all idempotent and re-publish-safe (the brief is NEVER locked,
    so it stays editable):
      1. Mint-once a ``ref_code`` (the Workable bridge match key).
      2. Create/refresh an inactive ``Role`` (``job_status=draft``) linked to the
         brief and materialize its criteria — the job the recruiter sees in Jobs
         and whose spec they copy into Workable.
      3. Snapshot public-safe fields onto the PUBLIC careers JobPage (one per
         brief; re-publish reuses the token).

    Returns the ref code + the ``workable_spec`` (the FE-rendered JD with the ref
    line appended) so the FE can offer a one-click "Copy for Workable".
    """
    brief = _get_brief(db, current_user.organization_id, brief_id)
    # Enforce the same "required brief fields must be filled" gate the frontend
    # applies — the API is the source of truth, so a direct call can't publish a
    # half-filled requisition that skips the UI guard.
    org = _org(db, current_user.organization_id)
    gaps = compute_gaps(brief, resolve_template(org))
    if gaps:
        labels = [g.get("label") or g.get("key") or "a required field" for g in gaps]
        raise HTTPException(
            status_code=422,
            detail=(
                "This requisition can't be published yet — fill the required fields first: "
                + ", ".join(labels)
            ),
        )
    ref_code = ensure_ref_code(db, brief)
    role = materialize_brief_to_role(
        db, brief, mark_applied=False, job_status=JOB_STATUS_DRAFT
    )
    page = publish_job_page(db, brief, jd_markdown=data.jd_markdown)
    db.commit()
    db.refresh(page)
    return {
        "job_page_id": page.id,
        "token": page.token,
        "url": _job_page_url(page.token),
        "status": page.status,
        "published_at": page.published_at.isoformat() if page.published_at else None,
        "ref_code": ref_code,
        "role_id": role.id,
        "job_status": role.job_status,
        "workable_spec": _workable_spec(data.jd_markdown, ref_code),
    }
