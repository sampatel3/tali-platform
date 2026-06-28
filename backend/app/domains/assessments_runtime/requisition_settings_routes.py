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


class CompanyBlurbPut(BaseModel):
    company_blurb: str = ""


@router.get("/settings/requisition-template")
def get_requisition_template(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The org's requisition spec template (its override, else the built-in
    default), plus the standardised "About the company" blurb — the role-agnostic
    boilerplate reused on every job spec (auto-derived once, editable here)."""
    org = _org(db, current_user.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {
        "template": get_template_for_org(org),
        "company_blurb": getattr(org, "company_blurb", None) or "",
    }


@router.put("/settings/requisition-template/company-blurb")
def put_company_blurb(
    data: CompanyBlurbPut,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save the org's "About the company" blurb (recruiter edit)."""
    org = _org(db, current_user.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    org.company_blurb = (data.company_blurb or "").strip()
    db.commit()
    return {"company_blurb": org.company_blurb}


@router.post("/settings/requisition-template/company-blurb/generate")
def generate_company_blurb_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """(Re)generate the "About the company" blurb from the org's recent role
    specs (one cheap LLM pass). Clears the cache first so it always re-derives.
    Returns the blurb (``""`` when nothing could be derived yet)."""
    from ...services.requisition_chat_service import derive_company_blurb

    org = _org(db, current_user.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    org.company_blurb = None  # clear the cache so derive recomputes
    db.flush()
    blurb = derive_company_blurb(db, current_user.organization_id)
    db.commit()
    return {"company_blurb": blurb or ""}


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
