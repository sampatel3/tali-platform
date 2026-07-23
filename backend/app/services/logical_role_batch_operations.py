"""Shared logical-role selection rules for recruiter batch operations.

The candidate application is the membership row for an ordinary role.  A
related role instead owns a live ``SisterRoleEvaluation`` membership; its
source and ATS applications are evidence/transport records only.  Keeping the
selection and score-reuse rules here prevents HTTP routes and workers from
quietly reverting to physical ``CandidateApplication.role_id`` checks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..candidate_search.population import apply_searchable_candidate_scope
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from .logical_role_application_authority import (
    LogicalRoleApplicationContext,
    authorize_logical_role_applications,
    list_logical_role_applications,
)

_RELATED_ACTIVE_SCORE_STATUSES = frozenset({"pending", "running", "retry_wait"})


def is_related_role(role: Role) -> bool:
    """Return whether ``role`` owns an independent related-role roster."""

    return bool(
        str(getattr(role, "role_kind", "") or "") == ROLE_KIND_SISTER
        or getattr(role, "ats_owner_role_id", None) is not None
    )


def logical_role_contexts(
    db: Session,
    *,
    role: Role,
    application_ids: Iterable[int] | None = None,
) -> tuple[LogicalRoleApplicationContext, ...]:
    """Resolve either a complete pool or an all-or-nothing explicit selection."""

    if application_ids is None:
        return list_logical_role_applications(db, role=role)
    return authorize_logical_role_applications(
        db,
        role=role,
        application_ids=application_ids,
    )


def parse_applied_after(value: str | None) -> datetime | None:
    if not value:
        return None
    cutoff = datetime.fromisoformat(value)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return cutoff


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _context_applied_at(
    context: LogicalRoleApplicationContext,
) -> datetime | None:
    """Resolve the applied date from this logical membership's application.

    A related membership may keep evidence on one source application while
    linking a different ATS application for the job-specific transport state.
    The validated transport therefore wins when one exists. A direct related
    membership or a rolling-compatibility membership without a usable
    transport uses its already-authorized source application.

    ``Candidate.workable_created_at`` is only the documented compatibility
    fallback for legacy Workable rows that predate the per-application column.
    It must never override a present application date or leak onto a manual
    application for the same deduplicated person.
    """

    application = context.source_application
    if context.is_related and context.ats_application is not None:
        application = context.ats_application

    applied_at = application.workable_created_at
    if (
        applied_at is None
        and str(application.source or "").strip().lower() == "workable"
    ):
        applied_at = context.candidate.workable_created_at
    return _utc_datetime(applied_at)


def filter_contexts_applied_after(
    contexts: Iterable[LogicalRoleApplicationContext],
    *,
    cutoff: datetime | None,
) -> tuple[LogicalRoleApplicationContext, ...]:
    if cutoff is None:
        return tuple(contexts)
    normalized_cutoff = _utc_datetime(cutoff)
    assert normalized_cutoff is not None
    return tuple(
        context
        for context in contexts
        if (applied_at := _context_applied_at(context)) is not None
        and applied_at >= normalized_cutoff
    )


def ordinary_score_targets_query(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    include_scored: bool,
    applied_after: datetime | None,
):
    """Build the canonical ordinary-role batch-scoring population.

    Candidate erasure is absolute even when an application row remains live.
    Application dates remain role-local: prefer the application timestamp, with
    the candidate timestamp only as a compatibility fallback for legacy
    Workable rows.
    """

    query = db.query(CandidateApplication)
    query = apply_searchable_candidate_scope(
        query,
        organization_id=int(organization_id),
    ).filter(
        CandidateApplication.organization_id == int(organization_id),
        CandidateApplication.role_id == int(role_id),
        CandidateApplication.deleted_at.is_(None),
    )
    if not include_scored:
        query = query.filter(CandidateApplication.cv_match_score.is_(None))
    if applied_after is None:
        return query

    query = query.join(
        Candidate,
        Candidate.id == CandidateApplication.candidate_id,
    )
    application_date = case(
        (
            CandidateApplication.workable_created_at.isnot(None),
            CandidateApplication.workable_created_at,
        ),
        (
            func.lower(
                func.trim(func.coalesce(CandidateApplication.source, ""))
            )
            == "workable",
            Candidate.workable_created_at,
        ),
        else_=None,
    )
    return query.filter(application_date >= _utc_datetime(applied_after))


def filter_contexts_stage(
    contexts: Iterable[LogicalRoleApplicationContext],
    *,
    stage: str | None,
) -> tuple[LogicalRoleApplicationContext, ...]:
    """Apply a stage/outcome filter using the acting role's local state."""

    if not stage or stage == "all":
        return tuple(contexts)
    selected: list[LogicalRoleApplicationContext] = []
    for context in contexts:
        subject = context.presented_application
        outcome = str(getattr(subject, "application_outcome", "") or "").lower()
        pipeline_stage = str(getattr(subject, "pipeline_stage", "") or "").lower()
        if stage == "rejected":
            if outcome == "rejected":
                selected.append(context)
        elif outcome == "open" and pipeline_stage == stage:
            selected.append(context)
    return tuple(selected)


def context_has_cv(context: LogicalRoleApplicationContext) -> bool:
    return bool(
        str(context.source_application.cv_text or "").strip()
        or str(context.candidate.cv_text or "").strip()
    )


def context_fetch_transport(
    context: LogicalRoleApplicationContext,
):
    """Return the validated application that may transport an ATS CV fetch."""

    if context.is_related:
        return context.ats_application
    return context.source_application


def related_score_is_reusable(context: LogicalRoleApplicationContext) -> bool:
    evaluation = context.related_evaluation
    if evaluation is None:
        return False
    status = str(evaluation.status or "")
    return bool(
        status in _RELATED_ACTIVE_SCORE_STATUSES
        or (status == "done" and evaluation.role_fit_score is not None)
    )


def related_score_targets(
    contexts: Iterable[LogicalRoleApplicationContext],
    *,
    include_scored: bool,
) -> tuple[LogicalRoleApplicationContext, ...]:
    """Return live, unresolved memberships that need a role-local score."""

    targets: list[LogicalRoleApplicationContext] = []
    for context in contexts:
        evaluation = context.related_evaluation
        if evaluation is None:
            continue
        if str(evaluation.application_outcome or "open").lower() != "open":
            continue
        if str(evaluation.pipeline_stage or "applied").lower() == "advanced":
            continue
        if not include_scored and related_score_is_reusable(context):
            continue
        targets.append(context)
    return tuple(targets)


__all__ = [
    "context_fetch_transport",
    "context_has_cv",
    "filter_contexts_applied_after",
    "filter_contexts_stage",
    "is_related_role",
    "logical_role_contexts",
    "ordinary_score_targets_query",
    "parse_applied_after",
    "related_score_is_reusable",
    "related_score_targets",
]
