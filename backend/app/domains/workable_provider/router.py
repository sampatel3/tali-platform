"""Workable Assessments-Provider endpoints.

Workable calls these (authenticated with the org's Taali API key, configured in
Workable) to list tests, create assessments, and fetch a candidate link.
Results are pushed back asynchronously via the webhook outbox. Error bodies use
Workable's ``{status, message}`` shape.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ...domains.identity_access.api_key_auth import require_scope
from ...models.api_key import ApiKey, SCOPE_ASSESSMENTS_WRITE, SCOPE_ROLES_READ
from ...models.assessment import Assessment
from ...platform.database import get_db
from ...platform.config import settings
from . import service
from .schemas import (
    CreateAssessmentRequest,
    ProviderTestList,
    SharedLinkResponse,
)

def _require_provider_enabled() -> None:
    if not settings.WORKABLE_PROVIDER_ENABLED:
        raise HTTPException(status_code=503, detail="Workable provider is disabled")


router = APIRouter(
    prefix="/public/v1/integrations/workable",
    tags=["Workable provider"],
    dependencies=[Depends(_require_provider_enabled)],
)


@router.get("/tests", response_model=ProviderTestList)
def list_tests(
    principal: ApiKey = Depends(require_scope(SCOPE_ROLES_READ)),
    db: Session = Depends(get_db),
):
    return {"tests": service.list_provider_tests(db, principal.organization_id)}


@router.post("/assessments")
def create_assessment(
    body: CreateAssessmentRequest,
    principal: ApiKey = Depends(require_scope(SCOPE_ASSESSMENTS_WRITE)),
    db: Session = Depends(get_db),
):
    try:
        assessment = service.provision_assessment(
            db,
            organization_id=principal.organization_id,
            test_id=body.test_id,
            callback_url=body.callback_url,
            candidate=body.candidate,
            job_shortcode=body.job_shortcode,
            job_title=body.job_title,
        )
    except service.ProviderError as exc:
        return JSONResponse(
            status_code=exc.code, content={"status": exc.code, "message": exc.message}
        )
    return JSONResponse(
        status_code=201, content={"assessment_id": str(assessment.id)}
    )


@router.get(
    "/assessments/{assessment_id}/shared-link",
    response_model=SharedLinkResponse,
)
def shared_link(
    assessment_id: int,
    principal: ApiKey = Depends(require_scope(SCOPE_ASSESSMENTS_WRITE)),
    db: Session = Depends(get_db),
):
    a = (
        db.query(Assessment)
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == principal.organization_id,
        )
        .first()
    )
    if a is None:
        return JSONResponse(
            status_code=404, content={"status": 404, "message": "Assessment not found"}
        )
    return {
        "url": service.candidate_link(a),
        "ttl": str(a.duration_minutes or 30),
        "ttl_units": "minutes",
    }
