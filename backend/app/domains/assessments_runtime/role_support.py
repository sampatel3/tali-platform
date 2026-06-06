from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import NO_VALUE
from sqlalchemy.orm import Session, joinedload

from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...schemas.role import ApplicationResponse, RoleCriterionResponse, RoleResponse
from ...services.interview_support_service import (
    build_role_interview_pack_templates,
    refresh_application_interview_support,
)
from ...services.pre_screening_service import pre_screen_snapshot, refresh_pre_screening_fields
from ...services.workable_actions_service import workable_job_syncable
from ...services.taali_scoring import (
    ROLE_FIT_WEIGHTS,
    TAALI_SCORING_RUBRIC_VERSION,
    TAALI_WEIGHTS,
    compute_role_fit_score,
    compute_taali_score,
    normalize_score_100,
)
from .pipeline_service import (
    ensure_pipeline_fields,
    stage_external_drift,
)


def _graph_state_for(app: CandidateApplication) -> tuple[datetime | None, bool | None]:
    """Return ``(last_synced_at, stale)`` for the candidate's graph_sync_state.

    Reads from the relationship if eagerly loaded; otherwise returns
    ``(None, None)``. We deliberately do NOT issue a fresh DB query per row
    because this is called inside the list-applications hot path. Callers
    that want this populated should load it via the join in their query.

    ``stale=True`` iff the CV was uploaded after the last graph sync.
    """
    candidate = getattr(app, "candidate", None)
    if candidate is None:
        return None, None
    state = None
    # graph_sync_state is a 1:1 relationship on Candidate via candidate_id.
    # Access lazily so it works whether the caller eager-loaded it or not;
    # SQLAlchemy will issue one extra SELECT per candidate when not loaded.
    try:
        state = getattr(candidate, "graph_sync_state", None)
    except Exception:
        return None, None
    if state is None or getattr(state, "last_synced_at", None) is None:
        return None, None
    last = state.last_synced_at
    cv_uploaded = candidate.cv_uploaded_at or app.cv_uploaded_at
    stale = bool(cv_uploaded and cv_uploaded > last)
    return last, stale


def _graph_synced_at_for(app: CandidateApplication) -> datetime | None:
    return _graph_state_for(app)[0]


def _graph_stale_for(app: CandidateApplication) -> bool | None:
    return _graph_state_for(app)[1]


def _normalize_cv_match_score_for_response(score: float | None, details: dict | None) -> float | None:
    """Coerce ``app.cv_match_score`` into 0-100 for the response.

    The v3 CV-match runner writes ``cv_match_score`` as the aggregated
    ``role_fit_score`` on a 0-100 scale. Legacy LLM paths only ever emit
    0-100 too. The old fallback "if ``numeric <= 10`` multiply by 10"
    silently inflated *real* weak scores — a candidate with
    ``role_fit_score = 9.6`` displayed as 96, masking a weak-fit
    candidate as a top one. Don't do that. The remaining ``"10" in
    scale`` branch is kept for explicit legacy payloads that tag a
    ``score_scale = "0-10"`` and really do need rescaling.
    """
    if score is None:
        return None
    scale = str((details or {}).get("score_scale") or "").strip().lower()
    if "10" in scale and "100" not in scale:
        try:
            numeric = float(score)
        except (TypeError, ValueError):
            return None
        if numeric < 0:
            return None
        return round(max(0.0, min(100.0, numeric * 10.0)), 1)
    return normalize_score_100(score)


def _normalize_score_100_for_response(value: float | int | None) -> float | None:
    return normalize_score_100(value)


def role_has_job_spec(role: Role) -> bool:
    return bool(
        (role.job_spec_file_url or "").strip()
        or (role.job_spec_text or "").strip()
        or (role.description or "").strip()
    )


def get_role(role_id: int, org_id: int, db: Session) -> Role:
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == org_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def get_application(application_id: int, org_id: int, db: Session) -> CandidateApplication:
    app = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.organization),
            joinedload(CandidateApplication.role),
            joinedload(CandidateApplication.interviews),
            joinedload(CandidateApplication.assessments).joinedload(Assessment.task),
        )
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .first()
    )
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


# A6 core invariant: once a candidate is rejected, hired, or advanced
# out of Tali, the agent never touches them again and the platform freezes
# the snapshot for audit. Every agent-acting site checks this and short-
# circuits if True. Candidates do not return to Tali post-handover.
RESOLVED_APPLICATION_OUTCOMES = frozenset({"rejected", "hired"})
RESOLVED_PIPELINE_STAGES = frozenset({"advanced"})


def is_resolved(app: CandidateApplication) -> bool:
    """True when an application is terminally resolved.

    Resolved == ``application_outcome in {rejected, hired}`` OR
    ``pipeline_stage == 'advanced'``. From this point forward the
    decision snapshot is frozen, agent never re-evaluates, score
    invalidation hooks no-op, and any re-evaluate request 409s.
    """
    outcome = (app.application_outcome or "").lower()
    if outcome in RESOLVED_APPLICATION_OUTCOMES:
        return True
    stage = (app.pipeline_stage or "").lower()
    if stage in RESOLVED_PIPELINE_STAGES:
        return True
    return False


def _loaded_relationship_items(entity: Any, relationship_name: str) -> list[Any] | None:
    try:
        loaded = getattr(sa_inspect(entity).attrs, relationship_name).loaded_value
    except Exception:
        return None
    if loaded is NO_VALUE:
        return None
    return list(loaded or [])


def role_to_response(
    role: Role,
    *,
    tasks_count: int | None = None,
    applications_count: int | None = None,
    stage_counts: dict[str, int] | None = None,
    pending_decisions_by_type: dict[str, int] | None = None,
    active_candidates_count: int | None = None,
    last_candidate_activity_at: datetime | None = None,
) -> RoleResponse:
    if tasks_count is None:
        loaded_tasks = _loaded_relationship_items(role, "tasks")
        tasks_count = len(loaded_tasks or [])
    if applications_count is None:
        loaded_applications = _loaded_relationship_items(role, "applications") or []
        applications_count = len(
            [a for a in loaded_applications if getattr(a, "deleted_at", None) is None]
        )

    role_templates = build_role_interview_pack_templates(role)
    screening_pack_template = (
        role.screening_pack_template
        if isinstance(role.screening_pack_template, dict)
        else role_templates.get("screening")
    )
    tech_interview_pack_template = (
        role.tech_interview_pack_template
        if isinstance(role.tech_interview_pack_template, dict)
        else role_templates.get("tech_stage_2")
    )
    loaded_criteria = _loaded_relationship_items(role, "criteria")
    if loaded_criteria is None:
        # Bounded fan-out (≤32 per role) makes lazy load acceptable here.
        try:
            loaded_criteria = list(role.criteria or [])
        except Exception:
            loaded_criteria = []
    criteria = [
        RoleCriterionResponse.model_validate(c)
        for c in loaded_criteria
        if getattr(c, "deleted_at", None) is None
    ]
    return RoleResponse(
        id=role.id,
        organization_id=role.organization_id,
        name=role.name,
        description=role.description,
        criteria=criteria,
        source=role.source,
        workable_job_id=role.workable_job_id,
        workable_job_state=(
            str(role.workable_job_data.get("state") or "").strip().lower() or None
            if isinstance(getattr(role, "workable_job_data", None), dict)
            else None
        ),
        workable_job_live=workable_job_syncable(role),
        job_spec_filename=role.job_spec_filename,
        job_spec_text=role.job_spec_text,
        job_spec_uploaded_at=role.job_spec_uploaded_at,
        job_spec_present=role_has_job_spec(role),
        interview_focus=role.interview_focus,
        interview_focus_generated_at=role.interview_focus_generated_at,
        screening_pack_template=screening_pack_template,
        tech_interview_pack_template=tech_interview_pack_template,
        auto_reject_threshold_mode=getattr(role, "auto_reject_threshold_mode", "manual") or "manual",
        auto_reject=bool(getattr(role, "auto_reject", False)),
        auto_promote=bool(getattr(role, "auto_promote", False)),
        workable_actor_member_id=role.workable_actor_member_id,
        starred_for_auto_sync=bool(getattr(role, "starred_for_auto_sync", False)),
        agentic_mode_enabled=bool(getattr(role, "agentic_mode_enabled", False)),
        agent_action_allowlist=getattr(role, "agent_action_allowlist", None),
        agent_token_budget_per_cycle=getattr(role, "agent_token_budget_per_cycle", None),
        agent_decision_budget_per_cycle=getattr(role, "agent_decision_budget_per_cycle", None),
        monthly_usd_budget_cents=getattr(role, "monthly_usd_budget_cents", None),
        score_threshold=getattr(role, "score_threshold", None),
        agent_paused_at=getattr(role, "agent_paused_at", None),
        agent_paused_reason=getattr(role, "agent_paused_reason", None),
        agent_last_run_at=getattr(role, "agent_last_run_at", None),
        tasks_count=tasks_count,
        applications_count=applications_count,
        stage_counts=stage_counts or {},
        pending_decisions_by_type=pending_decisions_by_type or {},
        active_candidates_count=int(active_candidates_count or 0),
        last_candidate_activity_at=last_candidate_activity_at,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _candidate_location(candidate) -> str | None:
    if not candidate:
        return None
    city = (candidate.location_city or "").strip()
    country = (candidate.location_country or "").strip()
    if city and country:
        return f"{city}, {country}"
    return city or country or None


def _assessment_status_value(assessment: Assessment | None) -> str | None:
    if not assessment:
        return None
    status = getattr(assessment, "status", None)
    return status.value if hasattr(status, "value") else (str(status) if status is not None else None)


def _is_completed_assessment(assessment: Assessment | None) -> bool:
    status = _assessment_status_value(assessment)
    return status in {
        AssessmentStatus.COMPLETED.value,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value,
    }


def _sort_dt(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _last_activity_at(app: CandidateApplication) -> datetime | None:
    """Most recent moment any meaningful activity touched this application.

    Spans the application row itself — CV upload, every scoring pass
    (CV-match, pre-screen, cached composite), and stage / outcome / notes
    edits (all of which bump ``updated_at``) — plus linked assessments,
    whose ``updated_at`` is bumped when a recruiter appends a note/comment
    to the timeline. Drives the pipeline "Last updated" column + sort.

    Relies only on columns + the ``assessments`` relationship that the list
    endpoint already eager-loads, so it adds no per-row queries. Events are
    intentionally excluded (not eager-loaded → would be N+1); the activity
    they record also bumps one of the timestamps below.
    """
    candidates: list[datetime | None] = [
        app.created_at,
        app.updated_at,
        app.pipeline_stage_updated_at,
        app.application_outcome_updated_at,
        app.cv_uploaded_at,
        app.cv_match_scored_at,
        app.pre_screen_run_at,
        app.score_cached_at,
        app.auto_reject_triggered_at,
    ]
    for assessment in (app.assessments or []):
        candidates.append(getattr(assessment, "updated_at", None))
        candidates.append(getattr(assessment, "scored_at", None))
        candidates.append(getattr(assessment, "completed_at", None))
        candidates.append(getattr(assessment, "created_at", None))
    present = [value for value in candidates if value is not None]
    if not present:
        return None
    # ``key=_sort_dt`` normalizes naive→UTC so mixed tz datetimes compare
    # cleanly; the original (tz-preserving) value is returned.
    return max(present, key=_sort_dt)


def _requirements_fit_score(details: dict | None) -> float | None:
    if not isinstance(details, dict):
        return None
    raw = details.get("requirements_match_score_100")
    try:
        numeric = float(raw)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(100.0, numeric)), 1)


def _score_formula_label(mode: str | None) -> str:
    if mode == "assessment_plus_role_fit":
        return "TAALI Score = 50% Assessment + 50% Role fit"
    if mode == "assessment_only_fallback":
        return "TAALI Score currently reflects Assessment only"
    return "TAALI Score currently reflects Role fit until assessment signal is available"


def _assessment_score_100(assessment: Assessment | None) -> float | None:
    if not assessment:
        return None
    for value in (
        getattr(assessment, "assessment_score", None),
        getattr(assessment, "final_score", None),
    ):
        if value is not None:
            normalized = _normalize_score_100_for_response(value)
            if normalized is not None:
                return normalized

    score_10 = getattr(assessment, "score", None)
    try:
        if score_10 is not None:
            return round(max(0.0, min(100.0, float(score_10) * 10.0)), 1)
    except (TypeError, ValueError):
        return None
    return None


def _assessment_taali_score_100(assessment: Assessment | None) -> float | None:
    if not assessment:
        return None
    if getattr(assessment, "taali_score", None) is not None:
        normalized = _normalize_score_100_for_response(getattr(assessment, "taali_score", None))
        if normalized is not None:
            return normalized

    assessment_score = _assessment_score_100(assessment)
    role_fit_score = _assessment_role_fit_score_100(assessment)
    taali_score = compute_taali_score(assessment_score, role_fit_score)
    if taali_score is not None:
        return taali_score

    if assessment_score is None:
        return role_fit_score
    if role_fit_score is None:
        return assessment_score
    return taali_score


def _assessment_role_fit_score_100(assessment: Assessment | None) -> float | None:
    if not assessment:
        return None
    score_breakdown = (
        assessment.score_breakdown
        if isinstance(getattr(assessment, "score_breakdown", None), dict)
        else {}
    )
    score_components = score_breakdown.get("score_components") if isinstance(score_breakdown, dict) else {}
    if isinstance(score_components, dict):
        try:
            if score_components.get("role_fit_score") is not None:
                return round(max(0.0, min(100.0, float(score_components.get("role_fit_score")))), 1)
        except (TypeError, ValueError):
            pass

    raw_details = (
        assessment.cv_job_match_details
        if isinstance(getattr(assessment, "cv_job_match_details", None), dict)
        else None
    )
    cv_fit_score = _normalize_cv_match_score_for_response(
        getattr(assessment, "cv_job_match_score", None),
        raw_details,
    )
    requirements_fit_score = _requirements_fit_score(raw_details)
    return compute_role_fit_score(cv_fit_score, requirements_fit_score)


def _dimension_extremes(category_scores: dict[str, Any] | None) -> tuple[str | None, str | None]:
    numeric_scores = []
    for key, value in (category_scores or {}).items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        numeric_scores.append((key, numeric))
    if not numeric_scores:
        return None, None
    strongest = max(numeric_scores, key=lambda item: item[1])[0]
    weakest = min(numeric_scores, key=lambda item: item[1])[0]
    return strongest, weakest


def _active_assessments_for_application(app: CandidateApplication) -> list[Assessment]:
    assessments = [
        assessment
        for assessment in (app.assessments or [])
        if not bool(getattr(assessment, "is_voided", False))
    ]
    return sorted(
        assessments,
        key=lambda assessment: (
            _sort_dt(getattr(assessment, "completed_at", None)),
            _sort_dt(getattr(assessment, "created_at", None)),
            int(getattr(assessment, "id", 0) or 0),
        ),
        reverse=True,
    )


def _has_voided_attempts_from_loaded_relationship(app: CandidateApplication) -> bool:
    try:
        loaded = sa_inspect(app).attrs.assessments.loaded_value
    except Exception:
        return False
    if loaded is NO_VALUE:
        return False
    return any(bool(getattr(assessment, "is_voided", False)) for assessment in (loaded or []))


def latest_valid_role_assessment(
    *,
    candidate_id: int | None,
    role_id: int | None,
    org_id: int,
    db: Session,
) -> Assessment | None:
    if not candidate_id or not role_id:
        return None
    return (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == org_id,
            Assessment.candidate_id == candidate_id,
            Assessment.role_id == role_id,
            Assessment.is_voided.is_(False),
        )
        .order_by(Assessment.created_at.desc(), Assessment.id.desc())
        .first()
    )


def completed_valid_role_assessment(
    *,
    candidate_id: int | None,
    role_id: int | None,
    org_id: int,
    db: Session,
) -> Assessment | None:
    if not candidate_id or not role_id:
        return None
    return (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == org_id,
            Assessment.candidate_id == candidate_id,
            Assessment.role_id == role_id,
            Assessment.is_voided.is_(False),
            Assessment.status.in_(
                [
                    AssessmentStatus.COMPLETED,
                    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
                ]
            ),
        )
        .order_by(Assessment.completed_at.desc(), Assessment.created_at.desc(), Assessment.id.desc())
        .first()
    )


def _score_summary_from_active_assessments(
    app: CandidateApplication,
    active_assessments: list[Assessment],
) -> dict[str, Any]:
    latest_assessment = active_assessments[0] if active_assessments else None
    completed_assessment = next((assessment for assessment in active_assessments if _is_completed_assessment(assessment)), None)

    app_cv_details = app.cv_match_details if isinstance(app.cv_match_details, dict) else {}
    app_cv_fit = _normalize_cv_match_score_for_response(app.cv_match_score, app_cv_details)
    app_requirements_fit = _requirements_fit_score(app_cv_details)
    app_role_fit = compute_role_fit_score(app_cv_fit, app_requirements_fit)

    if completed_assessment:
        assessment_details = (
            completed_assessment.cv_job_match_details
            if isinstance(getattr(completed_assessment, "cv_job_match_details", None), dict)
            else {}
        )
        cv_fit_score = _normalize_cv_match_score_for_response(
            getattr(completed_assessment, "cv_job_match_score", None),
            assessment_details,
        )
        requirements_fit_score = _requirements_fit_score(assessment_details)
        role_fit_score = _assessment_role_fit_score_100(completed_assessment)
        assessment_score = _assessment_score_100(completed_assessment)
        taali_score = _assessment_taali_score_100(completed_assessment)
        mode = "assessment_plus_role_fit" if role_fit_score is not None else "assessment_only_fallback"
        assessment_status = _assessment_status_value(completed_assessment)
        assessment_id = completed_assessment.id
        assessment_completed_at = completed_assessment.completed_at
    else:
        cv_fit_score = app_cv_fit
        requirements_fit_score = app_requirements_fit
        role_fit_score = app_role_fit
        assessment_score = None
        taali_score = app_role_fit
        assessment_status = _assessment_status_value(latest_assessment)
        assessment_id = latest_assessment.id if latest_assessment else None
        assessment_completed_at = None
        mode = "role_fit_only" if app_role_fit is not None else "pending"

    return {
        "taali_score": taali_score,
        "assessment_score": assessment_score,
        "role_fit_score": role_fit_score,
        "cv_fit_score": cv_fit_score,
        "requirements_fit_score": requirements_fit_score,
        "role_fit_components": {
            "cv_fit_score": cv_fit_score,
            "requirements_fit_score": requirements_fit_score,
        },
        "weights": {
            "cv_fit_score": ROLE_FIT_WEIGHTS["cv_fit"],
            "requirements_fit_score": ROLE_FIT_WEIGHTS["requirements_fit"],
            "assessment_score": TAALI_WEIGHTS["assessment"],
            "role_fit_score": TAALI_WEIGHTS["role_fit"],
        },
        "mode": mode,
        "formula_label": _score_formula_label(mode),
        "score_rubric_version": TAALI_SCORING_RUBRIC_VERSION,
        "assessment_id": assessment_id,
        "assessment_status": assessment_status,
        "assessment_completed_at": assessment_completed_at,
        "has_voided_attempts": _has_voided_attempts_from_loaded_relationship(app),
    }


def _score_summary_for_application(app: CandidateApplication) -> dict[str, Any]:
    active_assessments = _active_assessments_for_application(app)
    return _score_summary_from_active_assessments(app, active_assessments)


def _load_active_assessments_for_application(app: CandidateApplication, db: Session) -> list[Assessment]:
    if not app.candidate_id or not app.role_id:
        return []
    rows = (
        db.query(Assessment)
        .options(joinedload(Assessment.task))
        .filter(
            Assessment.organization_id == app.organization_id,
            Assessment.candidate_id == app.candidate_id,
            Assessment.role_id == app.role_id,
            Assessment.is_voided.is_(False),
        )
        .order_by(Assessment.completed_at.desc(), Assessment.created_at.desc(), Assessment.id.desc())
        .all()
    )
    return rows


def _apply_score_cache_from_summary(app: CandidateApplication, score_summary: dict[str, Any]) -> None:
    app.taali_score_cache_100 = _normalize_score_100_for_response(score_summary.get("taali_score"))
    app.assessment_score_cache_100 = _normalize_score_100_for_response(score_summary.get("assessment_score"))
    app.role_fit_score_cache_100 = _normalize_score_100_for_response(score_summary.get("role_fit_score"))
    app.score_mode_cache = (str(score_summary.get("mode") or "").strip() or None)
    app.score_cached_at = datetime.now(timezone.utc)


def refresh_application_score_cache(
    app: CandidateApplication,
    *,
    db: Session | None = None,
) -> dict[str, Any]:
    # A3: capture the assessment score BEFORE we refresh so we can
    # detect material swings (e.g. retake landed a much higher / lower
    # score). When the delta crosses the SCORE_DRIFT_BAND (5 points),
    # any pending decision cited the old score and is now stale — we
    # supersede it so the agent re-deliberates next cycle. Below the
    # band we let the staleness service flag it on the next Hub read
    # without churning the queue.
    prior_assessment_score = None
    try:
        prior_assessment_score = float(app.assessment_score_cache_100) if app.assessment_score_cache_100 is not None else None
    except (TypeError, ValueError):
        prior_assessment_score = None

    if db is not None:
        active_assessments = _load_active_assessments_for_application(app, db)
        score_summary = _score_summary_from_active_assessments(app, active_assessments)
    else:
        score_summary = _score_summary_for_application(app)
    _apply_score_cache_from_summary(app, score_summary)
    refresh_pre_screening_fields(app)

    # A3 assessment retake supersede: only for OPEN apps with a >=5pt
    # swing and only when we have a DB session to act on. A6 invariant:
    # never touch resolved candidates' decisions.
    if (
        db is not None
        and not is_resolved(app)
        and prior_assessment_score is not None
        and app.assessment_score_cache_100 is not None
    ):
        try:
            new_score = float(app.assessment_score_cache_100)
            if abs(new_score - prior_assessment_score) >= 5.0:
                # Lazy import to avoid circulars at module load.
                from ...services.cv_score_orchestrator import supersede_pending_decisions_for_app
                supersede_pending_decisions_for_app(
                    db, int(app.id),
                    reason=(
                        f"assessment_score_shifted: "
                        f"{prior_assessment_score:.1f} -> {new_score:.1f}"
                    ),
                )
        except Exception:  # pragma: no cover — defensive
            import logging
            logging.getLogger("taali.role_support").warning(
                "assessment-retake supersede failed for app=%s",
                getattr(app, "id", None), exc_info=True,
            )

    return score_summary


def score_summary_from_cache(app: CandidateApplication) -> dict[str, Any]:
    taali_score = _normalize_score_100_for_response(getattr(app, "taali_score_cache_100", None))
    assessment_score = _normalize_score_100_for_response(getattr(app, "assessment_score_cache_100", None))
    role_fit_score = _normalize_score_100_for_response(getattr(app, "role_fit_score_cache_100", None))
    cv_fit_score = _normalize_cv_match_score_for_response(
        getattr(app, "cv_match_score", None),
        app.cv_match_details if isinstance(getattr(app, "cv_match_details", None), dict) else {},
    )
    requirements_fit_score = _normalize_score_100_for_response(getattr(app, "requirements_fit_score_100", None))
    mode = str(getattr(app, "score_mode_cache", "") or "").strip()
    if not mode:
        if assessment_score is not None and role_fit_score is not None:
            mode = "assessment_plus_role_fit"
        elif assessment_score is not None:
            mode = "assessment_only_fallback"
        elif role_fit_score is not None:
            mode = "role_fit_only"
        else:
            mode = "pending"
    return {
        "taali_score": taali_score,
        "assessment_score": assessment_score,
        "role_fit_score": role_fit_score,
        "cv_fit_score": cv_fit_score,
        "requirements_fit_score": requirements_fit_score,
        "role_fit_components": {
            "cv_fit_score": cv_fit_score,
            "requirements_fit_score": requirements_fit_score,
        },
        "weights": {
            "cv_fit_score": ROLE_FIT_WEIGHTS["cv_fit"],
            "requirements_fit_score": ROLE_FIT_WEIGHTS["requirements_fit"],
            "assessment_score": TAALI_WEIGHTS["assessment"],
            "role_fit_score": TAALI_WEIGHTS["role_fit"],
        },
        "mode": mode,
        "formula_label": _score_formula_label(mode),
        "score_rubric_version": TAALI_SCORING_RUBRIC_VERSION,
        "assessment_id": None,
        "assessment_status": None,
        "assessment_completed_at": None,
        "has_voided_attempts": False,
    }


def _assessment_preview_for_application(app: CandidateApplication) -> dict[str, Any] | None:
    completed_assessment = next(
        (assessment for assessment in _active_assessments_for_application(app) if _is_completed_assessment(assessment)),
        None,
    )
    if not completed_assessment:
        return None

    score_breakdown = (
        completed_assessment.score_breakdown
        if isinstance(getattr(completed_assessment, "score_breakdown", None), dict)
        else {}
    )
    category_scores = score_breakdown.get("category_scores") or (
        completed_assessment.prompt_analytics.get("category_scores")
        if isinstance(getattr(completed_assessment, "prompt_analytics", None), dict)
        else {}
    )
    strongest_dimension, weakest_dimension = _dimension_extremes(category_scores if isinstance(category_scores, dict) else {})

    return {
        "assessment_id": completed_assessment.id,
        "task_name": completed_assessment.task.name if getattr(completed_assessment, "task", None) else None,
        "taali_score": _assessment_taali_score_100(completed_assessment),
        "assessment_score": _assessment_score_100(completed_assessment),
        "role_fit_score": _assessment_role_fit_score_100(completed_assessment),
        "category_scores": category_scores if isinstance(category_scores, dict) else {},
        "heuristic_summary": score_breakdown.get("heuristic_summary"),
        "strongest_dimension": strongest_dimension,
        "weakest_dimension": weakest_dimension,
        "completed_at": completed_assessment.completed_at,
        "status": _assessment_status_value(completed_assessment),
        "is_voided": bool(getattr(completed_assessment, "is_voided", False)),
    }


def _assessment_history_for_application(app: CandidateApplication) -> list[dict[str, Any]]:
    history = sorted(
        list(app.assessments or []),
        key=lambda assessment: (
            _sort_dt(getattr(assessment, "completed_at", None)),
            _sort_dt(getattr(assessment, "created_at", None)),
            int(getattr(assessment, "id", 0) or 0),
        ),
        reverse=True,
    )
    return [
        {
            "assessment_id": assessment.id,
            "task_name": assessment.task.name if getattr(assessment, "task", None) else None,
            "status": _assessment_status_value(assessment),
            "assessment_score": _assessment_score_100(assessment),
            "taali_score": _assessment_taali_score_100(assessment),
            "role_fit_score": _assessment_role_fit_score_100(assessment),
            "created_at": assessment.created_at,
            "completed_at": assessment.completed_at,
            "is_voided": bool(getattr(assessment, "is_voided", False)),
            "voided_at": getattr(assessment, "voided_at", None),
            "void_reason": getattr(assessment, "void_reason", None),
            "superseded_by_assessment_id": getattr(assessment, "superseded_by_assessment_id", None),
        }
        for assessment in history
    ]


# Sentinel so callers can pass an explicit ``score_status`` (including
# ``None``) and be distinguished from "not supplied — compute it yourself".
_UNSET = object()


def _latest_score_job_status(app: CandidateApplication) -> str | None:
    """Read latest CvScoreJob.status from the eagerly-loaded relationship.

    Returns ``None`` if the job log isn't loaded (e.g. detached instance).
    Avoids triggering a lazy DB query so list endpoints stay free of N+1.

    List endpoints that no longer eager-load the full ``score_jobs``
    collection should instead pass ``score_status`` into
    :func:`application_to_response` (computed once per page via a grouped
    DISTINCT ON query) rather than relying on this helper.
    """
    loaded = _loaded_relationship_items(app, "score_jobs")
    if not loaded:
        return None
    # The relationship is ordered by queued_at desc, so [0] is freshest.
    latest = loaded[0]
    return getattr(latest, "status", None)


def application_to_response(
    app: CandidateApplication,
    *,
    use_cached_score_summary: bool = False,
    score_status: Any = _UNSET,
) -> ApplicationResponse:
    ensure_pipeline_fields(app)
    candidate = app.candidate
    raw_details = app.cv_match_details if isinstance(app.cv_match_details, dict) else {}
    cv_match_score = _normalize_cv_match_score_for_response(app.cv_match_score, raw_details)
    cv_match_details = dict(raw_details)
    if cv_match_score is not None and "score_scale" not in cv_match_details:
        cv_match_details["score_scale"] = "0-100"
    # When the caller supplied the latest status (list endpoints, which fetch
    # it in one grouped query) use it; otherwise read the loaded relationship.
    if score_status is _UNSET:
        score_status = _latest_score_job_status(app)
    score_summary = score_summary_from_cache(app) if use_cached_score_summary else _score_summary_for_application(app)
    pre_screen = pre_screen_snapshot(app)
    if use_cached_score_summary:
        # List mode: read cached interview-pack columns. Avoids per-row
        # synchronous Claude calls that previously froze the pipeline page
        # under 1 uvicorn worker. Packs are refreshed on detail-view loads
        # and on explicit refresh endpoints.
        interview_support = {
            "screening_pack": app.screening_pack,
            "tech_interview_pack": app.tech_interview_pack,
            "screening_interview_summary": app.screening_interview_summary,
            "tech_interview_summary": app.tech_interview_summary,
            "interview_evidence_summary": app.interview_evidence_summary,
        }
    else:
        interview_support = refresh_application_interview_support(
            app,
            organization=getattr(app, "organization", None),
        )
    interviews = []
    for interview in app.interviews or []:
        interviews.append(
            {
                "id": interview.id,
                "application_id": interview.application_id,
                "organization_id": interview.organization_id,
                "stage": interview.stage,
                "source": interview.source,
                "provider": interview.provider,
                "provider_meeting_id": interview.provider_meeting_id,
                "provider_url": interview.provider_url,
                "status": interview.status,
                "transcript_text": interview.transcript_text,
                "summary": interview.summary,
                "speakers": interview.speakers if isinstance(interview.speakers, list) else [],
                "provider_payload": interview.provider_payload if isinstance(interview.provider_payload, dict) else None,
                "meeting_date": interview.meeting_date,
                "linked_at": interview.linked_at,
                "created_at": interview.created_at,
                "updated_at": interview.updated_at,
            }
        )

    return ApplicationResponse(
        id=app.id,
        organization_id=app.organization_id,
        candidate_id=app.candidate_id,
        role_id=app.role_id,
        status=app.status,
        pipeline_stage=app.pipeline_stage,
        pipeline_stage_updated_at=app.pipeline_stage_updated_at,
        pipeline_stage_source=app.pipeline_stage_source,
        application_outcome=app.application_outcome,
        application_outcome_updated_at=app.application_outcome_updated_at,
        external_refs=(app.external_refs if isinstance(app.external_refs, dict) else None),
        external_stage_raw=app.external_stage_raw,
        external_stage_normalized=app.external_stage_normalized,
        integration_sync_state=(app.integration_sync_state if isinstance(app.integration_sync_state, dict) else None),
        pipeline_external_drift=stage_external_drift(app),
        version=int(app.version or 1),
        notes=app.notes,
        candidate_email=(candidate.email if candidate else ""),
        candidate_name=(candidate.full_name if candidate else None),
        candidate_position=(candidate.position if candidate else None),
        role_name=(getattr(app.role, "name", None) if getattr(app, "role", None) else None),
        cv_filename=app.cv_filename or (candidate.cv_filename if candidate else None),
        cv_uploaded_at=app.cv_uploaded_at or (candidate.cv_uploaded_at if candidate else None),
        cv_match_score=cv_match_score,
        cv_match_details=cv_match_details or None,
        cv_match_scored_at=app.cv_match_scored_at,
        score_status=score_status,
        source=app.source,
        workable_candidate_id=app.workable_candidate_id,
        workable_stage=app.workable_stage,
        workable_score_raw=app.workable_score_raw,
        workable_score=app.workable_score,
        workable_score_source=app.workable_score_source,
        workable_disqualified=app.workable_disqualified,
        workable_disqualified_at=app.workable_disqualified_at,
        rank_score=app.rank_score,
        candidate_headline=(candidate.headline if candidate else None),
        candidate_image_url=(candidate.image_url if candidate else None),
        candidate_location=_candidate_location(candidate),
        candidate_phone=(candidate.phone if candidate else None),
        candidate_profile_url=(candidate.profile_url if candidate else None),
        candidate_social_profiles=(candidate.social_profiles if candidate else None),
        candidate_tags=(candidate.tags if candidate else None),
        candidate_skills=(candidate.skills if candidate else None),
        candidate_education=(candidate.education_entries if candidate else None),
        candidate_experience=(candidate.experience_entries if candidate else None),
        candidate_summary=(candidate.summary if candidate else None),
        candidate_workable_created_at=(candidate.workable_created_at if candidate else None),
        workable_sourced=app.workable_sourced,
        workable_profile_url=app.workable_profile_url,
        workable_enriched=(candidate.workable_enriched if candidate else None),
        pre_screen_score=pre_screen.get("pre_screen_score"),
        requirements_fit_score=pre_screen.get("requirements_fit_score"),
        pre_screen_recommendation=pre_screen.get("pre_screen_recommendation"),
        pre_screen_evidence=pre_screen.get("pre_screen_evidence"),
        pre_screen_run_at=getattr(app, "pre_screen_run_at", None),
        graph_synced_at=_graph_synced_at_for(app),
        graph_stale=_graph_stale_for(app),
        auto_reject_state=app.auto_reject_state,
        auto_reject_reason=app.auto_reject_reason,
        auto_reject_triggered_at=app.auto_reject_triggered_at,
        screening_pack=interview_support.get("screening_pack"),
        tech_interview_pack=interview_support.get("tech_interview_pack"),
        screening_interview_summary=interview_support.get("screening_interview_summary"),
        tech_interview_summary=interview_support.get("tech_interview_summary"),
        interview_evidence_summary=interview_support.get("interview_evidence_summary"),
        interviews=interviews,
        taali_score=score_summary.get("taali_score"),
        score_mode=score_summary.get("mode"),
        valid_assessment_id=score_summary.get("assessment_id"),
        valid_assessment_status=score_summary.get("assessment_status"),
        score_summary=score_summary,
        created_at=app.created_at,
        updated_at=app.updated_at,
        last_activity_at=_last_activity_at(app),
    )


def application_detail_payload(
    app: CandidateApplication,
    *,
    include_cv_text: bool,
    client_safe: bool = False,
) -> dict[str, Any]:
    from ...services.candidate_interview_kit import build_candidate_interview_kit_for_application

    # Use the cached score summary + cached interview support — the
    # non-cached path runs ``maybe_generate_tech_questions`` (a Claude
    # call) synchronously inside the GET, which made every candidate
    # detail load 20+ seconds. Caches are refreshed by the scoring
    # orchestrator on every successful score and by interview webhooks.
    data = application_to_response(app, use_cached_score_summary=True)
    payload = data.model_dump()
    if include_cv_text:
        cv = (app.cv_text or "").strip()
        if not cv and app.candidate:
            cv = (app.candidate.cv_text or "").strip()
        payload["cv_text"] = cv or None
    else:
        payload["cv_text"] = None
    cv_sections = app.cv_sections if isinstance(app.cv_sections, dict) else None
    if cv_sections is None and app.candidate and isinstance(app.candidate.cv_sections, dict):
        cv_sections = app.candidate.cv_sections
    payload["cv_sections"] = cv_sections
    payload["assessment_preview"] = _assessment_preview_for_application(app)
    payload["assessment_history"] = _assessment_history_for_application(app)
    payload["candidate_interview_kit"] = build_candidate_interview_kit_for_application(app)

    # Structured Workable surfaces for the Notes tab — recruiter comments,
    # LinkedIn/questionnaire answers, and the activity log we already sync
    # onto the candidate. Detail-only; stripped below for client shares.
    from ...services.workable_context_service import (
        workable_activity_log,
        workable_questionnaire_answers,
        workable_recruiter_comments,
    )

    candidate = getattr(app, "candidate", None)
    payload["workable_comments"] = workable_recruiter_comments(candidate)
    payload["workable_questionnaire_answers"] = workable_questionnaire_answers(candidate)
    payload["workable_activity_log"] = workable_activity_log(candidate)

    if client_safe:
        # Strip recruiter-internal fields so an external client share
        # (e.g. a hiring-manager-at-a-customer link) cannot read recruiter
        # comments, our internal interview prep, raw assessment transcripts,
        # or per-stage internal scoring breakdowns. Defense-in-depth: the
        # frontend client view also hides these tabs.
        payload["notes"] = None
        payload["candidate_interview_kit"] = None
        payload["assessment_history"] = []
        # Recruiter-internal interview prep / transcripts must never reach an
        # external client share.
        payload["screening_pack"] = None
        payload["tech_interview_pack"] = None
        payload["screening_interview_summary"] = None
        payload["tech_interview_summary"] = None
        payload["interview_evidence_summary"] = None
        payload["interviews"] = []
        # Workable recruiter comments + activity are recruiter-internal.
        payload["workable_comments"] = []
        payload["workable_questionnaire_answers"] = []
        payload["workable_activity_log"] = []
        if isinstance(payload.get("score_summary"), dict):
            ss = dict(payload["score_summary"])
            for k in (
                "rubric_breakdown",
                "weighting",
                "internal_notes",
                "claude_chat_log",
                "judge_rationale",
            ):
                ss.pop(k, None)
            payload["score_summary"] = ss
        payload["recruiter_notes"] = None
        payload["client_share_summary"] = _build_client_share_summary(app, payload)
    return payload


def _build_client_share_summary(app: CandidateApplication, payload: dict[str, Any]) -> dict[str, Any]:
    """Compose a small "why we're sharing this candidate" header for the
    external client view. Fully derived from existing cached fields — no
    new Claude calls.
    """
    from datetime import datetime, timezone

    role = getattr(app, "role", None)
    role_name = getattr(role, "name", None) or "this role"

    score_100: float | None = None
    summary_obj = payload.get("score_summary")
    if isinstance(summary_obj, dict):
        for k in ("taali_score_100", "score_100", "overall_score"):
            v = summary_obj.get(k)
            if isinstance(v, (int, float)):
                score_100 = float(v)
                break

    if score_100 is None and getattr(app, "taali_score_cache_100", None) is not None:
        try:
            score_100 = float(app.taali_score_cache_100)
        except (TypeError, ValueError):
            score_100 = None

    if score_100 is None:
        verdict = "Recommended for review"
        band = "na"
    elif score_100 >= 85:
        verdict = "Strong match — recommended"
        band = "strong"
    elif score_100 >= 70:
        verdict = "Good fit — recommended"
        band = "good"
    elif score_100 >= 55:
        verdict = "Standard fit — worth a conversation"
        band = "standard"
    else:
        verdict = "Recommended for review"
        band = "standard"

    highlights: list[str] = []
    cv_match = payload.get("cv_match") or {}
    if isinstance(cv_match, dict):
        hl = cv_match.get("experience_highlights")
        if isinstance(hl, list):
            for item in hl[:3]:
                text = str(item or "").strip()
                if text:
                    highlights.append(text[:240])

    if not highlights and isinstance(summary_obj, dict):
        bullets = summary_obj.get("highlights") or summary_obj.get("strengths")
        if isinstance(bullets, list):
            for item in bullets[:3]:
                text = str(item or "").strip()
                if text:
                    highlights.append(text[:240])

    return {
        "role": role_name,
        "verdict": verdict,
        "verdict_band": band,
        "score_100": score_100,
        "highlights": highlights,
        "shared_at": datetime.now(timezone.utc).isoformat(),
    }


# Detail-only fields stripped from list rows. Measured on a 343-applicant
# role, these accounted for ~93% of a 12.6MB response (cv_match_details alone
# was 57%). None of them are rendered in the role table, candidates directory,
# or pipeline kanban — they're only used by the candidate detail/report pages,
# which re-fetch the full payload via the /applications/{id} detail endpoint.
# Scores themselves (cv_match_score, pre_screen_score, score_summary) stay.
_LIST_OMITTED_HEAVY_FIELDS = (
    "cv_match_details",            # ~57% of payload — full per-requirement evidence
    "screening_pack",             # ~18% — generated screening interview questions
    "tech_interview_pack",        # ~12% — generated technical interview questions
    "interview_evidence_summary",  # ~4%
    "tech_interview_summary",
    "screening_interview_summary",
    "candidate_experience",       # full work-history array; report-only
    "candidate_education",        # full education array; report-only
)


def application_list_payload(
    app: CandidateApplication,
    *,
    include_cv_text: bool,
    score_status: Any = _UNSET,
    pending_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = application_to_response(
        app,
        use_cached_score_summary=True,
        score_status=score_status,
    )
    payload = data.model_dump()
    # Resolved by the list route in one batch query (see _pending_decision_map)
    # so the AGENT column shows a chip for every row that has a pending
    # decision, not just the first page of a capped decisions fetch.
    payload["pending_decision"] = pending_decision
    if include_cv_text:
        cv = (app.cv_text or "").strip()
        if not cv and app.candidate:
            cv = (app.candidate.cv_text or "").strip()
        payload["cv_text"] = cv or None
    else:
        payload["cv_text"] = None
    payload["assessment_preview"] = None
    payload["assessment_history"] = []
    # Strip heavy detail-only fields (see note above) to keep list responses
    # small. The detail endpoint serves the full payload when a row is opened.
    for key in _LIST_OMITTED_HEAVY_FIELDS:
        if key in payload:
            payload[key] = None
    # Interview transcripts + raw provider payloads are bulky and never
    # rendered in a list row — drop them here (list-only) while the detail
    # endpoint keeps them intact.
    if isinstance(payload.get("interviews"), list):
        for interview in payload["interviews"]:
            if isinstance(interview, dict):
                interview["transcript_text"] = None
                interview["provider_payload"] = None
    return payload
