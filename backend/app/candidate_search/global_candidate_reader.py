"""Deterministic organization-wide search over logical role memberships.

Global agent search cannot treat a physical ``CandidateApplication`` as the
product identity.  One evidence row can belong to an ordinary ATS role and to
one or more independent related roles, each with different score, stage, and
outcome.  This reader pages the canonical logical-membership union and only
then hydrates the physical evidence rows for transport-specific serialization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from .logical_application_scope import resolve_logical_application_selection
from .role_scope import hydrate_logical_candidate_rows


@dataclass(frozen=True)
class GlobalCandidatePage:
    """One stable page containing one row per logical role membership."""

    applications: tuple[Any, ...]
    logical_membership_ids: tuple[str, ...]
    total: int
    limit: int
    offset: int


def read_global_candidate_page(
    db: Session,
    *,
    organization_id: int,
    score_field: str,
    sort_field: str,
    sort_order: str,
    min_score: float | None,
    pipeline_stage: str | None,
    application_outcome: str | None,
    q: str | None,
    limit: int,
    offset: int,
    limit_ceiling: int,
    prioritize_advanced: bool,
) -> GlobalCandidatePage:
    """Read every active role as independent logical candidate memberships.

    Related memberships deliberately survive soft deletion of their source
    evidence row.  Removing the membership itself removes only that logical
    row; an independent ordinary-role membership for the same application is
    unaffected.
    """

    safe_limit = max(1, min(int(limit), int(limit_ceiling)))
    safe_offset = max(0, int(offset))
    selection = resolve_logical_application_selection(
        db,
        organization_id=int(organization_id),
        role_ids=(),
    )
    if not selection.valid_role_ids:
        return GlobalCandidatePage(
            applications=(),
            logical_membership_ids=(),
            total=0,
            limit=safe_limit,
            offset=safe_offset,
        )

    query = selection.apply_membership(
        db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == int(organization_id)
        )
    )
    stage_expression = selection.pipeline_stage_expression()
    outcome_expression = selection.application_outcome_expression()
    if pipeline_stage:
        query = query.filter(stage_expression == pipeline_stage)
    if application_outcome:
        query = query.filter(outcome_expression == application_outcome)
    if min_score is not None:
        query = query.filter(selection.score_expression(score_field) >= min_score)
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

    total = int(
        query.order_by(None).with_entities(func.count(CandidateApplication.id)).scalar()
        or 0
    )
    sort_expression = (
        selection.created_at_expression()
        if sort_field == "created_at"
        else selection.score_expression(sort_field)
    )
    sort_clause = (
        sort_expression.asc().nullsfirst()
        if sort_order == "asc"
        else sort_expression.desc().nullslast()
    )
    logical_role_id = selection.logical_role_id_expression()
    order_by = [
        sort_clause,
        CandidateApplication.id.desc(),
        logical_role_id.desc(),
    ]
    if prioritize_advanced:
        is_advanced = func.lower(func.coalesce(stage_expression, "")) == "advanced"
        order_by.insert(0, is_advanced.desc())
    keys = tuple(
        (int(role_id), int(application_id))
        for application_id, role_id in (
            query.with_entities(CandidateApplication.id, logical_role_id)
            .order_by(*order_by)
            .offset(safe_offset)
            .limit(safe_limit)
            .all()
        )
    )
    if not keys:
        return GlobalCandidatePage(
            applications=(),
            logical_membership_ids=(),
            total=total,
            limit=safe_limit,
            offset=safe_offset,
        )

    applications = hydrate_logical_candidate_rows(
        db,
        selection=selection,
        keys=keys,
    )
    membership_ids = [f"{role_id}:{application_id}" for role_id, application_id in keys]

    return GlobalCandidatePage(
        applications=tuple(applications),
        logical_membership_ids=tuple(membership_ids),
        total=total,
        limit=safe_limit,
        offset=safe_offset,
    )


__all__ = ["GlobalCandidatePage", "read_global_candidate_page"]
