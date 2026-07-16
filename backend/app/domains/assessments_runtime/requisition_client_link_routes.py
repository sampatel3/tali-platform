"""Requisition CLIENT-LINK surface — mint the scoped, no-login client intake link.

Split out of ``requisition_routes`` and re-composed there via
``router.include_router``; the paths/prefix are unchanged.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.database import get_db
from .job_authorization import JobPermission, require_job_permission
from .requisition_shared import _client_intake_url, _get_brief

router = APIRouter(tags=["Requisitions"])


@router.post("/requisitions/{brief_id}/client-link")
def mint_client_link(
    brief_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mint (or return) the SCOPED, no-login CLIENT INTAKE share link.

    For a consultancy: the recruiter sends this link to their CLIENT, who
    describes the role via the same conversational agent (company/economics
    layers hidden, no pay questions). Idempotent — the token is minted once
    (``secrets.token_urlsafe(8)``) and reused on subsequent calls so a shared
    link never goes stale.
    """
    brief = _get_brief(db, current_user.organization_id, brief_id)
    initial_role_id = int(brief.role_id) if brief.role_id is not None else None
    if initial_role_id is not None:
        require_job_permission(
            db,
            current_user=current_user,
            role_id=initial_role_id,
            permission=JobPermission.EDIT_ROLE,
        )
        brief = (
            db.query(RoleBrief)
            .filter(
                RoleBrief.id == brief_id,
                RoleBrief.organization_id == current_user.organization_id,
            )
            .with_for_update(of=RoleBrief)
            .populate_existing()
            .first()
        )
        if brief is None:
            raise HTTPException(status_code=404, detail="Requisition not found")
        if int(brief.role_id or 0) != initial_role_id:
            raise HTTPException(
                status_code=409,
                detail="The requisition's linked job changed; refresh and retry.",
            )
        if not brief.client_intake_token:
            raise HTTPException(
                status_code=409,
                detail="Client intake cannot be opened after a job is published.",
            )
    else:
        brief = (
            db.query(RoleBrief)
            .filter(
                RoleBrief.id == brief_id,
                RoleBrief.organization_id == current_user.organization_id,
            )
            .with_for_update(of=RoleBrief)
            .populate_existing()
            .first()
        )
        if brief is None:
            raise HTTPException(status_code=404, detail="Requisition not found")
        if brief.role_id is not None:
            raise HTTPException(
                status_code=409,
                detail="The requisition was published; refresh and retry.",
            )
        if (
            getattr(current_user, "role", None) != "owner"
            and int(brief.created_by_user_id or 0) != int(current_user.id)
        ):
            raise HTTPException(status_code=403, detail="Forbidden")
    if not brief.client_intake_token:
        brief.client_intake_token = secrets.token_urlsafe(8)
        db.add(brief)
        db.commit()
        db.refresh(brief)
    token = brief.client_intake_token
    return {"token": token, "url": _client_intake_url(token)}
