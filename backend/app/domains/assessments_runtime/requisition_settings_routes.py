"""Requisition TEMPLATE-SETTINGS surface — read/write the org's spec template.

Split out of ``requisition_routes`` and re-composed there via
``router.include_router``; the paths/prefix are unchanged.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.user import User
from ...platform.database import get_db
from ...services.requisition_template_service import (
    get_template_for_org,
    set_template_for_org,
)
from .requisition_shared import _org

router = APIRouter(tags=["Requisitions"])


class TemplatePut(BaseModel):
    template: dict[str, Any]


@router.get("/settings/requisition-template")
def get_requisition_template(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The org's requisition spec template (its override, else the built-in
    default)."""
    org = _org(db, current_user.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"template": get_template_for_org(org)}


@router.put("/settings/requisition-template")
def put_requisition_template(
    data: TemplatePut,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate + save the org's requisition spec template."""
    org = _org(db, current_user.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    saved = set_template_for_org(db, org, data.template)
    db.commit()
    return {"template": saved}
