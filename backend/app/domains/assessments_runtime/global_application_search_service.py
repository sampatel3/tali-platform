"""Logical-role aware implementation for the global applications search route."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from ...candidate_search.logical_application_scope import (
    resolve_logical_application_selection,
)
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...platform.request_context import get_request_id
from ...services.related_role_application_runtime import project_related_role_page
from .application_search_support import (
    APPLICATION_OUTCOME_VALUES,
    PIPELINE_STAGE_VALUES,
    application_order_columns,
    apply_application_source_filter,
    build_stage_counts,
    empty_stage_counts,
    enforce_provider_mode_request,
    normalize_taali_score_for_filter,
    page_retrieval_payload,
    parse_choice_csv_filter,
    parse_int_csv_filter,
    preferred_application_order,
    release_metadata,
    run_search_for_route,
)
from .role_support import application_list_payload

logger = logging.getLogger("taali.applications")


def list_applications_global_data(
    *,
    db: Session, current_user: Any,
    role_id: int | None, role_ids: str | None, source: str | None,
    pipeline_stage: str | None, pipeline_stages: str | None,
    application_outcome: str | None, application_outcomes: str | None,
    assessment_status: str | None, search: str | None, nl_query: str | None,
    view: str, rerank: bool, provider_mode: str,
    sort_by: str, sort_order: str,
    min_pre_screen_score: float | None, min_taali_score: float | None,
    include_stage_counts: bool, include_cv_text: bool,
    limit: int, offset: int,
) -> dict[str, Any]:
    started_at = perf_counter()
    requested_role_ids = parse_int_csv_filter(role_ids, field_name="role_ids")
    if role_id is not None:
        requested_role_ids = [int(role_id), *requested_role_ids]
    unique_role_ids = sorted(set(requested_role_ids))
    logical_selection = resolve_logical_application_selection(
        db,
        organization_id=int(current_user.organization_id),
        role_ids=unique_role_ids,
    )
    requested_outcomes = parse_choice_csv_filter(
        application_outcomes,
        allowed=APPLICATION_OUTCOME_VALUES,
        field_name="application_outcomes",
    )
    single_outcome = str(application_outcome or "").strip().lower()
    if not single_outcome and not requested_outcomes:
        single_outcome = "open"
    if single_outcome and single_outcome != "all":
        if single_outcome not in APPLICATION_OUTCOME_VALUES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid application_outcome value '{single_outcome}'",
            )
        if single_outcome not in requested_outcomes:
            requested_outcomes.append(single_outcome)
    requested_stages = parse_choice_csv_filter(
        pipeline_stages,
        allowed=PIPELINE_STAGE_VALUES,
        field_name="pipeline_stages",
    )
    single_stage = str(pipeline_stage or "").strip().lower()
    if single_stage and single_stage != "all":
        if single_stage not in PIPELINE_STAGE_VALUES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid pipeline_stage value '{single_stage}'",
            )
        if single_stage not in requested_stages:
            requested_stages.append(single_stage)
    threshold = normalize_taali_score_for_filter(min_taali_score)
    pre_screen_threshold = normalize_taali_score_for_filter(min_pre_screen_score)
    requested_assessment_statuses = parse_choice_csv_filter(
        assessment_status,
        allowed={"pending", "in_progress", "completed", "expired"},
        field_name="assessment_status",
    )
    status_by_value = {item.value: item for item in AssessmentStatus}
    wanted_assessment_statuses = [
        status_by_value[item]
        for item in requested_assessment_statuses
        if item in status_by_value
    ]
    if "completed" in requested_assessment_statuses:
        wanted_assessment_statuses.append(AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT)

    base_scope_query = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == current_user.organization_id,
    )
    base_scope_query = logical_selection.apply_membership(base_scope_query)
    outcome_expression = (
        logical_selection.application_outcome_expression()
        if logical_selection.active
        else CandidateApplication.application_outcome
    )
    stage_expression = (
        logical_selection.pipeline_stage_expression()
        if logical_selection.active
        else CandidateApplication.pipeline_stage
    )
    taali_score_expression = (
        logical_selection.score_expression("taali_score_cache_100")
        if logical_selection.active
        else CandidateApplication.taali_score_cache_100
    )
    pre_screen_score_expression = (
        logical_selection.score_expression("pre_screen_score_100")
        if logical_selection.active
        else CandidateApplication.pre_screen_score_100
    )
    base_scope_query = apply_application_source_filter(base_scope_query, source)
    if requested_outcomes:
        base_scope_query = base_scope_query.filter(
            outcome_expression.in_(requested_outcomes)
        )
    if search and not (nl_query or "").strip():
        term = f"%{search.strip()}%"
        base_scope_query = (
            base_scope_query.join(
                Candidate,
                Candidate.id == CandidateApplication.candidate_id,
            )
            .outerjoin(
                Role,
                Role.id
                == (
                    logical_selection.logical_role_id_expression()
                    if logical_selection.active
                    else CandidateApplication.role_id
                ),
            )
            .filter(
                Candidate.full_name.ilike(term)
                | Candidate.email.ilike(term)
                | Candidate.position.ilike(term)
                | Role.name.ilike(term)
            )
        )
    if threshold is not None:
        base_scope_query = base_scope_query.filter(
            taali_score_expression.is_not(None),
            taali_score_expression >= threshold,
        )
    if pre_screen_threshold is not None:
        base_scope_query = base_scope_query.filter(
            pre_screen_score_expression.is_not(None),
            pre_screen_score_expression >= pre_screen_threshold,
        )
    filtered_scope_query = base_scope_query
    if requested_stages:
        filtered_scope_query = filtered_scope_query.filter(
            stage_expression.in_(requested_stages)
        )
    if wanted_assessment_statuses:
        assessment_correlates = (
            (CandidateApplication, logical_selection.membership_rows)
            if logical_selection.active
            else (CandidateApplication,)
        )
        latest_assessment_id = (
            db.query(func.max(Assessment.id))
            .filter(
                Assessment.application_id == CandidateApplication.id,
                Assessment.is_voided.isnot(True),
                *(
                    (
                        Assessment.role_id
                        == logical_selection.logical_role_id_expression(),
                    )
                    if logical_selection.active
                    else ()
                ),
            )
            .correlate(*assessment_correlates)
            .scalar_subquery()
        )
        filtered_scope_query = filtered_scope_query.filter(
            db.query(Assessment.id)
            .filter(
                Assessment.id == latest_assessment_id,
                Assessment.status.in_(wanted_assessment_statuses),
            )
            .correlate(CandidateApplication)
            .exists()
        )

    nl_query_clean = (nl_query or "").strip()
    enforce_provider_mode_request(
        nl_query=nl_query_clean,
        provider_mode=provider_mode,
        rerank=rerank,
        view=view,
    )
    parsed_filter_payload = None
    nl_warnings: list[dict] = []
    nl_subgraph_payload = None
    nl_rerank_applied = False
    nl_coverage_payload = None
    nl_retrieval_payload = None
    nl_search_plan_payload = None
    nl_verification_payload: list[dict] = []
    nl_ids: list[int] = []
    if nl_query_clean:
        from ...candidate_search import rate_limit as nl_rate_limit
        from ...candidate_search.runner import MAX_RETRIEVAL_LIMIT

        if not nl_rate_limit.check_and_record(int(current_user.organization_id)):
            raise HTTPException(
                status_code=429,
                detail="Too many natural-language queries — try again in a minute.",
            )
        nl_scope_ids = (
            filtered_scope_query.order_by(None)
            .with_entities(CandidateApplication.id.label("application_id"))
            .distinct()
            .subquery()
        )
        nl_base = db.query(CandidateApplication).filter(
            CandidateApplication.organization_id
            == int(current_user.organization_id),
            CandidateApplication.id.in_(select(nl_scope_ids.c.application_id)),
        ).order_by(
            *preferred_application_order(
                application_outcome=CandidateApplication.application_outcome,
            )
        )
        nl_result = run_search_for_route(
            db=db,
            organization_id=int(current_user.organization_id),
            role_id=unique_role_ids[0] if len(unique_role_ids) == 1 else None,
            nl_query=nl_query_clean,
            base_query=nl_base,
            rerank_enabled=bool(rerank),
            include_subgraph=(view == "graph"),
            retrieval_limit=MAX_RETRIEVAL_LIMIT,
            provider_mode=provider_mode,
        )
        nl_ids = list(
            dict.fromkeys(
                int(application_id)
                for application_id in nl_result.application_ids
                if int(application_id) > 0
            )
        )
        parsed_filter_payload = nl_result.parsed_filter.model_dump(mode="json")
        nl_warnings = [w.model_dump(mode="json") for w in nl_result.warnings]
        nl_rerank_applied = nl_result.rerank_applied
        nl_verification_payload = [
            item.model_dump(mode="json") for item in nl_result.verification_results
        ]
        nl_coverage_payload = {
            "database_matches": (
                nl_result.database_matches
                if nl_result.database_matches is not None
                else len(nl_result.application_ids)
            ),
            "retrieval_matches": (
                nl_result.retrieval_matches
                if nl_result.retrieval_matches is not None
                else len(nl_result.application_ids)
            ),
            "deep_checked": nl_result.deep_checked,
            "evidence_succeeded": nl_result.evidence_succeeded,
            "evidence_failed": nl_result.evidence_failed,
            "qualified": nl_result.qualified,
            "capped": nl_result.capped,
            "exhaustive": nl_result.exhaustive,
        }
        if getattr(nl_result, "is_exact_empty", None) is not None:
            nl_coverage_payload["is_exact_empty"] = bool(nl_result.is_exact_empty)
        nl_retrieval_payload = (
            nl_result.retrieval.model_dump(mode="json")
            if nl_result.retrieval is not None
            else None
        )
        nl_search_plan_payload = nl_result.search_plan
        nl_subgraph_payload = (
            nl_result.subgraph.model_dump(mode="json")
            if nl_result.subgraph is not None
            else None
        )

    base_query = base_scope_query
    filtered_query = filtered_scope_query
    if nl_query_clean:
        constrained_ids = nl_ids or [-1]
        base_query = base_query.filter(CandidateApplication.id.in_(constrained_ids))
        filtered_query = filtered_query.filter(
            CandidateApplication.id.in_(constrained_ids)
        )
    stage_counts = empty_stage_counts()
    if include_stage_counts:
        stage_rows = (
            base_query.with_entities(stage_expression, func.count())
            .group_by(stage_expression)
            .all()
        )
        stage_counts = build_stage_counts(stage_rows)
    logical_role_expression = (
        logical_selection.logical_role_id_expression()
        if logical_selection.active
        else CandidateApplication.role_id
    )
    if nl_query_clean:
        eligible_keys_by_application: dict[int, list[tuple[int, int]]] = {}
        for application_id, logical_role_id in filtered_query.with_entities(
            CandidateApplication.id,
            logical_role_expression,
        ).all():
            key = (int(logical_role_id), int(application_id))
            eligible_keys_by_application.setdefault(int(application_id), []).append(key)
        ordered_keys = [
            key
            for application_id in nl_ids
            for key in sorted(eligible_keys_by_application.get(application_id, []))
        ]
        total = len(ordered_keys)
        page_keys = ordered_keys[offset : offset + limit]
        page_ids = [application_id for _, application_id in page_keys]
        page_id_set = set(page_ids)
        nl_verification_payload = [
            item
            for item in nl_verification_payload
            if int(item["application_id"]) in page_id_set
        ]
        if nl_coverage_payload is not None:
            nl_coverage_payload["filtered_matches"] = total
        if nl_retrieval_payload is not None:
            nl_retrieval_payload = page_retrieval_payload(
                nl_retrieval_payload,
                eligible_application_ids=list(
                    dict.fromkeys(
                        application_id for _, application_id in ordered_keys
                    )
                ),
                page_application_ids=page_ids,
                retrieval_matches=int(
                    (nl_coverage_payload or {}).get("retrieval_matches") or 0
                ),
            )
    else:
        total = filtered_query.order_by(None).count()
        page_keys = [
            (int(logical_role_id), int(application_id))
            for application_id, logical_role_id in (
                filtered_query.with_entities(
                    CandidateApplication.id,
                    logical_role_expression,
                )
                .order_by(
                    *application_order_columns(
                        sort_by,
                        sort_order,
                        logical_selection=logical_selection,
                    )
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
        ]
        page_ids = [application_id for _, application_id in page_keys]
    rows_by_id: dict[int, CandidateApplication] = {}
    if page_ids:
        fetched = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.organization),
                joinedload(CandidateApplication.role),
                selectinload(CandidateApplication.interviews),
                selectinload(CandidateApplication.assessments).joinedload(
                    Assessment.task
                ),
            )
            .filter(CandidateApplication.id.in_(page_ids))
            .all()
        )
        rows_by_id = {int(item.id): item for item in fetched}
    rows = [rows_by_id[app_id] for _, app_id in page_keys if app_id in rows_by_id]
    hydrated_keys = [key for key in page_keys if key[1] in rows_by_id]
    items = [
        application_list_payload(app, include_cv_text=include_cv_text)
        for app in rows
    ]
    if logical_selection.active and hydrated_keys:
        memberships = logical_selection.resolve_memberships(db, hydrated_keys)
        related_groups: dict[int, list[int]] = {}
        for index, (key, item) in enumerate(zip(hydrated_keys, items, strict=True)):
            membership = memberships.get(key)
            if membership is None:
                continue
            item["logical_membership_id"] = membership.public_id
            item["logical_role_id"] = int(membership.logical_role.id)
            item["role_id"] = int(membership.logical_role.id)
            item["role_name"] = membership.logical_role.name
            if membership.is_related and membership.evaluation is not None:
                related_groups.setdefault(int(membership.logical_role.id), []).append(
                    index
                )
        for related_role_id, indices in related_groups.items():
            related_role = logical_selection.roles_by_id[related_role_id]
            projected = project_related_role_page(
                db,
                sister_role=related_role,
                applications=[rows[index] for index in indices],
                payloads=[items[index] for index in indices],
                assessments_preloaded=True,
            )
            for index, payload in zip(indices, projected, strict=True):
                payload["logical_membership_id"] = (
                    f"{related_role_id}:{int(rows[index].id)}"
                )
                payload["logical_role_id"] = related_role_id
                items[index] = payload
    if nl_query_clean and nl_verification_payload:
        verification_by_id = {
            int(item["application_id"]): item for item in nl_verification_payload
        }
        for app, item in zip(rows, items):
            verification = verification_by_id.get(int(app.id))
            if verification is not None:
                item["deep_verification"] = verification

    duration_ms = (perf_counter() - started_at) * 1000.0
    logged_role_ids = sorted(set(requested_role_ids))
    logger.info(
        (
            "list_applications_global org_id=%s role_id=%s stage=%s outcome=%s "
            "search=%s source=%s total=%s limit=%s offset=%s sort_by=%s "
            "sort_order=%s include_stage_counts=%s duration_ms=%.1f request_id=%s"
        ),
        current_user.organization_id,
        ",".join(str(item) for item in logged_role_ids) or None,
        ",".join(requested_stages) or pipeline_stage,
        ",".join(requested_outcomes) or single_outcome or "all",
        bool(search and search.strip()),
        source or "all",
        total,
        limit,
        offset,
        sort_by,
        sort_order,
        include_stage_counts,
        duration_ms,
        get_request_id(),
    )
    response_payload: dict[str, Any] = {
        "items": items, "total": total, "limit": limit, "offset": offset,
    }
    if include_stage_counts:
        response_payload["stage_counts"] = stage_counts
    response_payload.update(
        release_metadata(provider_mode=provider_mode, nl_query=nl_query_clean)
    )
    if nl_query_clean:
        response_payload["parsed_filter"] = parsed_filter_payload
        response_payload["nl_warnings"] = nl_warnings
        response_payload["nl_rerank_applied"] = nl_rerank_applied
        response_payload["nl_provider_mode"] = provider_mode
        response_payload["nl_coverage"] = nl_coverage_payload
        if nl_retrieval_payload is not None:
            response_payload["nl_retrieval"] = nl_retrieval_payload
        if nl_search_plan_payload is not None:
            response_payload["nl_search_plan"] = nl_search_plan_payload
        response_payload["nl_verification"] = nl_verification_payload
        if view == "graph" and nl_subgraph_payload is not None:
            response_payload["subgraph"] = nl_subgraph_payload
    return response_payload


__all__ = ["list_applications_global_data"]
