"""Compact, read-only operational views for chat and MCP agents.

These handlers deliberately use column projections and aggregate queries. An
assessment token, raw CV, transcript, repository credential, or other runtime
blob is therefore never loaded as part of these operations, let alone returned
to an agent. Every query is scoped to the authenticated principal's
organization.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, distinct, func, not_, or_
from sqlalchemy.orm import Session

from ..candidate_search.application_role_scope import (
    application_outcome_expression,
    pipeline_stage_expression,
)
from ..candidate_search.role_scope import resolve_candidate_role_scope
from ..components.scoring.assessment_metrics import score_100 as assessment_score_100
from ..models.assessment import Assessment, AssessmentStatus
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from ..models.task import Task
from ..models.user import User
from ..shared.utils import ensure_utc, utcnow
from .urls import (
    assessment_url,
    assessments_url,
    candidates_url,
    home_url,
    role_url,
    roles_url,
)


ASSESSMENT_STATUSES = tuple(status.value for status in AssessmentStatus)
ATTENTION_FILTERS = (
    "any",
    "needs_attention",
    "none",
    "expiring_soon",
    "delivery_failed",
    "scoring_pending",
    "scoring_failed",
)

_COMPLETED_STATUSES = (
    AssessmentStatus.COMPLETED,
    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
)
_DELIVERY_FAILURE_STATUSES = ("bounced", "complained", "failed")
_EXPIRING_SOON_DAYS = 3
_MAX_PAGE_SIZE = 100

_PIPELINE_STAGES = (
    "sourced",
    "applied",
    "invited",
    "in_assessment",
    "review",
    "advanced",
)
_APPLICATION_OUTCOMES = ("open", "rejected", "withdrawn", "hired")


def _organization_id(user: User) -> int:
    """Return a validated organization id from a User or MCP Principal."""
    raw = getattr(user, "organization_id", None)
    try:
        organization_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("authenticated user has no organization") from exc
    if organization_id <= 0:
        raise ValueError("authenticated user has no organization")
    return organization_id


def _optional_positive_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if parsed <= 0 or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _page_value(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field_name} must be an integer")
    if parsed < minimum or (maximum is not None and parsed > maximum):
        if maximum is None:
            raise ValueError(f"{field_name} must be at least {minimum}")
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return parsed


def _normalize_status(status: str | None) -> str | None:
    if status is None:
        return None
    if not isinstance(status, str):
        raise ValueError(f"status must be one of {list(ASSESSMENT_STATUSES)}")
    normalized = status.strip().lower()
    if normalized not in ASSESSMENT_STATUSES:
        raise ValueError(
            f"status must be one of {list(ASSESSMENT_STATUSES)}, got {status!r}"
        )
    return normalized


def _normalize_attention(attention: str) -> str:
    if not isinstance(attention, str):
        raise ValueError(f"attention must be one of {list(ATTENTION_FILTERS)}")
    normalized = attention.strip().lower()
    if normalized not in ATTENTION_FILTERS:
        raise ValueError(
            f"attention must be one of {list(ATTENTION_FILTERS)}, got {attention!r}"
        )
    return normalized


def _attention_expressions(now: datetime) -> dict[str, Any]:
    """Canonical SQL predicates shared by filtering and overview counts."""
    expiring_cutoff = now + timedelta(days=_EXPIRING_SOON_DAYS)
    expiring_soon = and_(
        Assessment.status == AssessmentStatus.PENDING,
        Assessment.expires_at.isnot(None),
        Assessment.expires_at > now,
        Assessment.expires_at <= expiring_cutoff,
    )
    delivery_failed = func.lower(func.coalesce(Assessment.invite_email_status, "")).in_(
        _DELIVERY_FAILURE_STATUSES
    )
    scoring_pending = and_(
        Assessment.status.in_(_COMPLETED_STATUSES),
        Assessment.scored_at.is_(None),
    )
    scoring_failed = Assessment.scoring_failed.is_(True)
    needs_attention = or_(
        expiring_soon,
        delivery_failed,
        scoring_pending,
        scoring_failed,
    )
    return {
        "expiring_soon": expiring_soon,
        "delivery_failed": delivery_failed,
        "scoring_pending": scoring_pending,
        "scoring_failed": scoring_failed,
        "needs_attention": needs_attention,
    }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _status_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _score_100(row: Any) -> float | None:
    """Canonical compact score used by every assessment-facing surface."""
    return assessment_score_100(row)


def _attention_reasons(row: Any, *, now: datetime) -> list[str]:
    status = _status_value(row.status)
    reasons: list[str] = []
    expires_at = ensure_utc(row.expires_at)
    if (
        status == AssessmentStatus.PENDING.value
        and expires_at is not None
        and now < expires_at <= now + timedelta(days=_EXPIRING_SOON_DAYS)
    ):
        reasons.append("expiring_soon")
    if (
        str(row.invite_email_status or "").strip().lower()
        in _DELIVERY_FAILURE_STATUSES
    ):
        reasons.append("delivery_failed")
    if status in {item.value for item in _COMPLETED_STATUSES} and row.scored_at is None:
        reasons.append("scoring_pending")
    if bool(row.scoring_failed):
        reasons.append("scoring_failed")
    return reasons


def _assessment_row(row: Any, *, now: datetime) -> dict[str, Any]:
    reasons = _attention_reasons(row, now=now)
    return {
        "assessment_id": int(row.assessment_id),
        "application_id": row.application_id,
        "candidate_id": row.candidate_id,
        "role_id": row.role_id,
        "task_id": row.task_id,
        "candidate_name": row.candidate_name or row.candidate_email,
        "candidate_email": row.candidate_email,
        "role_name": row.role_name,
        "task_name": row.task_name,
        "status": _status_value(row.status),
        "score_100": _score_100(row),
        "invite_email_status": row.invite_email_status,
        "attention_required": bool(reasons),
        "attention_reasons": reasons,
        "created_at": _isoformat(row.created_at),
        "started_at": _isoformat(row.started_at),
        "completed_at": _isoformat(row.completed_at),
        "expires_at": _isoformat(row.expires_at),
        "scored_at": _isoformat(row.scored_at),
        "frontend_url": assessment_url(
            int(row.assessment_id),
            application_id=row.application_id,
            role_id=row.role_id,
        ),
        "role_url": role_url(int(row.role_id)) if row.role_id is not None else None,
    }


def list_assessments(
    db: Session,
    user: User,
    status: str | None = None,
    role_id: int | None = None,
    attention: str = "any",
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """List a safe, compact page of non-voided assessments.

    ``attention`` accepts broad buckets (``needs_attention`` / ``none``) and
    individual operational conditions. Pagination is deliberately bounded so
    a chat tool cannot accidentally dump an organization's full history into a
    model context window.
    """
    organization_id = _organization_id(user)
    normalized_status = _normalize_status(status)
    normalized_attention = _normalize_attention(attention)
    normalized_role_id = _optional_positive_int(role_id, field_name="role_id")
    normalized_limit = _page_value(
        limit, field_name="limit", minimum=1, maximum=_MAX_PAGE_SIZE
    )
    normalized_offset = _page_value(offset, field_name="offset", minimum=0)

    now = utcnow()
    attention_exprs = _attention_expressions(now)
    filters: list[Any] = [
        Assessment.organization_id == organization_id,
        Assessment.is_voided.is_(False),
    ]
    if normalized_status is not None:
        filters.append(Assessment.status == AssessmentStatus(normalized_status))
    if normalized_role_id is not None:
        filters.append(Assessment.role_id == normalized_role_id)
    if normalized_attention == "needs_attention":
        filters.append(attention_exprs["needs_attention"])
    elif normalized_attention == "none":
        filters.append(not_(attention_exprs["needs_attention"]))
    elif normalized_attention != "any":
        filters.append(attention_exprs[normalized_attention])

    total = int(db.query(func.count(Assessment.id)).filter(*filters).scalar() or 0)

    # Narrow projection is intentional: do not select Assessment.token,
    # Candidate.cv_text, application CVs, transcripts, prompts, git state, or
    # repository fields. Org constraints are repeated in the JOIN predicates so
    # even a corrupted cross-tenant foreign key cannot expose identity data.
    rows = (
        db.query(
            Assessment.id.label("assessment_id"),
            Assessment.application_id.label("application_id"),
            Assessment.candidate_id.label("candidate_id"),
            Assessment.role_id.label("role_id"),
            Assessment.task_id.label("task_id"),
            Assessment.status.label("status"),
            Assessment.score.label("score"),
            Assessment.final_score.label("final_score"),
            Assessment.assessment_score.label("assessment_score"),
            Assessment.taali_score.label("taali_score"),
            Assessment.invite_email_status.label("invite_email_status"),
            Assessment.scoring_failed.label("scoring_failed"),
            Assessment.created_at.label("created_at"),
            Assessment.started_at.label("started_at"),
            Assessment.completed_at.label("completed_at"),
            Assessment.expires_at.label("expires_at"),
            Assessment.scored_at.label("scored_at"),
            Candidate.full_name.label("candidate_name"),
            Candidate.email.label("candidate_email"),
            Role.name.label("role_name"),
            Task.name.label("task_name"),
        )
        .outerjoin(
            Candidate,
            and_(
                Candidate.id == Assessment.candidate_id,
                Candidate.organization_id == organization_id,
                Candidate.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            Role,
            and_(
                Role.id == Assessment.role_id,
                Role.organization_id == organization_id,
                Role.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            Task,
            and_(
                Task.id == Assessment.task_id,
                or_(
                    Task.organization_id == organization_id,
                    Task.organization_id.is_(None),
                ),
            ),
        )
        .filter(*filters)
        .order_by(Assessment.created_at.desc(), Assessment.id.desc())
        .offset(normalized_offset)
        .limit(normalized_limit)
        .all()
    )
    return {
        "items": [_assessment_row(row, now=now) for row in rows],
        "total": total,
        "limit": normalized_limit,
        "offset": normalized_offset,
        "filters": {
            "status": normalized_status,
            "role_id": normalized_role_id,
            "attention": normalized_attention,
        },
        "frontend_url": assessments_url(),
    }


def _count_if(predicate: Any) -> Any:
    return func.coalesce(func.sum(case((predicate, 1), else_=0)), 0)


def _application_aggregate(
    query: Any,
    *,
    id_expression: Any,
    candidate_expression: Any,
    stage_expression: Any,
    outcome_expression: Any,
) -> Any:
    return (
        query.with_entities(
            func.count(id_expression).label("total"),
            func.count(distinct(candidate_expression)).label("candidates"),
            *[
                _count_if(stage_expression == value).label(f"stage_{value}")
                for value in _PIPELINE_STAGES
            ],
            *[
                _count_if(outcome_expression == value).label(f"outcome_{value}")
                for value in _APPLICATION_OUTCOMES
            ],
        )
        .one()
    )


def get_recruiting_overview(
    db: Session,
    user: User,
    role_id: int | None = None,
) -> dict[str, Any]:
    """Return aggregate recruiting health for the org or one role."""
    organization_id = _organization_id(user)
    normalized_role_id = _optional_positive_int(role_id, field_name="role_id")

    scoped_role_name: str | None = None
    if normalized_role_id is not None:
        scoped_role = (
            db.query(Role.id.label("role_id"), Role.name.label("role_name"))
            .filter(
                Role.id == normalized_role_id,
                Role.organization_id == organization_id,
                Role.deleted_at.is_(None),
            )
            .first()
        )
        if scoped_role is None:
            raise ValueError(f"role {normalized_role_id} not found")
        scoped_role_name = scoped_role.role_name
        role_total = 1
    else:
        role_total = int(
            db.query(func.count(Role.id))
            .filter(
                Role.organization_id == organization_id,
                Role.deleted_at.is_(None),
            )
            .scalar()
            or 0
        )

    application_aggregate_rows: list[Any]
    if normalized_role_id is not None:
        application_query = db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == organization_id,
        )
        role_scope = resolve_candidate_role_scope(
            db,
            organization_id=organization_id,
            role_id=normalized_role_id,
        )
        application_query = role_scope.scope_visible_roster(application_query)
        stage_expression = pipeline_stage_expression(role_scope)
        outcome_expression = application_outcome_expression(role_scope)
        application_aggregate_rows = [
            _application_aggregate(
                application_query,
                id_expression=CandidateApplication.id,
                candidate_expression=CandidateApplication.candidate_id,
                stage_expression=stage_expression,
                outcome_expression=outcome_expression,
            )
        ]
    else:
        # Organisation totals count independent logical memberships. A live
        # owner application and a live related membership are two role
        # lifecycles; a direct related application is represented once by its
        # SRE membership rather than again as a physical application row.
        ordinary_query = (
            db.query(CandidateApplication)
            .join(Role, Role.id == CandidateApplication.role_id)
            .filter(
                CandidateApplication.organization_id == organization_id,
                CandidateApplication.deleted_at.is_(None),
                Role.organization_id == organization_id,
                Role.deleted_at.is_(None),
                Role.role_kind != ROLE_KIND_SISTER,
                Role.ats_owner_role_id.is_(None),
            )
        )
        related_query = (
            db.query(SisterRoleEvaluation)
            .join(Role, Role.id == SisterRoleEvaluation.role_id)
            .filter(
                SisterRoleEvaluation.organization_id == organization_id,
                SisterRoleEvaluation.deleted_at.is_(None),
                Role.organization_id == organization_id,
                Role.deleted_at.is_(None),
            )
        )
        application_aggregate_rows = [
            _application_aggregate(
                ordinary_query,
                id_expression=CandidateApplication.id,
                candidate_expression=CandidateApplication.candidate_id,
                stage_expression=CandidateApplication.pipeline_stage,
                outcome_expression=CandidateApplication.application_outcome,
            ),
            _application_aggregate(
                related_query,
                id_expression=SisterRoleEvaluation.id,
                candidate_expression=SisterRoleEvaluation.candidate_id,
                stage_expression=SisterRoleEvaluation.pipeline_stage,
                outcome_expression=SisterRoleEvaluation.application_outcome,
            ),
        ]

    if normalized_role_id is None:
        candidate_total = int(
            db.query(func.count(Candidate.id))
            .filter(
                Candidate.organization_id == organization_id,
                Candidate.deleted_at.is_(None),
            )
            .scalar()
            or 0
        )
    else:
        candidate_total = int(application_aggregate_rows[0].candidates or 0)

    now = utcnow()
    attention_exprs = _attention_expressions(now)
    assessment_filters: list[Any] = [
        Assessment.organization_id == organization_id,
        Assessment.is_voided.is_(False),
    ]
    if normalized_role_id is not None:
        assessment_filters.append(Assessment.role_id == normalized_role_id)
    assessment_aggregates = (
        db.query(
            func.count(Assessment.id).label("total"),
            *[
                _count_if(Assessment.status == item).label(f"status_{item.value}")
                for item in AssessmentStatus
            ],
            _count_if(attention_exprs["needs_attention"]).label("needs_attention"),
            *[
                _count_if(attention_exprs[name]).label(f"attention_{name}")
                for name in (
                    "expiring_soon",
                    "delivery_failed",
                    "scoring_pending",
                    "scoring_failed",
                )
            ],
        )
        .filter(*assessment_filters)
        .one()
    )

    application_total = sum(
        int(row.total or 0) for row in application_aggregate_rows
    )
    pipeline = {
        value: sum(
            int(getattr(row, f"stage_{value}") or 0)
            for row in application_aggregate_rows
        )
        for value in _PIPELINE_STAGES
    }
    outcomes = {
        value: sum(
            int(getattr(row, f"outcome_{value}") or 0)
            for row in application_aggregate_rows
        )
        for value in _APPLICATION_OUTCOMES
    }
    known_stage_total = sum(pipeline.values())
    known_outcome_total = sum(outcomes.values())
    if known_stage_total < application_total:
        pipeline["other"] = application_total - known_stage_total
    if known_outcome_total < application_total:
        outcomes["other"] = application_total - known_outcome_total

    statuses = {
        item.value: int(getattr(assessment_aggregates, f"status_{item.value}") or 0)
        for item in AssessmentStatus
    }
    attention_counts = {
        name: int(getattr(assessment_aggregates, f"attention_{name}") or 0)
        for name in (
            "expiring_soon",
            "delivery_failed",
            "scoring_pending",
            "scoring_failed",
        )
    }

    scoped_frontend_url = (
        role_url(normalized_role_id) if normalized_role_id is not None else home_url()
    )
    links: dict[str, str] = {
        "home": home_url(),
        "roles": roles_url(),
        "candidates": candidates_url(),
        "assessments": assessments_url(),
    }
    if normalized_role_id is not None:
        links["role"] = role_url(normalized_role_id)

    return {
        "scope": {
            "organization_id": organization_id,
            "role_id": normalized_role_id,
            "role_name": scoped_role_name,
        },
        "roles": {"total": role_total},
        "candidates": {"total": candidate_total},
        "applications": {
            "total": application_total,
            "open": outcomes["open"],
            "pipeline_stages": pipeline,
            "outcomes": outcomes,
        },
        "assessments": {
            "total": int(assessment_aggregates.total or 0),
            "statuses": statuses,
            "needs_attention": int(assessment_aggregates.needs_attention or 0),
            "attention": attention_counts,
        },
        "generated_at": now.isoformat(),
        "frontend_url": scoped_frontend_url,
        "links": links,
    }


__all__ = [
    "ASSESSMENT_STATUSES",
    "ATTENTION_FILTERS",
    "get_recruiting_overview",
    "list_assessments",
]
