"""Role-aware analytics over canonical logical application memberships.

Ordinary roles own ``CandidateApplication`` state. Related roles own explicit
``SisterRoleEvaluation`` state while reusing a physical application as evidence.
Every aggregate in this module therefore counts one row per logical
``(role_id, application_id)`` membership, never one row per physical record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from ...models.agent_decision import AgentDecision
from ...models.candidate_application import CandidateApplication
from ...models.sister_role_evaluation import SISTER_EVAL_DONE
from .pipeline_analytics_service import logical_analytics_selection
from .pipeline_service import (
    FUNNEL_BUCKETS,
    _post_handover_sql,
    funnel_bucket_for,
    normalize_pipeline_key,
)

ADVANCE_DECISION_TYPES = frozenset({"advance_to_interview"})
APPROVED_DECISION_STATUSES = frozenset({"approved", "processing"})
_FINAL_INTERVIEW_NORM_STAGES = frozenset({"final_interview"})
_OFFER_NORM_STAGES = frozenset({"offer", "offer_extended", "offer_accepted"})
_HIRED_NORM_STAGES = frozenset({"hired"})
_REJECT_OUTCOMES = frozenset({"rejected", "withdrawn"})


def empty_conversion_bucket() -> Dict[str, Any]:
    return {
        "advanced_total": 0,
        "reached_final_interview": 0,
        "reached_offer": 0,
        "hired": 0,
        "rejected": 0,
        "by_stage": {},
    }


def reporting_funnel_counts(
    db: Session,
    *,
    organization_id: int,
    role_id: Optional[int],
) -> Dict[str, int]:
    """Return reporting buckets from role-local stage, outcome, and score truth."""

    counts = {bucket: 0 for bucket in FUNNEL_BUCKETS}
    selection = logical_analytics_selection(db, organization_id, role_id)
    if not selection.valid_role_ids:
        return counts

    role_expr = selection.logical_role_id_expression()
    rows = selection.apply_membership(
        db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == int(organization_id)
        )
    ).with_entities(
        role_expr,
        selection.pipeline_stage_expression(),
        selection.application_outcome_expression(),
        selection.related_evaluation_status_expression(),
        CandidateApplication.cv_match_score,
        CandidateApplication.pre_screen_score_100,
        CandidateApplication.pre_screen_run_at,
        _post_handover_sql(),
    ).all()
    related_ids = set(selection.related_role_ids)
    for (
        logical_role_id,
        stage,
        outcome,
        evaluation_status,
        cv_match_score,
        pre_screen_score,
        pre_screen_run_at,
        is_post_handover,
    ) in rows:
        normalized_outcome = str(outcome or "open").lower()
        if normalized_outcome == "rejected":
            counts["rejected"] += 1
            continue
        if normalized_outcome != "open":
            continue
        is_related = int(logical_role_id) in related_ids
        is_scored = (
            str(evaluation_status or "").lower() == SISTER_EVAL_DONE
            if is_related
            else cv_match_score is not None
            or (pre_screen_score is not None and pre_screen_run_at is not None)
        )
        if not is_related and bool(is_post_handover):
            counts["advanced"] += 1
            continue
        bucket = funnel_bucket_for(normalize_pipeline_key(stage), bool(is_scored))
        if bucket:
            counts[bucket] += 1
        elif is_related:
            # Match the related-role pipeline contract for legacy/unknown local
            # stages: keep the membership visible rather than losing the row.
            counts["applied"] += 1
    return counts


def logical_current_state_aggregates(
    db: Session,
    *,
    organization_id: int,
    role_id: Optional[int],
) -> Dict[str, Any]:
    """Aggregate current stage and score state for every logical membership."""

    result: Dict[str, Any] = {
        "stages_by_role": {},
        "totals_stages": {},
        "scores_by_role": {},
        "all_scores": [],
    }
    selection = logical_analytics_selection(db, organization_id, role_id)
    if not selection.valid_role_ids:
        return result

    role_expr = selection.logical_role_id_expression()
    headline_score = func.coalesce(
        selection.score_expression("taali_score_cache_100"),
        selection.score_expression("cv_match_score"),
    )
    rows = selection.apply_membership(
        db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == int(organization_id)
        )
    ).with_entities(
        role_expr,
        selection.pipeline_stage_expression(),
        CandidateApplication.external_stage_normalized,
        CandidateApplication.workable_stage,
        headline_score,
    ).all()
    related_ids = set(selection.related_role_ids)
    for logical_role_id, local_stage, external_stage, raw_stage, score in rows:
        rid = int(logical_role_id)
        selected_stage = (
            local_stage if rid in related_ids else external_stage or raw_stage
        )
        stage_key = normalize_pipeline_key(selected_stage) or "unstaged"
        role_stages = result["stages_by_role"].setdefault(rid, {})
        role_stages[stage_key] = role_stages.get(stage_key, 0) + 1
        result["totals_stages"][stage_key] = (
            result["totals_stages"].get(stage_key, 0) + 1
        )
        if score is not None:
            value = float(score)
            result["scores_by_role"].setdefault(rid, []).append(value)
            result["all_scores"].append(value)
    return result


def logical_advance_conversion_aggregates(
    db: Session,
    *,
    organization_id: int,
    role_id: Optional[int],
    parsed_from: Optional[datetime],
    parsed_to: Optional[datetime],
) -> tuple[Dict[int, Dict[str, Any]], Dict[str, Any]]:
    """Resolve approved advances against each decision's logical-role state."""

    totals = empty_conversion_bucket()
    by_role: Dict[int, Dict[str, Any]] = {}
    selection = logical_analytics_selection(db, organization_id, role_id)
    if not selection.valid_role_ids:
        return by_role, totals

    role_expr = selection.logical_role_id_expression()
    query = selection.apply_membership(
        db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == int(organization_id)
        )
    ).join(
        AgentDecision,
        and_(
            AgentDecision.candidate_id == CandidateApplication.candidate_id,
            AgentDecision.role_id == role_expr,
        ),
    ).filter(
        AgentDecision.organization_id == int(organization_id),
        AgentDecision.decision_type.in_(ADVANCE_DECISION_TYPES),
        AgentDecision.status.in_(APPROVED_DECISION_STATUSES),
    )
    if parsed_from is not None:
        query = query.filter(AgentDecision.created_at >= parsed_from)
    if parsed_to is not None:
        query = query.filter(AgentDecision.created_at <= parsed_to)
    rows = query.with_entities(
        CandidateApplication.id,
        role_expr,
        selection.pipeline_stage_expression(),
        selection.application_outcome_expression(),
        CandidateApplication.external_stage_normalized,
        CandidateApplication.workable_stage,
        CandidateApplication.workable_disqualified,
    ).distinct().all()

    related_ids = set(selection.related_role_ids)
    for (
        _application_id,
        logical_role_id,
        local_stage,
        local_outcome,
        external_stage,
        raw_stage,
        external_disqualified,
    ) in rows:
        rid = int(logical_role_id)
        is_related = rid in related_ids
        stage = normalize_pipeline_key(
            local_stage if is_related else external_stage or raw_stage
        )
        outcome = str(local_outcome or "").lower()
        is_hired = outcome == "hired" or (
            not is_related and stage in _HIRED_NORM_STAGES
        )
        reached_offer = is_hired or (
            not is_related and stage in _OFFER_NORM_STAGES
        )
        reached_final = reached_offer or (
            not is_related and stage in _FINAL_INTERVIEW_NORM_STAGES
        )
        is_rejected = outcome in _REJECT_OUTCOMES or (
            not is_related and bool(external_disqualified)
        )
        stage_key = stage or "unstaged"
        for bucket in (by_role.setdefault(rid, empty_conversion_bucket()), totals):
            bucket["advanced_total"] += 1
            if reached_final:
                bucket["reached_final_interview"] += 1
            if reached_offer:
                bucket["reached_offer"] += 1
            if is_hired:
                bucket["hired"] += 1
            if is_rejected:
                bucket["rejected"] += 1
            bucket["by_stage"][stage_key] = bucket["by_stage"].get(stage_key, 0) + 1
    return by_role, totals


__all__ = [
    "APPROVED_DECISION_STATUSES",
    "empty_conversion_bucket",
    "logical_advance_conversion_aggregates",
    "logical_current_state_aggregates",
    "reporting_funnel_counts",
]
