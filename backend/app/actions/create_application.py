"""Create a candidate application against a role.

Both the recruiter UI and the agent invoke this through the same
action. Idempotent on (organization_id, candidate_email, role_id): if
an application already exists for that combination, raises 400.

The action handles candidate dedup: if a Candidate row with the same
email already exists in the org, it is reused (and name/position
updated when supplied); otherwise a new Candidate is created. The
caller is responsible for committing the session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.pipeline_service import (
    apply_legacy_status_update,  # noqa: F401  (left for symmetry; route uses it)
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    map_legacy_status_to_pipeline,
)
from ..domains.assessments_runtime.role_support import (
    get_role,
    refresh_application_score_cache,
    role_has_job_spec,
)
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER
from ..models.sister_role_evaluation import SisterRoleEvaluation
from ..components.assessments.repository import utcnow
from .types import Actor


logger = logging.getLogger("taali.actions.create_application")


@dataclass(frozen=True)
class CreateApplicationResult:
    application_id: int
    candidate_id: int
    status: str  # "created"

    def as_dict(self) -> dict:
        return {
            "application_id": self.application_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
        }


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    role_id: int,
    candidate_email: str,
    candidate_name: Optional[str] = None,
    candidate_position: Optional[str] = None,
    status: Optional[str] = None,
    pipeline_stage: Optional[str] = None,
    application_outcome: Optional[str] = None,
    notes: Optional[str] = None,
) -> CreateApplicationResult:
    role = get_role(role_id, organization_id, db)
    if not role_has_job_spec(role):
        raise HTTPException(
            status_code=400, detail="Upload job spec before adding applications"
        )

    email = (candidate_email or "").strip()
    if not email:
        raise HTTPException(status_code=422, detail="candidate_email is required")

    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.organization_id == organization_id,
            Candidate.email == email,
        )
        .first()
    )
    if candidate is None:
        candidate = Candidate(
            organization_id=organization_id,
            email=email,
            full_name=candidate_name or None,
            position=candidate_position or None,
        )
        db.add(candidate)
        db.flush()
    else:
        if candidate_name:
            candidate.full_name = candidate_name
        if candidate_position:
            candidate.position = candidate_position

    existing = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.role_id == role.id,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=400,
            detail="Candidate already has an application for this role",
        )
    if str(role.role_kind or "") == ROLE_KIND_SISTER:
        existing_membership = (
            db.query(SisterRoleEvaluation.id)
            .filter(
                SisterRoleEvaluation.organization_id == int(organization_id),
                SisterRoleEvaluation.role_id == int(role.id),
                SisterRoleEvaluation.candidate_id == int(candidate.id),
                SisterRoleEvaluation.deleted_at.is_(None),
            )
            .scalar()
        )
        if existing_membership is not None:
            raise HTTPException(
                status_code=400,
                detail="Candidate already belongs to this role's candidate pool",
            )

    mapped_stage, mapped_outcome = map_legacy_status_to_pipeline(status)
    final_pipeline_stage = pipeline_stage or mapped_stage
    final_application_outcome = application_outcome or mapped_outcome
    now = utcnow()
    app = CandidateApplication(
        organization_id=organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status=status or final_pipeline_stage,
        pipeline_stage=final_pipeline_stage,
        pipeline_stage_updated_at=now,
        pipeline_stage_source=actor.type,
        application_outcome=final_application_outcome,
        application_outcome_updated_at=now,
        version=1,
        notes=notes or None,
    )
    db.add(app)
    ensure_pipeline_fields(app, source=actor.type)
    db.flush()
    initialize_pipeline_event_if_missing(
        db,
        app=app,
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason="Application created",
    )
    refresh_application_score_cache(app, db=db)
    if str(role.role_kind or "") == ROLE_KIND_SISTER:
        from ..services.sister_role_service import (
            create_direct_related_membership,
        )

        create_direct_related_membership(
            db,
            role=role,
            application=app,
        )
    return CreateApplicationResult(
        application_id=int(app.id),
        candidate_id=int(candidate.id),
        status="created",
    )
