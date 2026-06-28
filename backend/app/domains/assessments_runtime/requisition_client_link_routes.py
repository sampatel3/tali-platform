"""Requisition CLIENT-LINK surface — mint the scoped, no-login client intake link.

Split out of ``requisition_routes`` and re-composed there via
``router.include_router``; the paths/prefix are unchanged.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.user import User
from ...platform.database import get_db
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
    if not brief.client_intake_token:
        brief.client_intake_token = secrets.token_urlsafe(8)
        db.add(brief)
        db.commit()
        db.refresh(brief)
    token = brief.client_intake_token
    return {"token": token, "url": _client_intake_url(token)}
