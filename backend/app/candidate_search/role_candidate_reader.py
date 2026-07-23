"""Shared exact logical-role candidate page reader.

This is the storage boundary behind MCP, every agent chat, autonomous reads,
and public REST.  It returns ORM-backed role-local projections; transports may
shape those rows for their own response schema without rebuilding membership,
state, ATS-link, pending-decision, filtering, or ordering rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ..models.agent_decision import AgentDecision
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from .application_role_scope import (
    application_outcome_expression,
    ats_stage_match_expression,
    pipeline_stage_expression,
    score_expression,
    with_ats_transport,
)
from .role_scope import CandidateRoleScope, resolve_candidate_role_scope


_LIVE_DECISION_STATUSES = (
    "pending",
    "processing",
    "reverted_for_feedback",
)


@dataclass(frozen=True)
class RoleCandidatePage:
    scope: CandidateRoleScope
    source_applications: tuple[CandidateApplication, ...]
    applications: tuple[Any, ...]
    pending_by_application: dict[int, AgentDecision]
    total: int
    limit: int
    offset: int


def read_role_candidate_page(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    score_field: str,
    sort_field: str,
    sort_order: str,
    min_score: float | None,
    pipeline_stage: str | None,
    application_outcome: str | None,
    q: str | None,
    ats_stage: str | None,
    workable_stage: str | None,
    has_pending_decision: bool | None,
    limit: int,
    offset: int,
    limit_ceiling: int,
    prioritize_advanced: bool,
) -> RoleCandidatePage:
    """Read one deterministic page from a role's independent candidate pool."""

    safe_limit = max(1, min(int(limit), int(limit_ceiling)))
    safe_offset = max(0, int(offset))
    scope = resolve_candidate_role_scope(
        db,
        organization_id=int(organization_id),
        role_id=int(role_id),
    )
    query = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(CandidateApplication.organization_id == int(organization_id))
    )
    query = scope.scope_visible_roster(query)
    stage_expr = pipeline_stage_expression(scope)
    outcome_expr = application_outcome_expression(scope)
    if pipeline_stage:
        query = query.filter(stage_expr == pipeline_stage)
    if application_outcome:
        query = query.filter(outcome_expr == application_outcome)
    if min_score is not None:
        query = query.filter(score_expression(scope, score_field) >= min_score)
    if q:
        like = f"%{q.strip()}%"
        query = query.join(
            Candidate,
            CandidateApplication.candidate_id == Candidate.id,
        ).filter(
            or_(
                Candidate.full_name.ilike(like),
                Candidate.email.ilike(like),
                Candidate.position.ilike(like),
            )
        )

    clean_ats_stage = str(ats_stage or "").strip()
    clean_workable_stage = str(workable_stage or "").strip()
    if clean_ats_stage or clean_workable_stage:
        query, transport = with_ats_transport(scope, query)
        if clean_ats_stage:
            query = query.filter(ats_stage_match_expression(transport, clean_ats_stage))
        if clean_workable_stage:
            query = query.filter(transport.workable_stage == clean_workable_stage)

    if has_pending_decision is not None:
        live_decision_exists = (
            db.query(AgentDecision.id)
            .filter(
                AgentDecision.organization_id == int(organization_id),
                AgentDecision.role_id == int(role_id),
                AgentDecision.candidate_id == CandidateApplication.candidate_id,
                AgentDecision.status.in_(_LIVE_DECISION_STATUSES),
            )
            .correlate(CandidateApplication)
            .exists()
        )
        query = query.filter(
            live_decision_exists if has_pending_decision else ~live_decision_exists
        )

    total = int(
        query.order_by(None).with_entities(func.count(CandidateApplication.id)).scalar()
        or 0
    )
    sort_expr = score_expression(scope, sort_field)
    sort_clause = (
        sort_expr.asc().nullsfirst()
        if sort_order == "asc"
        else sort_expr.desc().nullslast()
    )
    order_by = [sort_clause, CandidateApplication.id.desc()]
    if prioritize_advanced:
        is_advanced = func.lower(func.coalesce(stage_expr, "")) == "advanced"
        order_by.insert(0, is_advanced.desc())
    source_applications = tuple(
        query.order_by(*order_by).offset(safe_offset).limit(safe_limit).all()
    )
    evaluations = scope.evaluation_map(
        db,
        application_ids=[int(application.id) for application in source_applications],
    )
    assessment_scores = scope.assessment_score_map(
        db,
        applications=list(source_applications),
    )
    adapter = scope.row_adapter(evaluations, assessment_scores)
    applications = tuple(
        adapter(application) if adapter is not None else application
        for application in source_applications
    )

    pending_by_application: dict[int, AgentDecision] = {}
    if source_applications:
        source_application_by_candidate = {
            int(application.candidate_id): int(application.id)
            for application in source_applications
        }
        pending_rows = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.organization_id == int(organization_id),
                AgentDecision.role_id == int(role_id),
                AgentDecision.candidate_id.in_(source_application_by_candidate),
                AgentDecision.status.in_(_LIVE_DECISION_STATUSES),
            )
            .order_by(
                AgentDecision.candidate_id.asc(),
                AgentDecision.created_at.desc(),
                AgentDecision.id.desc(),
            )
            .all()
        )
        for decision in pending_rows:
            source_application_id = source_application_by_candidate.get(
                int(decision.candidate_id)
            )
            if source_application_id is not None:
                pending_by_application.setdefault(source_application_id, decision)

    return RoleCandidatePage(
        scope=scope,
        source_applications=source_applications,
        applications=applications,
        pending_by_application=pending_by_application,
        total=total,
        limit=safe_limit,
        offset=safe_offset,
    )


__all__ = ["RoleCandidatePage", "read_role_candidate_page"]
