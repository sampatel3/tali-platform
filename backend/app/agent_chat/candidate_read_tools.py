"""Role-scoped candidate read implementations for embedded Agent Chat.

The public tool catalogue and dispatcher remain in :mod:`agent_chat.tools`.
Keeping the query-heavy read implementations here prevents that command
surface from becoming a second monolith while the facade preserves its
existing private helper names for tests and monkeypatches.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ..candidate_search.application_role_scope import (
    application_outcome_expression,
    pipeline_stage_expression,
    score_expression,
)
from ..candidate_search.role_scope import resolve_candidate_role_scope
from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.role import Role


def role_overview(
    db: Session,
    role: Role,
    *,
    impact: Any,
    constraints: Any,
) -> dict[str, Any]:
    effective = impact.effective_threshold(db, role)
    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
    )
    base_query = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == int(role.organization_id),
    )
    base_query = role_scope.scope_visible_roster(base_query)
    stage_expression = pipeline_stage_expression(role_scope)
    outcome_expression = application_outcome_expression(role_scope)
    stage_rows = (
        base_query.with_entities(
            stage_expression, func.count(CandidateApplication.id)
        )
        .group_by(stage_expression)
        .all()
    )
    funnel = {str(stage or "unknown"): int(n) for stage, n in stage_rows}
    open_query = base_query.filter(outcome_expression == "open")
    open_candidates = int(
        open_query.order_by(None)
        .with_entities(func.count(CandidateApplication.id))
        .scalar()
        or 0
    )
    score = score_expression(role_scope, "pre_screen_score_100")
    below_query = (
        open_query.filter(score < float(effective))
        if effective is not None
        else None
    )
    if below_query is not None and not role_scope.is_related:
        # Preserve the canonical pre-screen gate: an explicit below-threshold
        # recommendation is a reject signal even when the score is absent.
        below_query = open_query.filter(
            or_(
                score < float(effective),
                func.lower(
                    func.coalesce(
                        CandidateApplication.pre_screen_recommendation, ""
                    )
                )
                == "below threshold",
            )
        )
    below_threshold = (
        int(
            below_query.order_by(None)
            .with_entities(func.count(CandidateApplication.id))
            .scalar()
            or 0
        )
        if below_query is not None
        else 0
    )
    above_threshold = max(0, open_candidates - below_threshold)

    # Provider stage is operational context shared through the ATS link; role
    # membership and Taali stage/outcome remain strictly role-local.
    source_apps = open_query.options(
        joinedload(CandidateApplication.candidate)
    ).all()
    evaluations = role_scope.evaluation_map(
        db,
        application_ids=[int(application.id) for application in source_apps],
    )
    adapter = role_scope.row_adapter(evaluations)
    presented_apps = [
        adapter(application) if adapter is not None else application
        for application in source_apps
    ]

    # Provider-neutral external-stage breakdown of the open pool. Keep the
    # Workable-specific field for backwards compatibility with the existing UI.
    workable_funnel: dict[str, int] = {}
    ats_stage_funnel: dict[str, int] = {}
    for application in presented_apps:
        state = getattr(application, "ats_context", None)
        if not isinstance(state, dict):
            from ..services.ats_context_service import application_ats_context

            state = application_ats_context(application)
        provider = str(state.get("provider") or "native")
        raw_stage = state.get("raw_stage") or getattr(
            application, "pipeline_stage", None
        ) or "(unsynced)"
        workable_key = (
            str(raw_stage) if provider == "workable" else "(unsynced)"
        )
        workable_funnel[workable_key] = workable_funnel.get(workable_key, 0) + 1
        ats_key = f"{provider}:{raw_stage}"
        ats_stage_funnel[ats_key] = ats_stage_funnel.get(ats_key, 0) + 1

    pending_rows = (
        db.query(AgentDecision.decision_type, func.count(AgentDecision.id))
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
        )
        .group_by(AgentDecision.decision_type)
        .all()
    )
    pending_by_type = {str(decision_type): int(n) for decision_type, n in pending_rows}

    # A metering/query failure must not make the whole role snapshot unavailable.
    effective_monthly_budget_cents: int | None = None
    month_to_date_spend_cents: int | None = None
    remaining_monthly_budget_cents: int | None = None
    try:
        from ..agent_runtime import budget_guard

        effective_monthly_budget_cents = budget_guard.role_monthly_usd_cents(role)
        month_to_date_spend_cents = max(
            0, int(budget_guard.month_to_date_spend_cents(db, role=role))
        )
        if effective_monthly_budget_cents > 0:
            remaining_monthly_budget_cents = max(
                0,
                effective_monthly_budget_cents - month_to_date_spend_cents,
            )
    except Exception:  # pragma: no cover - overview remains useful without usage data
        month_to_date_spend_cents = None
        remaining_monthly_budget_cents = None

    return {
        "role": {"id": int(role.id), "name": role.name},
        "agent": {
            "enabled": bool(role.agentic_mode_enabled),
            "paused": role.agent_paused_at is not None,
            "paused_reason": role.agent_paused_reason,
            "monthly_budget_cents": role.monthly_usd_budget_cents,
            "effective_monthly_budget_cents": effective_monthly_budget_cents,
            "month_to_date_spend_cents": month_to_date_spend_cents,
            "remaining_monthly_budget_cents": remaining_monthly_budget_cents,
            "auto_reject": bool(role.auto_reject),
            "auto_reject_pre_screen": bool(role.auto_reject_pre_screen),
            "auto_promote": bool(role.auto_promote),
            "auto_send_assessment": getattr(role, "auto_send_assessment", None),
            "auto_resend_assessment": getattr(
                role, "auto_resend_assessment", None
            ),
            "auto_advance": getattr(role, "auto_advance", None),
            "auto_skip_assessment": bool(role.auto_skip_assessment),
        },
        "threshold": {
            "effective": effective,
            "role_override": role.score_threshold,
            "mode": role.auto_reject_threshold_mode or "manual",
        },
        "constraints": constraints.list_constraints(role),
        "funnel": funnel,
        "workable_stage_funnel": workable_funnel,
        "ats_stage_funnel": ats_stage_funnel,
        "open_candidates": open_candidates,
        "above_threshold": above_threshold,
        "below_threshold": below_threshold,
        "pending_decisions": sum(pending_by_type.values()),
        "pending_by_type": pending_by_type,
    }


def comment_match(comments: list[dict[str, Any]] | None, term: str) -> bool:
    """Return whether any Workable comment body matches ``term``."""

    normalized = (term or "").strip().lower()
    if not normalized:
        return False
    bodies = [str((item or {}).get("body") or "") for item in (comments or [])]
    if " " not in normalized and normalized.isalnum():
        pattern = re.compile(
            rf"\b{re.escape(normalized)}\b",
            re.IGNORECASE,
        )
        return any(pattern.search(body) for body in bodies)
    return any(normalized in body.lower() for body in bodies)


def compact_comments(
    comments: list[dict[str, Any]] | None,
    *,
    max_items: int = 3,
    max_len: int = 240,
) -> list[dict[str, Any]]:
    """Bound comment payload size while retaining the newest useful context."""

    out: list[dict[str, Any]] = []
    for item in (comments or [])[:max_items]:
        body = str((item or {}).get("body") or "").strip()
        if len(body) > max_len:
            body = body[:max_len].rstrip() + "…"
        out.append(
            {
                "author": (item or {}).get("author"),
                "created_at": (item or {}).get("created_at"),
                "body": body,
            }
        )
    return out


def search_candidate_comments(
    db: Session,
    role: Role,
    *,
    limit: int,
    ats_stage: str | None,
    comment_contains: str | None,
    impact: Any,
    matches_comment: Callable[[list[dict[str, Any]] | None, str], bool],
    compact: Callable[..., list[dict[str, Any]]],
) -> dict[str, Any]:
    """Search recruiter comments without becoming a second state reader.

    Membership and optional ATS-stage filtering come from the same canonical
    role adapter used by the exact candidate tools. The response deliberately
    omits pipeline, outcome, score, and pending-decision fields: callers must
    use ``search_role_candidates`` for those facts.
    """

    comment_filter = (comment_contains or "").strip()
    rows = impact.load_open_candidates(db, role, with_comments=True)
    chosen = [row for row in rows if row.comments]

    stage_filter = (ats_stage or "").strip().lower()
    if stage_filter:
        chosen = [
            row
            for row in chosen
            if any(
                stage_filter in str(value or "").lower()
                for value in (
                    row.ats_context.get("raw_stage"),
                    row.ats_context.get("normalized_stage"),
                )
            )
        ]
    if comment_filter:
        chosen = [
            row
            for row in chosen
            if matches_comment(row.comments, comment_filter)
        ]
    chosen = sorted(
        chosen,
        key=lambda row: row.score if row.score is not None else -1,
        reverse=True,
    )

    return {
        "scope": "candidate_comments",
        "ats_stage_filter": ats_stage or None,
        "comment_filter": comment_filter or None,
        "match_count": len(chosen),
        "match_count_is_exact": True,
        "candidates": [
            {
                "application_id": row.application_id,
                "name": row.candidate_name,
                "comments": compact(row.comments),
            }
            for row in chosen[: int(limit)]
        ],
    }


def list_resolved(
    db: Session,
    role: Role,
    *,
    outcome: str,
    limit: int,
) -> dict[str, Any]:
    from ..models.candidate import Candidate

    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
    )
    query = (
        db.query(CandidateApplication, Candidate.full_name)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == int(role.organization_id),
        )
    )
    query = role_scope.scope_visible_roster(query).filter(
        application_outcome_expression(role_scope) == outcome
    )
    total = int(
        query.order_by(None)
        .with_entities(func.count(CandidateApplication.id))
        .scalar()
        or 0
    )
    rows = query.order_by(CandidateApplication.id.desc()).limit(int(limit)).all()
    evaluations = role_scope.evaluation_map(
        db,
        application_ids=[int(application.id) for application, _ in rows],
    )
    adapter = role_scope.row_adapter(evaluations)
    return {
        "bucket": outcome,
        "count": total,
        "candidates": [
            {
                "application_id": int(presented.id),
                "name": name or "Unnamed candidate",
                "score": (
                    float(presented.pre_screen_score_100)
                    if presented.pre_screen_score_100 is not None
                    else None
                ),
                "stage": presented.pipeline_stage,
            }
            for source, name in rows
            for presented in [
                adapter(source) if adapter is not None else source
            ]
        ],
    }


__all__ = [
    "comment_match",
    "compact_comments",
    "search_candidate_comments",
    "list_resolved",
    "role_overview",
]
