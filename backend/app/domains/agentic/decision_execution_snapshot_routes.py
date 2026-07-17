"""Cheap, complete polling projection for role-pipeline decision controls."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...domains.assessments_runtime.role_support import (
    role_family_response,
    roles_with_families,
)
from ...models.agent_decision import AgentDecision
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.user import User
from ...platform.database import get_db
from ...schemas.role import RoleFamilyResponse
from .decision_payload_support import workable_stage_job_id
from .decision_query_pagination import apply_before_cursor

router = APIRouter(tags=["agentic"])


class DecisionExecutionSnapshot(BaseModel):
    """Only the authority and display fields needed by the Job Pipeline poll."""

    id: int
    role_id: int
    application_id: int
    decision_type: str
    recommendation: str
    status: str
    created_at: datetime
    candidate_name: Optional[str] = None
    role_family: Optional[RoleFamilyResponse] = None
    workable_job_id: Optional[str] = None
    workable_stage: Optional[str] = None


@router.get(
    "/agent-decisions/execution-snapshots",
    response_model=list[DecisionExecutionSnapshot],
)
def list_decision_execution_snapshots(
    role_id: int = Query(ge=1),
    before_created_at: Optional[datetime] = Query(default=None),
    before_id: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=500, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List every live role decision without hydrating audit/explanation JSON."""

    organization_id = int(current_user.organization_id)
    query = (
        db.query(
            AgentDecision.id.label("id"),
            AgentDecision.role_id.label("role_id"),
            AgentDecision.application_id.label("application_id"),
            AgentDecision.decision_type.label("decision_type"),
            AgentDecision.recommendation.label("recommendation"),
            AgentDecision.status.label("status"),
            AgentDecision.created_at.label("created_at"),
            Candidate.full_name.label("candidate_name"),
            CandidateApplication.workable_candidate_id.label("workable_candidate_id"),
            CandidateApplication.workable_stage.label("workable_stage"),
        )
        .join(
            CandidateApplication,
            and_(
                CandidateApplication.id == AgentDecision.application_id,
                CandidateApplication.organization_id == organization_id,
            ),
        )
        .outerjoin(
            Candidate,
            and_(
                Candidate.id == CandidateApplication.candidate_id,
                Candidate.organization_id == organization_id,
            ),
        )
        .filter(
            AgentDecision.organization_id == organization_id,
            AgentDecision.role_id == int(role_id),
            AgentDecision.status.in_(("pending", "processing")),
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= datetime.now(timezone.utc),
            ),
        )
    )
    query = apply_before_cursor(
        query,
        before_created_at=before_created_at,
        before_id=before_id,
        status="pending",
    )
    rows = query.order_by(desc(AgentDecision.created_at), desc(AgentDecision.id)).limit(limit).all()
    family_roles = roles_with_families(
        db,
        [int(row.role_id) for row in rows],
        organization_id=organization_id,
    )
    family_payloads = {
        role_id: role_family_response(role) for role_id, role in family_roles.items()
    }
    return [
        DecisionExecutionSnapshot(
            id=int(row.id),
            role_id=int(row.role_id),
            application_id=int(row.application_id),
            decision_type=str(row.decision_type),
            recommendation=str(row.recommendation),
            status=str(row.status),
            created_at=row.created_at,
            candidate_name=row.candidate_name,
            role_family=family_payloads.get(int(row.role_id)),
            workable_job_id=workable_stage_job_id(
                family_roles.get(int(row.role_id)), row
            ),
            workable_stage=row.workable_stage,
        )
        for row in rows
    ]


__all__ = ["DecisionExecutionSnapshot", "router"]
