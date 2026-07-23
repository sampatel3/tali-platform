"""Pure-function handlers behind every MCP tool.

Each handler takes ``(db: Session, user: User, **args) -> dict | list``
and is fully self-contained — no Context, no Starlette request. The MCP
tool decorators in ``server.py`` resolve auth then delegate here, and the
in-process copilot orchestrator (``app/copilot/...``) calls the same
functions directly with the User it already authenticated.

Org-scoping is enforced inside every handler via ``user.organization_id``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from ..candidate_search.application_role_scope import (
    application_outcome_expression,
    build_role_local_projection,
    pipeline_stage_expression,
    scope_with_evaluations,
    score_expression,
    strip_owner_role_judgments,
)
from ..candidate_search.global_candidate_reader import read_global_candidate_page
from ..candidate_search.logical_policy_state import (
    read_logical_candidate_policy_states,
)
from ..candidate_search.logical_application_scope import (
    resolve_logical_application_selection,
)
from ..candidate_search.population import apply_searchable_candidate_scope
from ..candidate_search.role_assessment_scores import (
    assessment_truth_by_logical_membership,
)
from ..candidate_search.role_candidate_reader import read_role_candidate_page
from ..candidate_search.role_scope import (
    RelatedRoleSearchApplication,
    build_top_candidate_role_scope,
    resolve_candidate_role_scope,
)
from ..domains.assessments_runtime.pipeline_event_service import (
    event_metadata_id as _event_metadata_id,
    resolve_historical_event_role_id as _historical_event_role_id,
)
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SISTER_EVAL_DONE, SisterRoleEvaluation
from ..models.user import User
from ..services.decision_membership import apply_live_logical_decision_scope
from .payloads import (
    SCORE_FIELDS,
    application_detail,
    application_summary,
    candidate_detail,
    comparison_row,
    role_detail,
    role_summary,
)

logger = logging.getLogger("taali.mcp.handlers")

PIPELINE_STAGES = (
    "sourced",
    "applied",
    "invited",
    "in_assessment",
    "review",
    "advanced",
)
APPLICATION_OUTCOMES = ("open", "rejected", "withdrawn", "hired")


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _current_state_payload(application: Any) -> dict[str, Any]:
    """Provider-neutral role-local current state for every agent surface."""

    from ..services.ats_context_service import application_ats_context

    ats_context = getattr(application, "ats_context", None)
    if not isinstance(ats_context, dict):
        ats_context = application_ats_context(application)
    restrictions = getattr(application, "action_restrictions", None)
    if not isinstance(restrictions, dict):
        terminal = (
            str(getattr(application, "application_outcome", "open") or "open") != "open"
            or str(getattr(application, "pipeline_stage", "") or "").lower()
            == "advanced"
        )
        restrictions = {
            "can_assess": not terminal,
            "can_advance": not terminal,
            "can_reject": not terminal,
            "reason_codes": (["candidate_not_actionable"] if terminal else []),
        }
    return {
        "pipeline_stage": getattr(application, "pipeline_stage", None),
        "pipeline_stage_updated_at": _iso(
            getattr(application, "pipeline_stage_updated_at", None)
        ),
        "application_outcome": getattr(application, "application_outcome", None),
        "application_outcome_updated_at": _iso(
            getattr(application, "application_outcome_updated_at", None)
        ),
        "ats": ats_context,
        "restrictions": restrictions,
    }


def _with_current_state(row: dict[str, Any], application: Any) -> dict[str, Any]:
    payload = dict(row)
    payload["current_state"] = _current_state_payload(application)
    return payload


_PHYSICAL_EVIDENCE_SCOPE = "physical_application_evidence_only"
_PHYSICAL_EVIDENCE_NOTICE = (
    "This legacy read describes one physical source/ATS record. It does not "
    "include or certify logical-role membership, score, pipeline, outcome, "
    "recommendations, or recruiter judgments. Use a role-aware candidate tool "
    "for authoritative role state."
)


def _physical_application_evidence(
    row: dict[str, Any],
) -> dict[str, Any]:
    """Remove every field that could masquerade as logical-role state."""

    raw_ats = row.get("ats_context")
    ats = dict(raw_ats) if isinstance(raw_ats, dict) else {}
    # ``post_handover`` may be inferred from the physical application's local
    # pipeline stage. That inference is useful only after a logical role has
    # been selected, so it is deliberately absent from this legacy payload.
    ats.pop("post_handover", None)
    return {
        "record_scope": _PHYSICAL_EVIDENCE_SCOPE,
        "logical_role_state_included": False,
        "notice": _PHYSICAL_EVIDENCE_NOTICE,
        "application_id": row.get("application_id"),
        "candidate_id": row.get("candidate_id"),
        "candidate_name": row.get("candidate_name"),
        "candidate_email": row.get("candidate_email"),
        "candidate_position": row.get("candidate_position"),
        "candidate_location": row.get("candidate_location"),
        "ats_evidence": {
            "workable_stage": row.get("workable_stage"),
            "bullhorn_status": row.get("bullhorn_status"),
            "external_stage_raw": row.get("external_stage_raw"),
            "external_stage_normalized": row.get("external_stage_normalized"),
            "context": ats,
            "workable_profile_url": row.get("workable_profile_url"),
        },
        "cv_filename": row.get("cv_filename"),
        "cv_text": row.get("cv_text"),
        "cv_text_preview": row.get("cv_text_preview"),
        "integrity_trust_band": row.get("integrity_trust_band"),
        "integrity_warnings": row.get("integrity_warnings"),
        "created_at": row.get("created_at"),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bounded_search_text(value: object, *, field_name: str = "query") -> str:
    """Apply the shared paid-search input bound before any engine work."""

    from ..candidate_search.graph_retrieval_cache import validate_search_query

    return validate_search_query(value, field_name=field_name)


def _role_uses_explicit_membership(role: Role) -> bool:
    return bool(
        str(role.role_kind or "") == "sister" or role.ats_owner_role_id is not None
    )


def _stage_counts_for_role(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    role: Role | None = None,
) -> dict[str, int]:
    scoped_role = role or (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if scoped_role is None:
        return {stage: 0 for stage in PIPELINE_STAGES}
    scope = resolve_candidate_role_scope(
        db,
        organization_id=int(organization_id),
        role_id=int(role_id),
    )
    roster = scope.scope_visible_roster(
        db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == int(organization_id),
        )
    )
    stage = pipeline_stage_expression(scope)
    outcome = application_outcome_expression(scope)
    rows = (
        roster.filter(outcome == "open")
        .with_entities(stage, func.count(CandidateApplication.id))
        .group_by(stage)
        .all()
    )
    counts = {stage: 0 for stage in PIPELINE_STAGES}
    for stage, total in rows:
        counts[str(stage)] = int(total or 0)
    return counts


def _applications_count(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    role: Role | None = None,
) -> int:
    scoped_role = role or (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if scoped_role is None:
        return 0
    return resolve_candidate_role_scope(
        db,
        organization_id=int(organization_id),
        role_id=int(role_id),
    ).roster_size(db)


def _attach_shareable_candidate_report(
    db: Session,
    user: User,
    *,
    query: str,
    snapshot: dict[str, Any],
    scoped_role: Role | None,
) -> dict[str, Any]:
    """Best-effort attachment of a PII-scrubbed candidate-evidence snapshot."""
    # Session.begin_nested() pre-flushes the caller's pending state. Do that
    # before the best-effort boundary so a real chat/conversation flush error
    # propagates instead of being mistaken for an optional report failure.
    db.flush()
    try:
        from ..domains.top_reports.service import create_report, report_public_url

        raw_user_id = getattr(user, "id", None)
        report = create_report(
            db,
            organization_id=int(user.organization_id),
            created_by_user_id=(int(raw_user_id) if raw_user_id is not None else None),
            role_id=(int(scoped_role.id) if scoped_role is not None else None),
            query=query,
            snapshot=snapshot,
        )
        snapshot["report_token"] = report.token
        snapshot["report_url"] = report_public_url(report.token)
    except Exception as exc:  # noqa: BLE001 — search remains useful without a link
        # create_report isolates its flush in a savepoint. Never roll back the
        # caller-owned chat transaction merely because its optional report
        # attachment failed.
        logger.warning("candidate-evidence report persist failed: %s", exc)
    return snapshot


def _normalize_score_input(
    value: float | None,
    *,
    score_type: str,
) -> float | None:
    """Permit 0-10 or 0-100 input, then convert to the column's scale."""
    if value is None:
        return None
    f = float(value)
    if 0 <= f <= 10:
        f *= 10.0
    # Workable's normalized database column is deliberately 0-10. The chat
    # contract accepts the same human-friendly 0-10 shorthand as every other
    # score, or a canonical 0-100 threshold, so convert only at the SQL edge.
    return f / 10.0 if score_type == "workable" else f


def _applications_for_ids(
    db: Session,
    *,
    organization_id: int,
    application_ids: Iterable[int],
    include_deleted: bool = False,
) -> list[CandidateApplication]:
    """Hydrate a set of application ids with candidate + role joined."""
    ids = [int(a) for a in application_ids]
    if not ids:
        return []
    query = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.id.in_(ids),
            CandidateApplication.organization_id == organization_id,
        )
    )
    if not include_deleted:
        query = query.filter(CandidateApplication.deleted_at.is_(None))
    return apply_searchable_candidate_scope(
        query,
        organization_id=int(organization_id),
    ).all()


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def list_roles(
    db: Session,
    user: User,
    *,
    include_stage_counts: bool = False,
) -> list[dict[str, Any]]:
    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == user.organization_id,
            Role.deleted_at.is_(None),
        )
        .order_by(Role.created_at.desc())
        .all()
    )
    if not roles:
        return []
    logical_selection = resolve_logical_application_selection(
        db,
        organization_id=int(user.organization_id),
        role_ids=[int(role.id) for role in roles],
    )
    logical_role_id = logical_selection.logical_role_id_expression()
    count_query = logical_selection.apply_roster_membership(
        db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == int(user.organization_id),
        )
    )
    counts = {
        int(role_id): int(total or 0)
        for role_id, total in count_query.with_entities(
            logical_role_id,
            func.count(CandidateApplication.id),
        )
        .group_by(logical_role_id)
        .all()
    }
    out: list[dict[str, Any]] = []
    for role in roles:
        stage_counts = (
            _stage_counts_for_role(
                db,
                organization_id=user.organization_id,
                role_id=role.id,
                role=role,
            )
            if include_stage_counts
            else None
        )
        out.append(
            role_summary(
                role,
                applications_count=counts.get(int(role.id), 0),
                stage_counts=stage_counts,
            )
        )
    return out


def get_role(db: Session, user: User, *, role_id: int) -> dict[str, Any]:
    role = (
        db.query(Role)
        .options(joinedload(Role.criteria))
        .filter(
            Role.id == role_id,
            Role.organization_id == user.organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise ValueError(f"role {role_id} not found")
    return role_detail(
        role,
        applications_count=_applications_count(
            db,
            organization_id=user.organization_id,
            role_id=role.id,
            role=role,
        ),
        stage_counts=_stage_counts_for_role(
            db,
            organization_id=user.organization_id,
            role_id=role.id,
            role=role,
        ),
    )


def search_applications(
    db: Session,
    user: User,
    *,
    role_id: int | None = None,
    min_score: float | None = None,
    score_type: str = "taali",
    pipeline_stage: str | None = None,
    application_outcome: str | None = "open",
    q: str | None = None,
    sort_by: str = "taali_score",
    sort_order: str = "desc",
    limit: int = 25,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if score_type not in SCORE_FIELDS:
        raise ValueError(
            f"score_type must be one of {sorted(SCORE_FIELDS)}, got {score_type!r}"
        )
    if pipeline_stage and pipeline_stage not in PIPELINE_STAGES:
        raise ValueError(
            f"pipeline_stage must be one of {list(PIPELINE_STAGES)}, got {pipeline_stage!r}"
        )
    if application_outcome and application_outcome not in APPLICATION_OUTCOMES:
        raise ValueError(
            f"application_outcome must be one of {list(APPLICATION_OUTCOMES)} or null, "
            f"got {application_outcome!r}"
        )
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))

    sort_column_map = {
        "taali_score": "taali_score_cache_100",
        "pre_screen_score": "pre_screen_score_100",
        "rank_score": "rank_score",
        "cv_match_score": "cv_match_score",
        "workable_score": "workable_score",
        "assessment_score": "assessment_score_cache_100",
        "role_fit_score": "role_fit_score_cache_100",
        "created_at": "created_at",
    }
    if sort_by not in sort_column_map:
        raise ValueError(
            f"sort_by must be one of {sorted(sort_column_map)}, got {sort_by!r}"
        )
    threshold = _normalize_score_input(min_score, score_type=score_type)

    if role_id is None:
        page = read_global_candidate_page(
            db,
            organization_id=int(user.organization_id),
            score_field=SCORE_FIELDS[score_type],
            sort_field=sort_column_map[sort_by],
            sort_order=sort_order,
            min_score=threshold,
            pipeline_stage=pipeline_stage,
            application_outcome=application_outcome,
            q=q,
            limit=limit,
            offset=offset,
            limit_ceiling=100,
            prioritize_advanced=sort_by != "created_at",
        )
        rows: list[dict[str, Any]] = []
        for application, membership_id in zip(
            page.applications,
            page.logical_membership_ids,
            strict=True,
        ):
            row = _with_current_state(application_summary(application), application)
            if isinstance(application, RelatedRoleSearchApplication):
                row = strip_owner_role_judgments(row)
            row["logical_membership_id"] = membership_id
            rows.append(row)
        return rows

    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role_id),
    )

    query = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(CandidateApplication.organization_id == user.organization_id)
    )
    query = role_scope.scope_visible_roster(query)
    stage_expression = pipeline_stage_expression(role_scope)
    outcome_expression = application_outcome_expression(role_scope)
    if pipeline_stage:
        query = query.filter(stage_expression == pipeline_stage)
    if application_outcome:
        query = query.filter(outcome_expression == application_outcome)
    if threshold is not None:
        score_col = score_expression(role_scope, SCORE_FIELDS[score_type])
        query = query.filter(score_col >= threshold)
    if q:
        like = f"%{q.strip()}%"
        query = query.join(
            Candidate, CandidateApplication.candidate_id == Candidate.id
        ).filter(
            or_(
                Candidate.full_name.ilike(like),
                Candidate.email.ilike(like),
                Candidate.position.ilike(like),
            )
        )

    sort_col = score_expression(role_scope, sort_column_map[sort_by])
    ascending = sort_order == "asc"

    # Agent should evaluate candidates the recruiter has already moved
    # forward (pipeline_stage='advanced') BEFORE fresh applied rows —
    # those carry hard recruiter signal and tend to be the ones a
    # decision is actually waiting on. Express that ordering in SQL — an
    # "is advanced" flag first, then the chosen sort column — so we can
    # push .limit() to the DB instead of materializing the whole org's
    # filtered set and slicing in Python.
    is_advanced = func.lower(func.coalesce(stage_expression, "")) == "advanced"
    # NULL scores sort as the smallest value (matches the previous
    # float("-inf") key): last on desc, first on asc.
    score_order = (
        sort_col.asc().nullsfirst() if ascending else sort_col.desc().nullslast()
    )
    # The id tie-breaker is required for offset pagination: equal/null scores
    # are common, and an under-specified order can otherwise overlap or skip
    # rows between pages.
    query = query.order_by(
        is_advanced.desc(),
        score_order,
        CandidateApplication.id.desc(),
    )

    apps = query.offset(offset).limit(limit).all()
    assessment_truth = role_scope.assessment_truth_map(
        db,
        applications=list(apps),
    )
    adapter = role_scope.row_adapter(
        role_scope.evaluation_map(
            db,
            application_ids=[int(application.id) for application in apps],
        ),
        assessment_truth=assessment_truth,
    )
    rows = [
        application_summary(adapter(app) if adapter is not None else app)
        for app in apps
    ]
    if role_scope.is_related:
        rows = [strip_owner_role_judgments(row) for row in rows]
    return rows


def search_role_candidates(
    db: Session,
    user: User,
    *,
    role_id: int,
    min_score: float | None = None,
    score_type: str = "taali",
    pipeline_stage: str | None = None,
    application_outcome: str | None = "open",
    q: str | None = None,
    sort_by: str = "taali_score",
    sort_order: str = "desc",
    limit: int = 25,
    offset: int = 0,
    ats_stage: str | None = None,
    workable_stage: str | None = None,
    has_pending_decision: bool | None = None,
) -> dict[str, Any]:
    """Exact logical-role pool query with current role and ATS state."""

    if score_type not in SCORE_FIELDS:
        raise ValueError(
            f"score_type must be one of {sorted(SCORE_FIELDS)}, got {score_type!r}"
        )
    if pipeline_stage and pipeline_stage not in PIPELINE_STAGES:
        raise ValueError(
            f"pipeline_stage must be one of {list(PIPELINE_STAGES)}, got {pipeline_stage!r}"
        )
    if application_outcome and application_outcome not in APPLICATION_OUTCOMES:
        raise ValueError(
            f"application_outcome must be one of {list(APPLICATION_OUTCOMES)} or null, "
            f"got {application_outcome!r}"
        )
    sort_column_map = {
        "taali_score": "taali_score_cache_100",
        "pre_screen_score": "pre_screen_score_100",
        "rank_score": "rank_score",
        "cv_match_score": "cv_match_score",
        "workable_score": "workable_score",
        "assessment_score": "assessment_score_cache_100",
        "role_fit_score": "role_fit_score_cache_100",
        "created_at": "created_at",
    }
    if sort_by not in sort_column_map:
        raise ValueError(
            f"sort_by must be one of {sorted(sort_column_map)}, got {sort_by!r}"
        )
    threshold = _normalize_score_input(min_score, score_type=score_type)
    page = read_role_candidate_page(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role_id),
        score_field=SCORE_FIELDS[score_type],
        sort_field=sort_column_map[sort_by],
        sort_order=sort_order,
        min_score=threshold,
        pipeline_stage=pipeline_stage,
        application_outcome=application_outcome,
        q=q,
        ats_stage=ats_stage,
        workable_stage=workable_stage,
        has_pending_decision=has_pending_decision,
        limit=limit,
        offset=offset,
        limit_ceiling=100,
        prioritize_advanced=sort_by != "created_at",
    )
    role_scope = page.scope
    assert role_scope.requested_role is not None

    items: list[dict[str, Any]] = []
    for application in page.applications:
        row = _with_current_state(application_summary(application), application)
        if role_scope.is_related:
            row = strip_owner_role_judgments(row)
        pending = page.pending_by_application.get(int(application.id))
        row["pending_decision"] = (
            {
                "id": int(pending.id),
                "decision_type": pending.decision_type,
                "recommendation": pending.recommendation,
                "status": pending.status,
                "created_at": _iso(pending.created_at),
            }
            if pending is not None
            else None
        )
        items.append(row)

    returned = len(items)
    return {
        "role": {
            "id": int(role_scope.requested_role.id),
            "name": role_scope.requested_role.name,
        },
        "items": items,
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
        "total_is_exact": True,
        "has_more": page.offset + returned < page.total,
        "page_exhaustive": page.offset == 0 and returned == page.total,
        "filters": {
            "q": q,
            "pipeline_stage": pipeline_stage,
            "application_outcome": application_outcome,
            "ats_stage": ats_stage,
            "workable_stage": workable_stage,
            "has_pending_decision": has_pending_decision,
            "min_score": min_score,
            "score_type": score_type,
            "sort_by": sort_by,
            "sort_order": sort_order,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_application(
    db: Session,
    user: User,
    *,
    application_id: int,
    include_cv_text: bool = False,
) -> dict[str, Any]:
    query = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == user.organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
    )
    app = apply_searchable_candidate_scope(
        query,
        organization_id=int(user.organization_id),
    ).first()
    if app is None:
        raise ValueError(f"application {application_id} not found")
    return _physical_application_evidence(
        application_detail(app, include_cv_text=include_cv_text)
    )


def get_role_candidate(
    db: Session,
    user: User,
    *,
    role_id: int,
    application_id: int,
    include_cv_text: bool = False,
) -> dict[str, Any]:
    """Return one application only when it belongs to the logical role."""

    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role_id),
    )
    query = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(user.organization_id),
        )
    )
    application = role_scope.scope_visible_roster(query).one_or_none()
    if application is None:
        raise ValueError(
            f"application {application_id} is not in role {role_id}'s candidate pool"
        )
    evaluation_map = role_scope.evaluation_map(
        db, application_ids=[int(application.id)]
    )
    assessment_truth = role_scope.assessment_truth_map(
        db,
        applications=[application],
    )
    adapter = role_scope.row_adapter(
        evaluation_map,
        assessment_truth=assessment_truth,
    )
    presented = adapter(application) if adapter is not None else application
    row = _with_current_state(
        application_detail(presented, include_cv_text=include_cv_text),
        presented,
    )
    return strip_owner_role_judgments(row) if role_scope.is_related else row


def get_candidate(db: Session, user: User, *, candidate_id: int) -> dict[str, Any]:
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == candidate_id,
            Candidate.organization_id == user.organization_id,
            Candidate.deleted_at.is_(None),
        )
        .first()
    )
    if candidate is None:
        raise ValueError(f"candidate {candidate_id} not found")
    ordinary_applications = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.role))
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.organization_id == int(user.organization_id),
            CandidateApplication.candidate_id == int(candidate_id),
            CandidateApplication.deleted_at.is_(None),
            Role.organization_id == int(user.organization_id),
            Role.deleted_at.is_(None),
            Role.role_kind != ROLE_KIND_SISTER,
            Role.ats_owner_role_id.is_(None),
        )
        .order_by(Role.id.asc(), CandidateApplication.id.asc())
        .all()
    )
    related_rows = (
        db.query(SisterRoleEvaluation, Role, CandidateApplication)
        .join(Role, Role.id == SisterRoleEvaluation.role_id)
        .join(
            CandidateApplication,
            CandidateApplication.id == SisterRoleEvaluation.source_application_id,
        )
        .options(
            joinedload(SisterRoleEvaluation.source_application).joinedload(
                CandidateApplication.role
            )
        )
        .filter(
            SisterRoleEvaluation.organization_id == int(user.organization_id),
            SisterRoleEvaluation.deleted_at.is_(None),
            Role.organization_id == int(user.organization_id),
            Role.deleted_at.is_(None),
            or_(
                SisterRoleEvaluation.candidate_id == int(candidate_id),
                and_(
                    SisterRoleEvaluation.candidate_id.is_(None),
                    CandidateApplication.candidate_id == int(candidate_id),
                ),
            ),
        )
        .order_by(Role.id.asc(), SisterRoleEvaluation.id.asc())
        .all()
    )
    related_assessment_truth = assessment_truth_by_logical_membership(
        db,
        organization_id=int(user.organization_id),
        memberships=[
            (int(logical_role.id), source_application)
            for _evaluation, logical_role, source_application in related_rows
        ],
    )
    logical_applications: list[Any] = list(ordinary_applications)
    logical_applications.extend(
        RelatedRoleSearchApplication(
            source_application,
            role=logical_role,
            evaluation=evaluation,
            assessment_score=None,
            assessment_truth=related_assessment_truth.get(
                (int(logical_role.id), int(source_application.id))
            ),
        )
        for evaluation, logical_role, source_application in related_rows
    )
    return candidate_detail(candidate, applications=logical_applications)


def compare_applications(
    db: Session,
    user: User,
    *,
    application_ids: list[int],
) -> dict[str, Any]:
    if len(application_ids) < 2:
        raise ValueError("compare_applications requires at least 2 ids")
    if len(application_ids) > 5:
        raise ValueError("compare_applications accepts at most 5 ids")

    apps = _applications_for_ids(
        db, organization_id=user.organization_id, application_ids=application_ids
    )
    found_ids = {a.id for a in apps}
    missing = [aid for aid in application_ids if aid not in found_ids]
    rows = [
        _physical_application_evidence(
            application_detail(application, include_cv_text=False)
        )
        for application in apps
    ]
    order = {aid: idx for idx, aid in enumerate(application_ids)}
    rows.sort(key=lambda r: order.get(r["application_id"], len(order)))
    return {
        "record_scope": _PHYSICAL_EVIDENCE_SCOPE,
        "logical_role_state_included": False,
        "notice": _PHYSICAL_EVIDENCE_NOTICE,
        "applications": rows,
        "missing_ids": missing,
    }


def compare_role_applications(
    db: Session,
    user: User,
    *,
    role_id: int,
    application_ids: list[int],
) -> dict[str, Any]:
    """Compare candidates only inside one logical role's local state.

    The organization-wide comparison remains useful for explicitly cross-role
    recruiter reads. Agent runtimes are bound to one role, so this variant
    prevents an ATS owner application's state or score from standing in for a
    related role's independent projection.
    """

    if len(application_ids) < 2:
        raise ValueError("compare_role_applications requires at least 2 ids")
    if len(application_ids) > 5:
        raise ValueError("compare_role_applications accepts at most 5 ids")

    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role_id),
    )
    query = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.organization_id == int(user.organization_id),
            CandidateApplication.id.in_([int(item) for item in application_ids]),
        )
    )
    applications = role_scope.scope_visible_roster(query).all()
    evaluation_map = role_scope.evaluation_map(
        db,
        application_ids=[int(application.id) for application in applications],
    )
    assessment_truth = role_scope.assessment_truth_map(
        db,
        applications=list(applications),
    )
    adapter = role_scope.row_adapter(
        evaluation_map,
        assessment_truth=assessment_truth,
    )
    presented = [
        adapter(application) if adapter is not None else application
        for application in applications
    ]
    found_ids = {int(application.id) for application in presented}
    missing = [
        int(application_id)
        for application_id in application_ids
        if int(application_id) not in found_ids
    ]
    order = {
        int(application_id): index
        for index, application_id in enumerate(application_ids)
    }
    rows = []
    for application in presented:
        row = _with_current_state(comparison_row(application), application)
        if role_scope.is_related:
            row = strip_owner_role_judgments(row)
        rows.append(row)
    rows.sort(key=lambda row: order.get(int(row["application_id"]), len(order)))
    return {
        "role": {
            "id": int(role_scope.requested_role.id),
            "name": role_scope.requested_role.name,
        },
        "applications": rows,
        "missing_ids": missing,
        "score_legend": {
            "taali": "Merged primary score (0-100) — recommended for ranking.",
            "pre_screen": "Cheap LLM gating score (0-100).",
            "rank": "Pairwise ranking against role pool (0-100).",
            "cv_match": "CV-vs-job-spec similarity (0-100).",
            "workable": "External Workable score, if synced.",
            "assessment": "Cached assessment-result score (0-100).",
            "role_fit": "Composite role-fit score (0-100).",
        },
    }


# ---------------------------------------------------------------------------
# v2 tools (semantic search across CV / skills / experience / graph)
# ---------------------------------------------------------------------------


def nl_search_candidates(
    db: Session,
    user: User,
    *,
    query: str,
    role_id: int | None = None,
    rerank: bool | None = None,
    deep_verify: bool = False,
    include_graph: bool = False,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Natural-language search over CV text, skills, experience, and graph.

    Wraps ``app.candidate_search.runner.run_search`` — same parser, same
    SQL/Cypher/rerank pipeline that powers the in-app search box. Returns
    application summaries with the ``parsed_filter`` and any ``warnings``
    so the caller (Claude / UI) can show what it actually searched for.
    """
    text = _bounded_search_text(query)

    from ..candidate_search.retrieval_reporting import page_retrieval_payload
    from ..candidate_search.runner import MAX_RETRIEVAL_LIMIT, run_search

    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(user.organization_id),
        role_id=role_id,
    )
    base = role_scope.scope_visible_roster(
        db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == user.organization_id,
        )
    )

    verify = bool(deep_verify if rerank is None else rerank)
    search_kwargs: dict[str, Any] = {}
    if getattr(user, "require_role_authority", False) is True:
        search_kwargs["require_role_authority"] = True
    result = run_search(
        db=db,
        organization_id=int(user.organization_id),
        role_id=int(role_id) if role_id is not None else None,
        nl_query=text,
        base_query=base,
        rerank_enabled=verify,
        # Topology rendering is optional. Semantic graph recall is selected by
        # the search plan independently of whether the caller requests a view.
        include_subgraph=bool(include_graph),
        # Pagination slices a fixed bounded person window. Retrieval totals must
        # not change merely because the caller asks for a later page.
        retrieval_limit=MAX_RETRIEVAL_LIMIT,
        **search_kwargs,
    )

    safe_limit = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))
    capped_ids = result.application_ids[safe_offset : safe_offset + safe_limit]
    apps = _applications_for_ids(
        db,
        organization_id=user.organization_id,
        application_ids=capped_ids,
        include_deleted=role_scope.is_related,
    )
    by_id = {a.id: a for a in apps}
    ordered = [by_id[aid] for aid in capped_ids if aid in by_id]
    verification_payload = [
        item.model_dump(mode="json") for item in result.verification_results
    ]
    verification_by_id = {
        int(item["application_id"]): item for item in verification_payload
    }
    evaluation_map = role_scope.evaluation_map(
        db,
        application_ids=[int(app.id) for app in ordered],
    )
    assessment_truth = role_scope.assessment_truth_map(
        db,
        applications=list(ordered),
    )
    row_adapter = role_scope.row_adapter(
        evaluation_map,
        assessment_truth=assessment_truth,
    )
    presented = (
        [row_adapter(app) for app in ordered] if row_adapter is not None else ordered
    )
    application_rows: list[dict[str, Any]] = []
    for app in presented:
        row = application_summary(app)
        verification = verification_by_id.get(int(app.id))
        if verification is not None:
            row["deep_verification"] = verification
        application_rows.append(row)
    retrieval_matches = (
        int(result.retrieval_matches)
        if result.retrieval_matches is not None
        else len(result.application_ids)
    )
    database_matches = (
        int(result.database_matches)
        if result.database_matches is not None
        else len(result.application_ids)
    )
    page_ids = set(capped_ids)
    page_verification_payload = [
        item for item in verification_payload if int(item["application_id"]) in page_ids
    ]
    retrieval_payload = (
        page_retrieval_payload(
            result.retrieval.model_dump(mode="json"),
            eligible_application_ids=list(result.application_ids),
            page_application_ids=capped_ids,
            retrieval_matches=retrieval_matches,
        )
        if result.retrieval is not None
        else None
    )
    return {
        "applications": application_rows,
        # Backward-compatible name plus the explicit coverage vocabulary.
        "total_matched": retrieval_matches,
        "database_matches": database_matches,
        "retrieval_matches": retrieval_matches,
        "postgres_matches": database_matches,
        "deep_checked": int(result.deep_checked),
        "evidence_succeeded": int(result.evidence_succeeded),
        "evidence_failed": int(result.evidence_failed),
        "qualified": result.qualified,
        "verification_results": page_verification_payload,
        "returned": len(ordered),
        "offset": safe_offset,
        "capped": bool(result.capped),
        "exhaustive": bool(result.exhaustive),
        "is_exact_empty": bool(getattr(result, "is_exact_empty", False)),
        "rerank_applied": bool(result.rerank_applied),
        "parsed_filter": result.parsed_filter.model_dump(mode="json"),
        "search_plan": result.search_plan,
        "retrieval": retrieval_payload,
        "warnings": [w.model_dump(mode="json") for w in result.warnings],
        "graph": _graph_topology(result.subgraph) if result.subgraph else None,
    }


def find_top_candidates(
    db: Session,
    user: User,
    *,
    query: str,
    limit: int = 10,
    rank_by: str = "taali",
    role_id: int | None = None,
    _search_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evidence-aware bounded candidate discovery and top-N ranking.

    Ranks the structured-match set by ``rank_by`` (taali by default) and
    returns the top ``limit`` candidates with available per-criterion verdicts
    and cited CV/stored evidence. Coverage and warnings explicitly identify
    degraded or unchecked results; callers must not treat unavailable evidence
    as grounded. Returns a ``spec`` echo, counts, candidates, warnings, and a
    30-day unguessable bearer ``report_url`` for the same read-only, recursively
    scrubbed evidence snapshot.
    """
    text = _bounded_search_text(query)

    from ..candidate_search.top_candidates import find_top_candidates as _engine

    rank_key = str(rank_by or "taali")
    if rank_key not in SCORE_FIELDS:
        rank_key = "taali"
    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(user.organization_id),
        role_id=role_id,
    )
    scoped_role = role_scope.requested_role
    top_scope = build_top_candidate_role_scope(
        db,
        scope=role_scope,
        rank_by=rank_key,
        score_field=SCORE_FIELDS[rank_key],
    )

    engine_args = {
        "db": db,
        "organization_id": int(user.organization_id),
        "role_id": int(role_id) if role_id is not None else None,
        "query": text,
        "base_query": top_scope.base_query,
        "limit": int(limit),
        "rank_by": rank_key,
        "score_expression": top_scope.score_expression,
        "row_adapter": top_scope.row_adapter,
        "payload_transform": top_scope.payload_transform,
        "authoritative_pool_size": top_scope.roster_size,
        "candidate_loader": top_scope.candidate_loader,
    }
    if _search_context:
        engine_args.update(
            {
                "inherited_titles_all": list(_search_context.get("titles_all") or []),
                "inherited_titles_any": list(_search_context.get("titles_any") or []),
            }
        )
    if getattr(user, "require_role_authority", False) is True:
        engine_args["require_role_authority"] = True
    result = _engine(**engine_args)

    if scoped_role is not None:
        result["role_name"] = scoped_role.name
        result["role_id"] = int(scoped_role.id)

    # Persist only after role ownership has been validated above. The report
    # service scrubs contact PII and failure never invalidates the search.
    return _attach_shareable_candidate_report(
        db,
        user,
        query=text,
        snapshot=result,
        scoped_role=scoped_role,
    )


def screen_pool_against_requirement(
    db: Session,
    user: User,
    *,
    requirement_text: str,
    limit: int = 20,
    role_id: int | None = None,
    deep_verify: bool = False,
    offset: int = 0,
) -> dict[str, Any]:
    """Rediscovery: screen the WHOLE already-scored candidate pool against a NEW
    free-text requirement.

    Where ``find_top_candidates`` returns a shortlist of the CURRENT pipeline
    ranked by each candidate's existing score, this casts a new requirement
    across the org's entire scored history — reusing each candidate's stored
    per-criterion evidence where it overlaps, optionally grounding a bounded
    subset with verbatim CV citations, and ranking by fit to THIS requirement
    (not the stale score). Returns candidates plus ``screened`` / ``capped`` /
    per-candidate ``coverage``, ``rescore_candidate_ids`` (those a full re-score
    clarifies), and a shareable report preserving the same coverage state.
    """
    text = _bounded_search_text(requirement_text, field_name="requirement_text")

    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role_id) if role_id is not None else None,
    )
    scoped_role = role_scope.requested_role

    from ..candidate_search.top_candidates import (
        screen_pool_against_requirement as _engine,
    )

    # The scored history to rediscover from: every candidate with a stored CV
    # match, EXCEPT those already hired (placed). Unlike find_top_candidates we
    # deliberately do NOT restrict to the current open pipeline — a candidate
    # rejected for or advanced on ANOTHER role may be exactly who fits this new
    # requirement (the whole point of rediscovery). NULL outcome reads as open.
    # Exclude anyone already PLACED. "Placed" is a property of the PERSON, not a
    # single application row: a candidate hired via a *different* application
    # (e.g. another role) still has other scored rows that would otherwise be
    # recommended here. So exclude at the candidate level, not just the row whose
    # own outcome is "hired". (Hires per org are few, so the id list is small.)
    hired_candidate_ids = {
        int(cid)
        for (cid,) in db.query(CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == user.organization_id,
            func.coalesce(CandidateApplication.application_outcome, "open") == "hired",
        )
        .distinct()
        .all()
        if cid is not None
    }
    hired_candidate_ids.update(
        int(cid)
        for (cid,) in db.query(SisterRoleEvaluation.candidate_id)
        .filter(
            SisterRoleEvaluation.organization_id == user.organization_id,
            func.coalesce(SisterRoleEvaluation.application_outcome, "open") == "hired",
            SisterRoleEvaluation.candidate_id.isnot(None),
        )
        .distinct()
        .all()
        if cid is not None
    )
    base = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == user.organization_id,
    )
    if hired_candidate_ids:
        base = base.filter(
            CandidateApplication.candidate_id.notin_(sorted(hired_candidate_ids))
        )
    if role_scope.is_related:
        # A related role's scored history lives entirely in its evaluation
        # rows.  Owner ``cv_match_details`` is neither admission evidence nor a
        # safe role-local verdict for this search.
        base = scope_with_evaluations(role_scope, base, required=True).filter(
            SisterRoleEvaluation.status == SISTER_EVAL_DONE,
            SisterRoleEvaluation.role_fit_score.isnot(None),
        )
    else:
        base = role_scope.scope_visible_roster(base).filter(
            CandidateApplication.cv_match_details.isnot(None)
        )

    engine_kwargs = dict(
        db=db,
        organization_id=int(user.organization_id),
        role_id=int(scoped_role.id) if scoped_role is not None else None,
        requirement=text,
        base_query=base,
        limit=int(limit),
    )
    if deep_verify:
        engine_kwargs["deep_verify"] = True
    if offset:
        engine_kwargs["offset"] = max(0, int(offset))
    if getattr(user, "require_role_authority", False) is True:
        engine_kwargs["require_role_authority"] = True
    row_adapter, payload_transform = build_role_local_projection(db, role_scope)
    if row_adapter is not None:
        engine_kwargs["row_adapter"] = row_adapter
    if payload_transform is not None:
        engine_kwargs["payload_transform"] = payload_transform
    result = _engine(**engine_kwargs)
    if scoped_role is not None:
        result["role_name"] = scoped_role.name
        result["role_id"] = int(scoped_role.id)
    return _attach_shareable_candidate_report(
        db,
        user,
        query=text,
        snapshot=result,
        scoped_role=scoped_role,
    )


def graph_search_candidates(
    db: Session,
    user: User,
    *,
    query: str,
    limit: int = 25,
    role_id: int | None = None,
) -> dict[str, Any]:
    """Compatibility view over the canonical hybrid candidate search.

    Candidate admission, role scope, grounding, coverage, and exact-zero
    semantics come from :func:`nl_search_candidates`. ``graph_facts`` remains
    for existing clients, but is presentation data from the topology of
    already-authorized results—not a second substring-matching search path.
    Original-source graph citations are exposed separately as ``evidence``.
    """
    text = _bounded_search_text(query)
    result = nl_search_candidates(
        db,
        user,
        query=text,
        role_id=role_id,
        deep_verify=False,
        include_graph=True,
        limit=limit,
        offset=0,
    )
    topology = result.get("graph") or {}
    graph_facts: list[dict[str, Any]] = []
    for edge in topology.get("edges") or []:
        fact = edge.get("fact") if isinstance(edge, dict) else None
        if not fact:
            continue
        graph_facts.append(
            {
                "fact": str(fact),
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
                "label": str(edge.get("label") or ""),
                # Graphiti edge text is generated topology context. Only the
                # episode references returned in ``evidence`` are grounding.
                "is_citation": False,
            }
        )
        if len(graph_facts) >= 10:
            break

    evidence: list[dict[str, Any]] = []
    retrieval = result.get("retrieval") or {}
    for hit in retrieval.get("hits") or []:
        if not isinstance(hit, dict) or "graph" not in (hit.get("sources") or []):
            continue
        for item in hit.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            evidence.append(
                {
                    "application_id": hit.get("application_id"),
                    "candidate_id": hit.get("candidate_id"),
                    "source": item.get("source"),
                    "reference": item.get("reference"),
                    "clause_ids": list(item.get("clause_ids") or []),
                }
            )

    return {
        **result,
        "graph_facts": graph_facts,
        "graph_facts_are_evidence": False,
        "evidence": evidence,
    }


def _graph_topology(payload) -> dict[str, Any]:
    """Convert a GraphPayload into a thin ``{nodes, edges}`` shape for
    inline visualisation in the chat UI. Hard-cap at 60 nodes / 100 edges
    so an over-broad query can't blow up the React renderer.

    The two slices are NOT independent — slicing nodes and edges by
    position lets through edges that reference nodes outside the kept
    set, and cytoscape throws synchronously when that happens (which
    React then surfaces as the global "Something went wrong" error
    boundary). We guarantee referential integrity here:

    1. Take the first 100 edges.
    2. Collect every node id those edges reference, plus the first 60
       payload nodes, capped at 60 total.
    3. Drop any edge whose source/target isn't in the kept node set.
    """
    raw_nodes = payload.nodes or []
    raw_edges = payload.edges or []

    # Step 1: pick edges first so we know which nodes we MUST keep.
    candidate_edges = list(raw_edges[:100])

    # Step 2: build the kept-nodes set, prioritising endpoints of the
    # chosen edges (so the graph is connected) over the head-of-list
    # fallback nodes.
    nodes_by_id = {n.id: n for n in raw_nodes}
    kept_ids: list[str] = []
    seen_kept: set[str] = set()

    def _try_add(node_id: str) -> None:
        if not node_id or node_id in seen_kept:
            return
        node = nodes_by_id.get(node_id)
        if node is None:
            return
        if len(kept_ids) >= 60:
            return
        seen_kept.add(node_id)
        kept_ids.append(node_id)

    for edge in candidate_edges:
        _try_add(edge.source)
        _try_add(edge.target)
    # Fill remaining capacity with head-of-list nodes so an empty-edge
    # payload still surfaces something.
    for node in raw_nodes:
        if len(kept_ids) >= 60:
            break
        _try_add(node.id)

    nodes_out = [
        {
            "id": nodes_by_id[node_id].id,
            "label": nodes_by_id[node_id].label,
            "name": nodes_by_id[node_id].name,
            "extra": nodes_by_id[node_id].extra
            if isinstance(nodes_by_id[node_id].extra, dict)
            else {},
        }
        for node_id in kept_ids
    ]

    # Step 3: keep only edges whose endpoints survived the node cap.
    edges_out = [
        {
            "source": edge.source,
            "target": edge.target,
            "label": edge.label,
            "fact": (edge.extra or {}).get("fact")
            if isinstance(edge.extra, dict)
            else None,
        }
        for edge in candidate_edges
        if edge.source in seen_kept and edge.target in seen_kept
    ]
    return {"nodes": nodes_out, "edges": edges_out}


def get_candidate_cv(
    db: Session,
    user: User,
    *,
    candidate_id: int,
) -> dict[str, Any]:
    """Parsed CV sections + raw text for a candidate.

    Useful when Claude wants to quote a candidate's CV verbatim — much
    cheaper than embedding the full CV in every search response.
    """
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == candidate_id,
            Candidate.organization_id == user.organization_id,
            Candidate.deleted_at.is_(None),
        )
        .first()
    )
    if candidate is None:
        raise ValueError(f"candidate {candidate_id} not found")
    return {
        "candidate_id": candidate.id,
        "full_name": candidate.full_name,
        "email": candidate.email,
        "cv_filename": candidate.cv_filename,
        "cv_uploaded_at": candidate.cv_uploaded_at.isoformat()
        if candidate.cv_uploaded_at
        else None,
        "cv_sections": candidate.cv_sections
        if isinstance(candidate.cv_sections, dict)
        else None,
        "cv_text": (candidate.cv_text or "").strip() or None,
        "skills": candidate.skills,
        "experience_entries": candidate.experience_entries,
        "education_entries": candidate.education_entries,
    }


# ---------------------------------------------------------------------------
# Candidate action and decision audit tools
# ---------------------------------------------------------------------------


def _normalized_state_value(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _candidate_action_from_event(
    event: CandidateApplicationEvent,
) -> dict[str, Any] | None:
    event_type = str(event.event_type or "").strip().lower()
    metadata = event.event_metadata if isinstance(event.event_metadata, dict) else {}
    recorded_status = str(getattr(event, "effect_status", None) or "").strip().lower()
    status = recorded_status or (
        "failed"
        if "failed" in event_type or "error" in event_type
        else "skipped"
        if "skipped" in event_type
        else "confirmed"
    )
    action: str | None = None
    ats_movement = event_type in {
        "workable_moved",
        "bullhorn_moved",
        "workable_move_stage_failed",
        "bullhorn_move_stage_failed",
        "workable_move_skipped",
    }
    if (
        event_type
        in {
            "pipeline_stage_changed",
            "role_pipeline_stage_changed",
        }
        and _normalized_state_value(event.to_stage) == "advanced"
    ):
        action = "advanced"
    elif ats_movement:
        action = "advanced"
    elif event_type in {
        "application_outcome_changed",
        "role_application_outcome_changed",
    }:
        outcome = _normalized_state_value(event.to_outcome)
        if outcome in {"rejected", "hired", "withdrawn"}:
            action = outcome
    elif event_type in {
        "auto_rejected",
        "workable_disqualified",
        "workable_auto_reject_applied",
        "bullhorn_rejected",
    }:
        action = "rejected"
    elif event_type == "assessment_invite_sent":
        action = "assessment_sent"
    elif event_type in {"assessment_invite_resent", "assessment_retake_sent"}:
        action = "assessment_resent"
    elif status != "confirmed":
        operation_hints = [
            str(metadata.get(key) or "").strip().lower()
            for key in (
                "action",
                "op_type",
                "source",
                "target_outcome",
                "target_stage",
                "workable_target_stage",
            )
        ]
        operation_hint = " ".join(operation_hints)
        if any(token in operation_hint for token in ("reject", "disqualif")):
            action = "rejected"
        elif "move" in event_type or any(
            hint == "move"
            or "move_stage" in hint
            or "move_candidate" in hint
            or "advance" in hint
            for hint in operation_hints
        ):
            action = "advanced"
            ats_movement = True
        elif "outcome" in event_type or "manual_outcome" in operation_hint:
            target_outcome = _normalized_state_value(metadata.get("target_outcome"))
            action = (
                target_outcome
                if target_outcome in {"rejected", "hired", "withdrawn"}
                else None
            )
    if action is None:
        return None

    if event_type in {"pipeline_stage_changed", "role_pipeline_stage_changed"}:
        # A local Taali transition to ``advanced`` confirms only that local
        # stage. Legacy metadata such as workable_target_stage is intent, not
        # proof that the provider reached Technical Interview.
        target_stage = event.to_stage
        target_outcome = event.to_outcome
    elif event_type in {
        "application_outcome_changed",
        "role_application_outcome_changed",
    }:
        target_stage = None
        target_outcome = event.to_outcome
    elif ats_movement:
        target_stage = (
            getattr(event, "target_stage", None)
            or metadata.get("target_stage")
            or metadata.get("workable_target_stage")
            or metadata.get("bullhorn_status")
            or event.to_stage
        )
        target_outcome = metadata.get("target_outcome") or event.to_outcome
    else:
        target_stage = getattr(event, "target_stage", None) or event.to_stage
        target_outcome = event.to_outcome
    decision_id = getattr(event, "agent_decision_id", None)
    try:
        decision_id = int(decision_id) if decision_id is not None else None
    except (TypeError, ValueError):
        decision_id = None
    return {
        "action": action,
        "status": status,
        "ats_movement": ats_movement,
        "target_stage": target_stage,
        "target_outcome": target_outcome,
        "decision_id": decision_id,
        "metadata": metadata,
    }


def _canonical_action_application_id(
    *,
    event: CandidateApplicationEvent,
    source_application: CandidateApplication | None,
    memberships: Iterable[SisterRoleEvaluation],
) -> int:
    """Resolve a historical physical event to one deterministic logical id.

    A live role/candidate membership is authoritative even when an older event
    was written on its previous source or ATS transport row. If the candidate
    has left the role, use the membership that was active when the event
    occurred. Database result order is never part of the decision.
    """

    event_application_id = int(event.application_id)
    candidate_id = (
        int(source_application.candidate_id)
        if source_application is not None
        else None
    )
    rows = list(memberships)
    if candidate_id is not None:
        candidate_rows = [
            row
            for row in rows
            if row.candidate_id is not None and int(row.candidate_id) == candidate_id
        ]
        if candidate_rows:
            rows = candidate_rows
    if not rows:
        return event_application_id

    def _timestamp(value: datetime | None) -> float:
        if value is None:
            return float("-inf")
        moment = value
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).timestamp()

    def _recency(row: SisterRoleEvaluation) -> tuple[float, int]:
        return (_timestamp(row.created_at), int(row.id or 0))

    live = [row for row in rows if row.deleted_at is None]
    if live:
        return int(max(live, key=_recency).source_application_id)

    event_timestamp = _timestamp(event.created_at)
    at_event = [
        row
        for row in rows
        if _timestamp(row.created_at) <= event_timestamp
        and event_timestamp <= _timestamp(row.deleted_at)
    ]
    if not at_event:
        return event_application_id
    return int(max(at_event, key=_recency).source_application_id)


def list_candidate_actions(
    db: Session,
    user: User,
    *,
    role_id: int,
    application_id: int | None = None,
    candidate_id: int | None = None,
    action: str | None = None,
    target_stage: str | None = None,
    status: str = "confirmed",
    actor_type: str | None = None,
    actor_id: int | None = None,
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
    result_view: str = "events",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Role-attributed, confirmed candidate workflow history.

    PostgreSQL events are the movement authority. Logical role, target, effect
    status, and optional decision linkage come only from first-class event
    columns; a pending recommendation or current state can never become a
    completed action through this handler.
    """

    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role_id),
    )
    assert role_scope.requested_role is not None
    safe_limit = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))
    if result_view not in {"events", "candidates"}:
        raise ValueError("result_view must be 'events' or 'candidates'")
    # Event.role_id is immutable historical authority. Do not intersect the
    # audit log with today's live roster: an application or explicit related-
    # role membership may be removed after a confirmed action, but that must
    # never erase who was advanced/rejected/sent an assessment and when.
    # Current membership is loaded separately below only to enrich each event
    # with a current-state snapshot when one still exists.
    if role_scope.is_related:
        historical_memberships = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.organization_id == int(user.organization_id),
                SisterRoleEvaluation.role_id == int(role_id),
            )
            .all()
        )
        legacy_application_ids = {
            application_key
            for membership in historical_memberships
            for application_key in (
                int(membership.source_application_id),
                (
                    int(membership.ats_application_id)
                    if membership.ats_application_id is not None
                    else None
                ),
            )
            if application_key is not None
        }
    else:
        historical_memberships = []
        legacy_application_ids = {
            int(row_id)
            for (row_id,) in db.query(CandidateApplication.id)
            .filter(
                CandidateApplication.organization_id == int(user.organization_id),
                CandidateApplication.role_id == int(role_id),
            )
            .all()
        }

    logical_candidate_id = int(candidate_id) if candidate_id is not None else None
    if application_id is not None:
        application_candidate_id = (
            db.query(CandidateApplication.candidate_id)
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(user.organization_id),
            )
            .scalar()
        )
        if application_candidate_id is None:
            raise ValueError(f"application {application_id} not found")
        if (
            logical_candidate_id is not None
            and logical_candidate_id != int(application_candidate_id)
        ):
            raise ValueError(
                "application_id and candidate_id identify different candidates"
            )
        logical_candidate_id = int(application_candidate_id)

    event_query = (
        db.query(CandidateApplicationEvent)
        .join(
            CandidateApplication,
            CandidateApplication.id == CandidateApplicationEvent.application_id,
        )
        .filter(
            CandidateApplication.organization_id == int(user.organization_id),
            or_(
                CandidateApplicationEvent.role_id == int(role_id),
                and_(
                    CandidateApplicationEvent.role_id.is_(None),
                    CandidateApplicationEvent.application_id.in_(
                        sorted(legacy_application_ids) or [-1]
                    ),
                ),
            ),
        )
    )
    if logical_candidate_id is not None:
        event_query = event_query.filter(
            CandidateApplication.candidate_id == logical_candidate_id,
        )
    if actor_type is not None:
        event_query = event_query.filter(
            CandidateApplicationEvent.actor_type == actor_type
        )
    if actor_id is not None:
        event_query = event_query.filter(
            CandidateApplicationEvent.actor_id == int(actor_id)
        )
    if occurred_after is not None:
        event_query = event_query.filter(
            CandidateApplicationEvent.created_at >= occurred_after
        )
    if occurred_before is not None:
        event_query = event_query.filter(
            CandidateApplicationEvent.created_at <= occurred_before
        )
    events = event_query.order_by(
        CandidateApplicationEvent.created_at.desc(),
        CandidateApplicationEvent.id.desc(),
    ).all()

    application_ids = sorted({int(event.application_id) for event in events})
    applications = _applications_for_ids(
        db,
        organization_id=int(user.organization_id),
        application_ids=application_ids,
        include_deleted=True,
    )
    source_by_id = {int(application.id): application for application in applications}
    role_memberships_by_candidate: dict[int, list[SisterRoleEvaluation]] = {}
    role_memberships_by_linked_id: dict[int, list[SisterRoleEvaluation]] = {}
    for membership in historical_memberships:
        if membership.candidate_id is not None:
            role_memberships_by_candidate.setdefault(
                int(membership.candidate_id), []
            ).append(membership)
        for linked_id in {
            int(membership.source_application_id),
            *(
                [int(membership.ats_application_id)]
                if membership.ats_application_id is not None
                else []
            ),
        }:
            role_memberships_by_linked_id.setdefault(linked_id, []).append(membership)

    # Historical NULL-role rows stay append-only. Resolve their original role
    # from metadata/decision/application using both live and soft-deleted
    # memberships, because removal today must not erase an action that occurred
    # while the candidate belonged to the role.
    null_role_events = [event for event in events if event.role_id is None]
    historical_memberships_by_application: dict[int, list[SisterRoleEvaluation]] = {}
    if null_role_events:
        all_historical_memberships = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.organization_id == int(user.organization_id),
                or_(
                    SisterRoleEvaluation.source_application_id.in_(application_ids),
                    SisterRoleEvaluation.ats_application_id.in_(application_ids),
                ),
            )
            .all()
        )
        for membership in all_historical_memberships:
            for membership_application_id in (
                int(membership.source_application_id),
                (
                    int(membership.ats_application_id)
                    if membership.ats_application_id is not None
                    else None
                ),
            ):
                if membership_application_id is not None:
                    historical_memberships_by_application.setdefault(
                        membership_application_id, []
                    ).append(membership)

    from ..models.agent_decision import AgentDecision

    hinted_decision_ids = {
        decision_id
        for event in null_role_events
        for decision_id in [
            _event_metadata_id(event, "agent_decision_id", "decision_id")
        ]
        if decision_id is not None
    }
    decisions_by_id = {
        int(decision.id): decision
        for decision in db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == int(user.organization_id),
            AgentDecision.id.in_(sorted(hinted_decision_ids) or [-1]),
        )
        .all()
    }
    decision_applications = {
        int(application.id): application
        for application in _applications_for_ids(
            db,
            organization_id=int(user.organization_id),
            application_ids=[
                int(decision.application_id) for decision in decisions_by_id.values()
            ],
            include_deleted=True,
        )
    }
    hinted_role_ids = {
        role_hint
        for event in null_role_events
        for role_hint in [_event_metadata_id(event, "acting_role_id", "role_id")]
        if role_hint is not None
    }
    hinted_role_ids.update(
        int(decision.role_id) for decision in decisions_by_id.values()
    )
    hinted_role_ids.update(int(application.role_id) for application in applications)
    valid_role_ids = {
        int(valid_role_id)
        for (valid_role_id,) in db.query(Role.id)
        .filter(
            Role.organization_id == int(user.organization_id),
            Role.id.in_(sorted(hinted_role_ids) or [-1]),
        )
        .all()
    }

    if role_scope.is_related:
        live_role_memberships = [
            membership
            for membership in historical_memberships
            if membership.deleted_at is None
            and any(
                linked_application_id in application_ids
                for linked_application_id in (
                    int(membership.source_application_id),
                    (
                        int(membership.ats_application_id)
                        if membership.ats_application_id is not None
                        else -1
                    ),
                )
            )
        ]
        current_lookup_ids = sorted(
            {
                int(membership.source_application_id)
                for membership in live_role_memberships
            }
        )
    else:
        live_role_memberships = []
        current_lookup_ids = application_ids

    current_query = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate))
        .filter(
            CandidateApplication.organization_id == int(user.organization_id),
            CandidateApplication.id.in_(current_lookup_ids),
        )
    )
    current_applications = (
        role_scope.scope_visible_roster(current_query).all()
        if current_lookup_ids
        else []
    )
    current_application_ids = [
        int(application.id) for application in current_applications
    ]
    evaluation_map = role_scope.evaluation_map(
        db, application_ids=current_application_ids
    )
    assessment_truth = role_scope.assessment_truth_map(
        db,
        applications=list(current_applications),
    )
    adapter = role_scope.row_adapter(
        evaluation_map,
        assessment_truth=assessment_truth,
    )
    presented_by_id = {
        int(application.id): (
            adapter(application) if adapter is not None else application
        )
        for application in current_applications
    }
    for membership in live_role_memberships:
        presented = presented_by_id.get(int(membership.source_application_id))
        if presented is None:
            continue
        presented_by_id[int(membership.source_application_id)] = presented
        if membership.ats_application_id is not None:
            presented_by_id[int(membership.ats_application_id)] = presented
    selected: list[dict[str, Any]] = []
    wanted_target = _normalized_state_value(target_stage)

    for event in events:
        event_role_id = _historical_event_role_id(
            event,
            application=source_by_id.get(int(event.application_id)),
            memberships=historical_memberships_by_application.get(
                int(event.application_id), []
            ),
            decisions_by_id=decisions_by_id,
            decision_applications=decision_applications,
            valid_role_ids=valid_role_ids,
        )
        if event_role_id != int(role_id):
            continue
        classified = _candidate_action_from_event(event)
        if classified is None or classified["status"] != status:
            continue
        if action is not None:
            if action == "ats_moved":
                if not classified["ats_movement"]:
                    continue
            elif classified["action"] != action:
                continue
        if (
            wanted_target
            and _normalized_state_value(classified["target_stage"]) != wanted_target
        ):
            continue

        source_application = source_by_id.get(int(event.application_id))
        presented = presented_by_id.get(int(event.application_id))
        candidate_memberships = (
            role_memberships_by_candidate.get(
                int(source_application.candidate_id), []
            )
            if source_application is not None
            else []
        )
        logical_application_id = _canonical_action_application_id(
            event=event,
            source_application=source_application,
            memberships=(
                candidate_memberships
                or role_memberships_by_linked_id.get(int(event.application_id), [])
            ),
        )
        candidate = (
            source_application.candidate if source_application is not None else None
        )
        selected.append(
            {
                "event_id": int(event.id),
                "event_type": event.event_type,
                "role_id": int(event_role_id),
                # The canonical id is the role-pool application returned by
                # search/get tools.  An event may physically live on a linked
                # ATS transport row, which remains explicit evidence below but
                # must not make the agent follow an id that is outside the
                # logical role's pool.
                "application_id": logical_application_id,
                "event_application_id": int(event.application_id),
                "candidate_id": (
                    int(source_application.candidate_id)
                    if source_application is not None
                    else None
                ),
                "candidate_name": (
                    candidate.full_name if candidate is not None else None
                ),
                "candidate_email": candidate.email if candidate is not None else None,
                "action": classified["action"],
                "status": classified["status"],
                "occurred_at": _iso(event.created_at),
                "from_stage": event.from_stage,
                "to_stage": event.to_stage,
                "from_outcome": event.from_outcome,
                "to_outcome": event.to_outcome,
                "target_stage": classified["target_stage"],
                "target_outcome": classified["target_outcome"],
                "actor": {
                    "type": event.actor_type,
                    "id": int(event.actor_id) if event.actor_id is not None else None,
                },
                "decision_id": classified["decision_id"],
                "reason": event.reason,
                "in_current_role_pool": presented is not None,
                "current_state": (
                    _current_state_payload(presented) if presented is not None else None
                ),
            }
        )

    if result_view == "candidates":
        # A single recruiter advance can write both the role-local pipeline
        # transition and the confirmed ATS transport movement.  Candidate-list
        # questions need one logical person, not one row per persistence event.
        # Keep the latest row as the compact projection and retain every event
        # id/type as auditable backing evidence. Full chronology remains
        # available through the default ``events`` view.
        candidates: dict[tuple[str, int], dict[str, Any]] = {}
        for item in selected:
            identity = (
                ("candidate", int(item["candidate_id"]))
                if item.get("candidate_id") is not None
                else ("application", int(item["application_id"]))
            )
            existing = candidates.get(identity)
            if existing is None:
                candidates[identity] = {
                    **item,
                    "event_ids": [int(item["event_id"])],
                    "event_types": [str(item["event_type"])],
                    "event_count": 1,
                    "actions": [str(item["action"])],
                }
                continue
            existing["event_ids"].append(int(item["event_id"]))
            event_type = str(item["event_type"])
            if event_type not in existing["event_types"]:
                existing["event_types"].append(event_type)
            action_name = str(item["action"])
            if action_name not in existing["actions"]:
                existing["actions"].append(action_name)
            existing["event_count"] = int(existing["event_count"]) + 1
        selected = list(candidates.values())

    total = len(selected)
    page = selected[safe_offset : safe_offset + safe_limit]
    return {
        "role": {
            "id": int(role_scope.requested_role.id),
            "name": role_scope.requested_role.name,
        },
        "items": page,
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "total_is_exact": True,
        "has_more": safe_offset + len(page) < total,
        "result_view": result_view,
        "filters": {
            "application_id": application_id,
            "candidate_id": candidate_id,
            "action": action,
            "target_stage": target_stage,
            "status": status,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "occurred_after": _iso(occurred_after),
            "occurred_before": _iso(occurred_before),
            "result_view": result_view,
        },
        "warnings": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def list_recent_agent_decisions(
    db: Session,
    user: User,
    *,
    role_id: int | None = None,
    status: str | None = None,
    application_id: int | None = None,
    candidate_id: int | None = None,
    decision_type: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    resolved_after: datetime | None = None,
    resolved_before: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Recent agent decisions visible to the recruiter.

    Used by the role-scoped Taali Chat to answer "why did the agent
    queue Lucas?" and "what did the agent decide today?". Accepts an
    optional status filter (e.g. ``pending`` / ``approved`` /
    ``overridden``) and an optional role_id (defaults to all roles in
    the org when None).
    """
    from ..models.agent_decision import (
        AGENT_DECISION_STATUSES,
        AGENT_DECISION_TYPES,
        AgentDecision,
    )

    if status and status not in AGENT_DECISION_STATUSES:
        raise ValueError(
            f"status must be one of {list(AGENT_DECISION_STATUSES)} or null, got {status!r}"
        )
    if decision_type and decision_type not in AGENT_DECISION_TYPES:
        raise ValueError(
            f"decision_type must be one of {list(AGENT_DECISION_TYPES)} or null, "
            f"got {decision_type!r}"
        )
    if role_id is not None:
        # A guessed, deleted, or cross-tenant role is not an exact empty
        # decision history. Validate the same logical-role boundary used by
        # every other canonical candidate read before returning a count.
        resolve_candidate_role_scope(
            db,
            organization_id=int(user.organization_id),
            role_id=int(role_id),
        )
    capped = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))
    query = apply_live_logical_decision_scope(
        db,
        db.query(AgentDecision),
        organization_id=int(user.organization_id),
    )
    if role_id is not None:
        query = query.filter(AgentDecision.role_id == int(role_id))
    if status:
        query = query.filter(AgentDecision.status == status)
    logical_candidate_id = int(candidate_id) if candidate_id is not None else None
    if application_id is not None:
        application_query = db.query(CandidateApplication.candidate_id).filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(user.organization_id),
        )
        application_candidate_id = apply_searchable_candidate_scope(
            application_query,
            organization_id=int(user.organization_id),
        ).scalar()
        if application_candidate_id is None:
            raise ValueError(f"application {application_id} not found")
        if (
            logical_candidate_id is not None
            and logical_candidate_id != int(application_candidate_id)
        ):
            raise ValueError(
                "application_id and candidate_id identify different candidates"
            )
        logical_candidate_id = int(application_candidate_id)
    if logical_candidate_id is not None:
        query = query.filter(AgentDecision.candidate_id == logical_candidate_id)
    if decision_type:
        query = query.filter(AgentDecision.decision_type == decision_type)
    if created_after is not None:
        query = query.filter(AgentDecision.created_at >= created_after)
    if created_before is not None:
        query = query.filter(AgentDecision.created_at <= created_before)
    if resolved_after is not None:
        query = query.filter(AgentDecision.resolved_at >= resolved_after)
    if resolved_before is not None:
        query = query.filter(AgentDecision.resolved_at <= resolved_before)

    total = int(
        query.order_by(None).with_entities(func.count(AgentDecision.id)).scalar() or 0
    )
    ordering_timestamp = (
        AgentDecision.resolved_at
        if resolved_after is not None or resolved_before is not None
        else AgentDecision.created_at
    )
    rows = (
        query.order_by(
            ordering_timestamp.desc(),
            AgentDecision.id.desc(),
        )
        .offset(safe_offset)
        .limit(capped)
        .all()
    )
    candidate_ids = sorted({int(row.candidate_id) for row in rows})
    candidates_by_id = {
        int(candidate.id): candidate
        for candidate in db.query(Candidate)
        .filter(
            Candidate.organization_id == int(user.organization_id),
            Candidate.id.in_(candidate_ids or [-1]),
            Candidate.deleted_at.is_(None),
        )
        .all()
    }
    current_states = read_logical_candidate_policy_states(
        db,
        organization_id=int(user.organization_id),
        candidate_keys=(
            (int(row.role_id), int(row.candidate_id)) for row in rows
        ),
    )
    current_state_by_key = {
        state.candidate_key: state for state in current_states
    }

    items: list[dict[str, Any]] = []
    for row in rows:
        payload = _agent_decision_payload(row)
        candidate = candidates_by_id.get(int(row.candidate_id))
        current_state = current_state_by_key.get(
            (int(row.role_id), int(row.candidate_id))
        )
        presented = current_state.application if current_state is not None else None
        payload["candidate_id"] = int(row.candidate_id)
        payload["candidate_name"] = (
            candidate.full_name if candidate is not None else None
        )
        payload["candidate_email"] = (
            candidate.email if candidate is not None else None
        )
        payload["in_current_role_pool"] = presented is not None
        if current_state is not None:
            payload["application_id"] = int(current_state.application_id)
        payload["current_state"] = (
            _current_state_payload(presented) if presented is not None else None
        )
        items.append(payload)
    return {
        "items": items,
        "total": total,
        "limit": capped,
        "offset": safe_offset,
        "total_is_exact": True,
        "has_more": safe_offset + len(items) < total,
        "filters": {
            "role_id": role_id,
            "status": status,
            "application_id": application_id,
            "candidate_id": candidate_id,
            "decision_type": decision_type,
            "created_after": _iso(created_after),
            "created_before": _iso(created_before),
            "resolved_after": _iso(resolved_after),
            "resolved_before": _iso(resolved_before),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def list_recent_agent_runs(
    db: Session,
    user: User,
    *,
    role_id: int | None = None,
    trigger: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Recent autonomous-cycle log entries.

    Each row is one ``AgentRun`` — the cycle's trigger, status, decisions
    emitted, tools called, error if any, model + prompt versions for
    A/B observation. Lets the recruiter ask "what did the agent do
    today?" or "why did the cycle fail this morning?".
    """
    from ..models.agent_run import AGENT_RUN_TRIGGERS, AgentRun

    if trigger and trigger not in AGENT_RUN_TRIGGERS:
        raise ValueError(
            f"trigger must be one of {list(AGENT_RUN_TRIGGERS)} or null, got {trigger!r}"
        )
    capped = max(1, min(int(limit), 100))
    query = db.query(AgentRun).filter(AgentRun.organization_id == user.organization_id)
    if role_id is not None:
        query = query.filter(AgentRun.role_id == int(role_id))
    if trigger:
        query = query.filter(AgentRun.trigger == trigger)

    rows = (
        query.order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
        .limit(capped)
        .all()
    )
    return [_agent_run_payload(row) for row in rows]


def explain_agent_decision(
    db: Session,
    user: User,
    *,
    decision_id: int,
) -> dict[str, Any]:
    """Full reasoning detail for one agent decision.

    Returns the decision (reasoning + evidence + confidence + status)
    plus the linked AgentRun (trigger, model_version, tools_called,
    started_at, finished_at) so the recruiter can drill into "what
    cycle produced this and what evidence did the agent see."
    """
    from ..models.agent_decision import AgentDecision
    from ..models.agent_run import AgentRun

    decision = apply_live_logical_decision_scope(
        db,
        db.query(AgentDecision).filter(
            AgentDecision.id == int(decision_id),
        ),
        organization_id=int(user.organization_id),
    ).first()
    if decision is None:
        raise ValueError(f"agent_decision {decision_id} not found")

    run_payload: dict[str, Any] | None = None
    if decision.agent_run_id is not None:
        run_row = (
            db.query(AgentRun)
            .filter(
                AgentRun.id == int(decision.agent_run_id),
                AgentRun.organization_id == user.organization_id,
            )
            .first()
        )
        if run_row is not None:
            run_payload = _agent_run_payload(run_row)

    return {
        "decision": _agent_decision_payload(decision),
        "agent_run": run_payload,
    }


def _agent_decision_payload(row: Any) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "role_id": int(row.role_id),
        "candidate_id": int(row.candidate_id),
        "application_id": int(row.application_id),
        "evidence_application_id": int(row.application_id),
        "agent_run_id": (
            int(row.agent_run_id) if row.agent_run_id is not None else None
        ),
        "decision_type": str(row.decision_type),
        "recommendation": str(row.recommendation),
        "status": str(row.status),
        "reasoning": str(row.reasoning),
        "evidence": row.evidence if isinstance(row.evidence, dict) else None,
        "confidence": (float(row.confidence) if row.confidence is not None else None),
        "model_version": str(row.model_version),
        "prompt_version": str(row.prompt_version),
        "created_at": (row.created_at.isoformat() if row.created_at else None),
        "resolved_at": (row.resolved_at.isoformat() if row.resolved_at else None),
        "resolved_by_user_id": (
            int(row.resolved_by_user_id)
            if row.resolved_by_user_id is not None
            else None
        ),
        "resolution_note": row.resolution_note,
        "override_action": row.override_action,
        "resolution_metadata": (
            row.resolution_metadata
            if isinstance(getattr(row, "resolution_metadata", None), dict)
            else {}
        ),
    }


def _agent_run_payload(row: Any) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "role_id": int(row.role_id),
        "trigger": str(row.trigger),
        "trigger_event_id": (
            int(row.trigger_event_id) if row.trigger_event_id is not None else None
        ),
        "status": str(row.status),
        "started_at": (row.started_at.isoformat() if row.started_at else None),
        "finished_at": (row.finished_at.isoformat() if row.finished_at else None),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "cache_read_tokens": int(row.cache_read_tokens or 0),
        "cache_creation_tokens": int(row.cache_creation_tokens or 0),
        "total_cost_micro_usd": int(row.total_cost_micro_usd or 0),
        "decisions_emitted": int(row.decisions_emitted or 0),
        "tools_called": row.tools_called if isinstance(row.tools_called, list) else [],
        "error": row.error,
        "model_version": str(row.model_version),
        "prompt_version": str(row.prompt_version),
    }
