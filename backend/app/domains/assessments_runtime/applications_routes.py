from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
import secrets
import threading
from datetime import datetime, timedelta, timezone
from time import perf_counter
import re

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy import asc, desc, func, or_
from sqlalchemy.orm import Session, joinedload

from ...components.assessments.repository import assessment_to_response, utcnow
from ...components.assessments.service import get_assessment_creation_gate
from ...components.integrations.workable.sync_service import _extract_candidate_fields
from ...deps import get_current_user, get_optional_current_user
from ...domains.integrations_notifications.invite_flow import dispatch_assessment_invite
from ...models.assessment import Assessment
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent
from ...models.application_interview import ApplicationInterview
from ...models.cv_score_job import CvScoreJob, SCORE_JOB_DONE, SCORE_JOB_ERROR, SCORE_JOB_PENDING, SCORE_JOB_RUNNING
from ...models.organization import Organization
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
from ...platform.config import settings
from ...platform.database import SessionLocal, get_db
from ...platform.request_context import get_request_id
from ...platform.secrets import decrypt_text
from ...schemas.role import (
    ApplicationCreate,
    ApplicationCvUploadResponse,
    ApplicationDetailResponse,
    ApplicationEventResponse,
    ApplicationInterviewResponse,
    ApplicationOutcomeUpdate,
    ApplicationReportShareLinkResponse,
    ApplicationResponse,
    ApplicationStageUpdate,
    ApplicationUpdate,
    AssessmentFromApplicationCreate,
    AssessmentRetakeCreate,
    FirefliesInterviewLinkCreate,
    ManualApplicationInterviewCreate,
)
from ...components.integrations.workable.service import WorkableRateLimitError, WorkableService
from ...services.document_service import (
    MAX_FILE_SIZE,
    extract_text,
    load_stored_document_bytes,
    process_document_upload,
    sanitize_json_for_storage,
    sanitize_text_for_storage,
    save_file_locally,
)
from ...services.candidate_feedback_engine import (
    build_client_application_report_payload,
    build_client_assessment_summary_pdf,
    build_interview_debrief_payload_for_application,
)
from ...services.application_automation_service import run_auto_reject_if_needed
from ...services.fireflies_service import (
    FirefliesService,
    attach_fireflies_match_metadata,
    normalized_transcript_bundle,
)
from ...services.application_events import on_application_created
from ...services.cv_score_orchestrator import (
    enqueue_score,
    latest_score_status,
    mark_role_scores_stale,
)
from ...services.interview_support_service import refresh_application_interview_support
from ...services.pre_screening_service import refresh_pre_screening_fields
from ...services.workable_actions_service import (
    disqualify_candidate_in_workable,
    revert_candidate_disqualification_in_workable,
)
from ...services.assessment_repository_service import (
    AssessmentRepositoryError,
    AssessmentRepositoryService,
)
from .role_support import (
    application_list_payload,
    application_detail_payload,
    application_to_response,
    get_application,
    get_role,
    latest_valid_role_assessment,
    refresh_application_score_cache,
    role_has_job_spec,
)
from .pipeline_service import (
    append_application_event,
    apply_legacy_status_update,
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    list_application_events,
    map_legacy_status_to_pipeline,
    transition_outcome,
    transition_stage,
)

router = APIRouter(tags=["Roles"])
logger = logging.getLogger("taali.applications")

PIPELINE_STAGE_VALUES = {"applied", "invited", "in_assessment", "review"}
APPLICATION_OUTCOME_VALUES = {"open", "rejected", "withdrawn", "hired"}


def _application_is_workable_linked(app: CandidateApplication) -> bool:
    return bool(sanitize_text_for_storage(str(getattr(app, "workable_candidate_id", None) or "").strip()))


def _generate_application_report_share_token() -> str:
    return f"shr_{secrets.token_urlsafe(18)}"


def _build_application_report_share_url(application_id: int, share_token: str) -> str:
    frontend_base = str(settings.FRONTEND_URL or "").rstrip("/")
    path = f"/c/{application_id}?view=interview&k={share_token}"
    if not frontend_base:
        return path
    return f"{frontend_base}{path}"


def _application_report_share_response(app: CandidateApplication) -> ApplicationReportShareLinkResponse:
    token = sanitize_text_for_storage(str(app.report_share_token or "").strip())
    if not token:
        raise HTTPException(status_code=500, detail="Candidate report share link is unavailable.")
    created_at = app.report_share_created_at or app.updated_at or app.created_at or utcnow()
    return ApplicationReportShareLinkResponse(
        application_id=app.id,
        share_token=token,
        share_url=_build_application_report_share_url(app.id, token),
        created_at=created_at,
        member_access_only=False,
    )


def _ensure_application_report_share_link(*, db: Session, app: CandidateApplication) -> CandidateApplication:
    existing_token = sanitize_text_for_storage(str(app.report_share_token or "").strip())
    if existing_token:
        if app.report_share_created_at is None:
            app.report_share_created_at = app.updated_at or app.created_at or utcnow()
            db.add(app)
            db.commit()
            db.refresh(app)
        return app

    for _ in range(5):
        app.report_share_token = _generate_application_report_share_token()
        app.report_share_created_at = utcnow()
        db.add(app)
        try:
            db.commit()
            db.refresh(app)
            return app
        except IntegrityError:
            db.rollback()

    raise HTTPException(status_code=500, detail="Failed to create candidate report share link.")


def _get_application_by_share_token(
    share_token: str,
    *,
    org_id: int | None = None,
    db: Session,
) -> CandidateApplication:
    normalized_token = sanitize_text_for_storage(str(share_token or "").strip())
    if not normalized_token or not normalized_token.startswith("shr_"):
        raise HTTPException(status_code=404, detail="Candidate report unavailable.")

    query = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
            joinedload(CandidateApplication.interviews),
            joinedload(CandidateApplication.assessments).joinedload(Assessment.task),
        )
        .filter(
            CandidateApplication.report_share_token == normalized_token,
            CandidateApplication.deleted_at.is_(None),
        )
    )

    if org_id is not None:
        query = query.filter(CandidateApplication.organization_id == org_id)

    app = query.first()
    if not app:
        raise HTTPException(status_code=404, detail="Candidate report unavailable.")
    return app


def _sync_workable_outcome_change(
    *,
    db: Session,
    app: CandidateApplication,
    target_outcome: str,
    current_user: User,
    reason: str | None = None,
) -> dict | None:
    current_outcome = sanitize_text_for_storage(str(app.application_outcome or "").strip()) or "open"
    normalized_target = sanitize_text_for_storage(str(target_outcome or "").strip()) or current_outcome
    if not _application_is_workable_linked(app):
        return None
    if normalized_target == current_outcome:
        return None
    if (current_outcome, normalized_target) not in {("open", "rejected"), ("rejected", "open")}:
        return None

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if normalized_target == "rejected":
        result = disqualify_candidate_in_workable(
            org=org,
            app=app,
            role=app.role,
            reason=reason or "Rejected in TAALI",
            withdrew=False,
        )
        if not result.get("success"):
            append_application_event(
                db,
                app=app,
                event_type="workable_writeback_failed",
                actor_type="recruiter",
                actor_id=current_user.id,
                reason=result.get("message") or reason or "Failed to reject candidate in Workable",
                metadata={
                    "action": result.get("action"),
                    "code": result.get("code"),
                    "target_outcome": normalized_target,
                    "workable_candidate_id": app.workable_candidate_id,
                },
            )
            db.commit()
            raise HTTPException(status_code=502, detail=result.get("message") or "Failed to reject candidate in Workable")
        return result

    result = revert_candidate_disqualification_in_workable(
        org=org,
        app=app,
        role=app.role,
    )
    if not result.get("success"):
        append_application_event(
            db,
            app=app,
            event_type="workable_writeback_failed",
            actor_type="recruiter",
            actor_id=current_user.id,
            reason=result.get("message") or reason or "Failed to reopen candidate in Workable",
            metadata={
                "action": result.get("action"),
                "code": result.get("code"),
                "target_outcome": normalized_target,
                "workable_candidate_id": app.workable_candidate_id,
            },
        )
        db.commit()
        raise HTTPException(status_code=502, detail=result.get("message") or "Failed to reopen candidate in Workable")
    return result


def _report_filename_part(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", str(value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned or fallback


def _refresh_rank_score(app: CandidateApplication) -> None:
    refresh_pre_screening_fields(app)


# Scoring is owned by services.cv_score_orchestrator. Routes call
# enqueue_score(db, app); inline (MVP_DISABLE_CELERY) and Celery paths share
# the same _execute_scoring body. The legacy _compute_cv_match_for_application
# helper that lived here was deleted in the move to async + cached scoring.


def _latest_active_assessment_for_application(app: CandidateApplication, db: Session) -> Assessment | None:
    return latest_valid_role_assessment(
        candidate_id=app.candidate_id,
        role_id=app.role_id,
        org_id=app.organization_id,
        db=db,
    )


def _assessment_create_conflict_response(existing: Assessment) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": "A valid assessment already exists for this candidate and role. Use retake instead.",
            "code": "retake_required",
            "assessment_id": existing.id,
            "assessment_status": (
                existing.status.value if hasattr(existing.status, "value") else str(existing.status)
            ),
        },
    )


def _is_active_role_assessment_integrity_error(err: Exception) -> bool:
    if not isinstance(err, IntegrityError):
        return False
    message = str(getattr(err, "orig", err)).lower()
    return (
        "uq_assessments_candidate_role_active" in message
        or ("assessments.candidate_id" in message and "assessments.role_id" in message and "unique" in message)
    )


def _application_sort_value(item: ApplicationDetailResponse, sort_by: str):
    if sort_by == "pre_screen_score":
        return item.pre_screen_score if item.pre_screen_score is not None else float("-inf")
    if sort_by == "taali_score":
        normalized = _normalize_taali_score_for_filter(item.taali_score)
        if normalized is not None:
            return normalized
        # Fall back to the CV match (pre-screen) score so unified-column sort
        # matches what the row displays — most candidates haven't done an
        # assessment, so taali_score is NULL until then.
        if item.pre_screen_score is not None:
            return float(item.pre_screen_score)
        return float("-inf")
    if sort_by == "cv_match_score":
        return getattr(item, "cv_match_score", None) if getattr(item, "cv_match_score", None) is not None else float("-inf")
    if sort_by == "cv_match_scored_at":
        scored_at = getattr(item, "cv_match_scored_at", None)
        return scored_at or datetime.min.replace(tzinfo=timezone.utc)
    if sort_by == "created_at":
        return item.created_at or datetime.min.replace(tzinfo=timezone.utc)
    return (
        item.pipeline_stage_updated_at
        or item.updated_at
        or item.created_at
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _sort_application_payload(
    payload: list[ApplicationDetailResponse],
    *,
    sort_by: str,
    sort_order: str,
) -> list[ApplicationDetailResponse]:
    reverse = sort_order != "asc"
    payload.sort(
        key=lambda item: (
            _application_sort_value(item, sort_by),
            item.created_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=reverse,
    )
    return payload


def _apply_min_taali_score_filter(
    payload: list[ApplicationDetailResponse],
    *,
    min_taali_score: float | None,
) -> list[ApplicationDetailResponse]:
    if min_taali_score is None:
        return payload
    threshold = _normalize_taali_score_for_filter(min_taali_score)
    if threshold is None:
        return payload
    filtered: list[ApplicationDetailResponse] = []
    for item in payload:
        normalized = _normalize_taali_score_for_filter(item.taali_score)
        if normalized is None:
            continue
        if normalized >= threshold:
            filtered.append(item)
    return filtered


def _normalize_taali_score_for_filter(value: float | int | None) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    # Legacy payloads can still surface 0-10 scale values.
    if numeric <= 10:
        numeric = numeric * 10.0
    return round(max(0.0, min(100.0, numeric)), 1)


def _parse_csv_tokens(raw_value: str | None) -> list[str]:
    if raw_value is None:
        return []
    return [
        token.strip()
        for token in str(raw_value).split(",")
        if token and token.strip()
    ]


def _parse_int_csv_filter(raw_value: str | None, *, field_name: str) -> list[int]:
    tokens = _parse_csv_tokens(raw_value)
    values: list[int] = []
    for token in tokens:
        try:
            parsed = int(token)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid {field_name} value '{token}'. Expected comma-separated integers.",
            ) from None
        if parsed <= 0:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid {field_name} value '{token}'. Expected positive integers.",
            )
        values.append(parsed)
    return values


def _parse_choice_csv_filter(raw_value: str | None, *, allowed: set[str], field_name: str) -> list[str]:
    tokens = [token.lower() for token in _parse_csv_tokens(raw_value)]
    if not tokens:
        return []
    if "all" in tokens:
        return []
    invalid = [token for token in tokens if token not in allowed]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field_name} value(s): {', '.join(sorted(set(invalid)))}",
        )
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _empty_stage_counts() -> dict[str, int]:
    return {
        "all": 0,
        "applied": 0,
        "invited": 0,
        "in_assessment": 0,
        "review": 0,
    }


def _build_stage_counts(stage_rows: list[tuple[str | None, int]]) -> dict[str, int]:
    counts = _empty_stage_counts()
    for stage, total in stage_rows:
        key = str(stage or "").strip().lower()
        if key in counts:
            counts[key] = int(total or 0)
    counts["all"] = int(sum(counts[key] for key in ("applied", "invited", "in_assessment", "review")))
    return counts


def _application_order_columns(sort_by: str, sort_order: str):
    reverse = sort_order != "asc"
    if sort_by == "pre_screen_score":
        primary = func.coalesce(
            CandidateApplication.pre_screen_score_100,
            -1.0 if reverse else 101.0,
        )
    elif sort_by == "taali_score":
        # Coalesce to pre-screen so the sort matches the unified score column.
        primary = func.coalesce(
            CandidateApplication.taali_score_cache_100,
            CandidateApplication.pre_screen_score_100,
            -1.0 if reverse else 101.0,
        )
    elif sort_by == "cv_match_score":
        primary = func.coalesce(
            CandidateApplication.cv_match_score,
            -1.0 if reverse else 101.0,
        )
    elif sort_by == "cv_match_scored_at":
        # "Newest scored first" — recently scored apps float to the top.
        # NULLs sort last in either direction so unscored candidates don't
        # crowd the top during an active batch.
        unscored_anchor = (
            datetime.min.replace(tzinfo=timezone.utc)
            if reverse
            else datetime.max.replace(tzinfo=timezone.utc)
        )
        primary = func.coalesce(
            CandidateApplication.cv_match_scored_at,
            unscored_anchor,
        )
    elif sort_by == "created_at":
        primary = CandidateApplication.created_at
    else:
        primary = func.coalesce(
            CandidateApplication.pipeline_stage_updated_at,
            CandidateApplication.updated_at,
            CandidateApplication.created_at,
        )
    if reverse:
        return [primary.desc(), CandidateApplication.created_at.desc(), CandidateApplication.id.desc()]
    return [primary.asc(), CandidateApplication.created_at.asc(), CandidateApplication.id.asc()]


def _apply_application_source_filter(query, source: str | None):
    normalized = str(source or "").strip().lower()
    if normalized == "workable":
        return query.filter(
            or_(
                CandidateApplication.source == "workable",
                CandidateApplication.workable_sourced.is_(True),
            )
        )
    if normalized == "manual":
        return query.filter(
            CandidateApplication.source != "workable",
            or_(
                CandidateApplication.workable_sourced.is_(False),
                CandidateApplication.workable_sourced.is_(None),
            ),
        )
    return query


def _provision_assessment_branch(assessment: Assessment, task: Task) -> None:
    repo_service = AssessmentRepositoryService(settings.GITHUB_ORG, settings.GITHUB_TOKEN)
    branch_ctx = repo_service.create_assessment_branch(task, assessment.id)
    assessment.assessment_repo_url = branch_ctx.repo_url
    assessment.assessment_branch = branch_ctx.branch_name
    assessment.clone_command = branch_ctx.clone_command


def _create_application_assessment(
    *,
    app: CandidateApplication,
    role: Role,
    task: Task,
    duration_minutes: int,
    current_user: User,
    db: Session,
    void_existing: Assessment | None = None,
    void_reason: str | None = None,
) -> Assessment:
    token = secrets.token_urlsafe(32)
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if void_existing is not None:
        void_existing.is_voided = True
        void_existing.voided_at = utcnow()
        void_existing.void_reason = (void_reason or "").strip() or "Superseded by retake"
    assessment = Assessment(
        organization_id=current_user.organization_id,
        candidate_id=app.candidate_id,
        task_id=task.id,
        role_id=role.id,
        application_id=app.id,
        token=token,
        duration_minutes=duration_minutes,
        expires_at=utcnow() + timedelta(days=settings.ASSESSMENT_EXPIRY_DAYS),
        workable_candidate_id=app.workable_candidate_id,
        workable_job_id=role.workable_job_id,
        candidate_feedback_enabled=bool(getattr(org, "candidate_feedback_enabled", True)) if org else True,
    )
    db.add(assessment)
    db.flush()

    if void_existing is not None:
        void_existing.superseded_by_assessment_id = assessment.id

    _provision_assessment_branch(assessment, task)
    db.commit()
    db.refresh(assessment)

    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task), joinedload(Assessment.role), joinedload(Assessment.application))
        .filter(Assessment.id == assessment.id)
        .first()
    )

    candidate_email = app.candidate.email if app.candidate else None
    if not candidate_email:
        raise HTTPException(status_code=400, detail="Application has no candidate email")
    candidate_name = app.candidate.full_name or app.candidate.email

    if org:
        dispatch_assessment_invite(
            assessment=assessment,
            org=org,
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            position=task.name or "Technical assessment",
        )
        try:
            db.commit()
            db.refresh(assessment)
        except Exception:
            db.rollback()
            logger.exception("Failed to persist invite metadata for assessment_id=%s", assessment.id)
    return assessment


@router.post("/roles/{role_id}/applications", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
def create_application(
    role_id: int,
    data: ApplicationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    if not role_has_job_spec(role):
        raise HTTPException(status_code=400, detail="Upload job spec before adding applications")
    candidate = db.query(Candidate).filter(
        Candidate.organization_id == current_user.organization_id,
        Candidate.email == str(data.candidate_email),
    ).first()
    if not candidate:
        candidate = Candidate(
            organization_id=current_user.organization_id,
            email=str(data.candidate_email),
            full_name=data.candidate_name or None,
            position=data.candidate_position or None,
        )
        db.add(candidate)
        db.flush()
    else:
        if data.candidate_name:
            candidate.full_name = data.candidate_name
        if data.candidate_position:
            candidate.position = data.candidate_position

    existing = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == current_user.organization_id,
        CandidateApplication.candidate_id == candidate.id,
        CandidateApplication.role_id == role.id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Candidate already has an application for this role")

    mapped_stage, mapped_outcome = map_legacy_status_to_pipeline(data.status)
    pipeline_stage = data.pipeline_stage or mapped_stage
    application_outcome = data.application_outcome or mapped_outcome
    now = utcnow()
    app = CandidateApplication(
        organization_id=current_user.organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status=data.status or pipeline_stage,
        pipeline_stage=pipeline_stage,
        pipeline_stage_updated_at=now,
        pipeline_stage_source="recruiter",
        application_outcome=application_outcome,
        application_outcome_updated_at=now,
        version=1,
        notes=data.notes or None,
    )
    db.add(app)
    ensure_pipeline_fields(app, source="recruiter")
    db.flush()
    initialize_pipeline_event_if_missing(
        db,
        app=app,
        actor_type="recruiter",
        actor_id=current_user.id,
        reason="Application created",
    )
    refresh_application_score_cache(app, db=db)
    try:
        db.commit()
        db.refresh(app)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create application")
    app = get_application(app.id, current_user.organization_id, db)
    return application_to_response(app)


@router.get("/roles/{role_id}/applications")
def list_role_applications(
    role_id: int,
    sort_by: str = Query(
        default="pre_screen_score",
        pattern="^(pre_screen_score|rank_score|workable_score|cv_match_score|cv_match_scored_at|taali_score|created_at)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    min_pre_screen_score: float | None = Query(default=None),
    min_rank_score: float | None = Query(default=None),
    min_workable_score: float | None = Query(default=None),
    min_cv_match_score: float | None = Query(default=None),
    source: str | None = Query(default=None, pattern="^(manual|workable)$"),
    status: str | None = Query(default=None, description="Filter by application status (e.g. applied, shortlisted)"),
    pipeline_stage: str | None = Query(default=None),
    application_outcome: str | None = Query(default=None),
    include_cv_text: bool = Query(False, description="Include full CV text for each application (for viewer)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_role(role_id, current_user.organization_id, db)
    query = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate).joinedload(Candidate.graph_sync_state),
            joinedload(CandidateApplication.organization),
            joinedload(CandidateApplication.role),
            joinedload(CandidateApplication.interviews),
            joinedload(CandidateApplication.score_jobs),
            joinedload(CandidateApplication.assessments).joinedload(Assessment.task),
        )
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
        )
    )
    if source:
        query = query.filter(CandidateApplication.source == source)
    if status and status.strip().lower() != "all":
        query = query.filter(CandidateApplication.status.ilike(status.strip()))
    if pipeline_stage and pipeline_stage.strip().lower() != "all":
        query = query.filter(CandidateApplication.pipeline_stage == pipeline_stage.strip().lower())
    if application_outcome and application_outcome.strip().lower() != "all":
        query = query.filter(CandidateApplication.application_outcome == application_outcome.strip().lower())
    if min_pre_screen_score is not None:
        threshold = _normalize_taali_score_for_filter(min_pre_screen_score)
        if threshold is not None:
            query = query.filter(CandidateApplication.pre_screen_score_100 >= threshold)
    if min_rank_score is not None:
        query = query.filter(CandidateApplication.rank_score >= min_rank_score)
    if min_workable_score is not None:
        query = query.filter(CandidateApplication.workable_score >= min_workable_score)
    if min_cv_match_score is not None:
        threshold = float(min_cv_match_score)
        if 0 <= threshold <= 10:
            threshold *= 10.0
        query = query.filter(CandidateApplication.cv_match_score >= threshold)

    apps = query.all()

    # List context: use the cached-score payload to avoid per-row Anthropic
    # calls (interview-pack regeneration). Detail-only fields (cv_sections,
    # assessment_preview, assessment_history, candidate_interview_kit) stay
    # absent and the schema treats them as optional. The dedicated detail
    # endpoint below still uses application_detail_payload.
    out = [ApplicationDetailResponse(**application_list_payload(app, include_cv_text=include_cv_text)) for app in apps]

    def _sort_value(item: ApplicationDetailResponse):
        if sort_by == "pre_screen_score":
            return item.pre_screen_score if item.pre_screen_score is not None else float("-inf")
        if sort_by == "taali_score":
            return item.taali_score if item.taali_score is not None else float("-inf")
        if sort_by == "rank_score":
            return item.rank_score if item.rank_score is not None else float("-inf")
        if sort_by == "workable_score":
            return item.workable_score if item.workable_score is not None else float("-inf")
        if sort_by == "cv_match_score":
            return item.cv_match_score if item.cv_match_score is not None else float("-inf")
        return item.created_at or datetime.min.replace(tzinfo=timezone.utc)

    reverse = sort_order != "asc"
    out.sort(
        key=lambda item: (_sort_value(item), item.created_at or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=reverse,
    )
    return out


@router.get("/applications/{application_id}", response_model=ApplicationDetailResponse)
def get_application_detail(
    application_id: int,
    include_cv_text: bool = Query(False, description="Include full CV extracted text for viewer"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single application; optionally include full cv_text for CV viewer sidebar."""
    app = get_application(application_id, current_user.organization_id, db)
    return ApplicationDetailResponse(**application_detail_payload(app, include_cv_text=include_cv_text))


@router.get("/applications/share/{share_token}", response_model=ApplicationDetailResponse)
def get_application_detail_by_share_token(
    share_token: str,
    include_cv_text: bool = Query(False, description="Include full CV extracted text for viewer"),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
):
    app = _get_application_by_share_token(
        share_token,
        org_id=current_user.organization_id if current_user else None,
        db=db,
    )
    return ApplicationDetailResponse(**application_detail_payload(app, include_cv_text=include_cv_text))


@router.post("/applications/{application_id}/share-link", response_model=ApplicationReportShareLinkResponse)
def ensure_application_report_share_link(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    app = _ensure_application_report_share_link(db=db, app=app)
    return _application_report_share_response(app)


@router.post("/applications/{application_id}/interview-debrief")
def generate_application_interview_debrief(
    application_id: int,
    body: dict | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    generated_at = utcnow()
    debrief = build_interview_debrief_payload_for_application(app)
    return {
        "success": True,
        "cached": False,
        "generated_at": generated_at,
        "interview_debrief": debrief,
    }


def _fireflies_service_for_org(org: Organization) -> FirefliesService:
    api_key = decrypt_text(getattr(org, "fireflies_api_key_encrypted", None), settings.SECRET_KEY)
    if not api_key:
        raise HTTPException(status_code=400, detail="Fireflies is not configured")
    return FirefliesService(api_key=api_key)


def _application_interview_response(interview: ApplicationInterview) -> ApplicationInterviewResponse:
    return ApplicationInterviewResponse.model_validate(interview)


def _upsert_application_interview(
    *,
    db: Session,
    app: CandidateApplication,
    stage: str,
    source: str,
    provider: str,
    provider_meeting_id: str | None = None,
) -> ApplicationInterview:
    interview = None
    if provider_meeting_id:
        interview = (
            db.query(ApplicationInterview)
            .filter(
                ApplicationInterview.organization_id == app.organization_id,
                ApplicationInterview.application_id == app.id,
                ApplicationInterview.provider == provider,
                ApplicationInterview.provider_meeting_id == provider_meeting_id,
            )
            .first()
        )
    if interview is None:
        interview = ApplicationInterview(
            organization_id=app.organization_id,
            application_id=app.id,
            stage=stage,
            source=source,
            provider=provider,
            provider_meeting_id=provider_meeting_id,
        )
        db.add(interview)
        db.flush()
    interview.stage = stage
    interview.source = source
    interview.provider = provider
    interview.provider_meeting_id = provider_meeting_id
    return interview


@router.post(
    "/applications/{application_id}/interviews",
    response_model=ApplicationInterviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_manual_application_interview(
    application_id: int,
    data: ManualApplicationInterviewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    interview = _upsert_application_interview(
        db=db,
        app=app,
        stage=data.stage,
        source="manual",
        provider="manual",
        provider_meeting_id=None,
    )
    interview.provider_url = sanitize_text_for_storage(str(data.provider_url or "").strip()) or None
    interview.status = "completed"
    interview.transcript_text = sanitize_text_for_storage(data.transcript_text)
    interview.summary = sanitize_text_for_storage(
        str(data.summary or "").strip()
    ) or sanitize_text_for_storage(data.transcript_text[:400])
    interview.speakers = sanitize_json_for_storage(data.speakers or [])
    interview.provider_payload = {
        "source": "manual",
        "captured_by_user_id": current_user.id,
    }
    interview.meeting_date = data.meeting_date or datetime.now(timezone.utc)
    interview.linked_at = datetime.now(timezone.utc)
    refresh_application_interview_support(app)
    try:
        db.commit()
        db.refresh(interview)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save interview transcript")
    return _application_interview_response(interview)


@router.post(
    "/applications/{application_id}/interviews/fireflies-link",
    response_model=ApplicationInterviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def link_fireflies_interview(
    application_id: int,
    data: FirefliesInterviewLinkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    fireflies = _fireflies_service_for_org(org)
    try:
        transcript = fireflies.get_transcript(data.fireflies_meeting_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Fireflies transcript: {exc}") from exc
    if not transcript:
        raise HTTPException(status_code=404, detail="Fireflies transcript not found")
    normalized = normalized_transcript_bundle(transcript)
    interview = _upsert_application_interview(
        db=db,
        app=app,
        stage=data.stage,
        source="fireflies",
        provider="fireflies",
        provider_meeting_id=normalized.get("provider_meeting_id"),
    )
    interview.provider_url = sanitize_text_for_storage(str(data.provider_url or normalized.get("provider_url") or "").strip()) or None
    interview.status = "completed"
    interview.transcript_text = normalized.get("transcript_text")
    interview.summary = normalized.get("summary")
    interview.speakers = normalized.get("speakers") if isinstance(normalized.get("speakers"), list) else []
    interview.provider_payload = attach_fireflies_match_metadata(
        normalized.get("raw") if isinstance(normalized.get("raw"), dict) else {},
        invite_email=getattr(org, "fireflies_invite_email", None),
        linked_via="manual_link",
        matched_application_id=app.id,
        linked_by_user_id=current_user.id,
    )
    interview.meeting_date = normalized.get("meeting_date")
    interview.linked_at = datetime.now(timezone.utc)
    refresh_application_interview_support(app, organization=org)
    try:
        db.commit()
        db.refresh(interview)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to link Fireflies transcript")
    return _application_interview_response(interview)


@router.get("/applications/{application_id}/report.pdf")
def download_application_report_pdf(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    organization = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    payload = build_client_application_report_payload(
        app,
        organization_name=(organization.name if organization and organization.name else "Employer"),
    )
    final_pdf = build_client_assessment_summary_pdf(payload)
    candidate_name = (
        (app.candidate.full_name if getattr(app, "candidate", None) else None)
        or (app.candidate.email if getattr(app, "candidate", None) else "Candidate")
    )
    filename = (
        f"{_report_filename_part(app.role.name if getattr(app, 'role', None) else None, 'Role')}-"
        f"{_report_filename_part(candidate_name, 'Candidate')}.pdf"
    )
    return Response(
        content=final_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/applications/{application_id}/documents/{doc_type}")
def download_application_document(
    application_id: int,
    doc_type: str,
    download: bool = Query(False, description="Return attachment disposition for browser downloads"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return application-scoped CV/job-spec files for inline report previews."""
    app = get_application(application_id, current_user.organization_id, db)
    normalized_doc_type = str(doc_type or "").strip().lower()
    if normalized_doc_type not in {"cv", "job-spec"}:
        raise HTTPException(status_code=400, detail="Unsupported document type")

    if normalized_doc_type == "cv":
        file_url = app.cv_file_url or (app.candidate.cv_file_url if app.candidate else None)
        filename = app.cv_filename or (app.candidate.cv_filename if app.candidate else None) or "candidate-cv"
    else:
        file_url = app.role.job_spec_file_url if app.role else None
        filename = app.role.job_spec_filename if app.role else None
        if not file_url and app.candidate:
            file_url = app.candidate.job_spec_file_url
            filename = app.candidate.job_spec_filename
        filename = filename or "job-spec"

    if not file_url:
        raise HTTPException(status_code=404, detail="Document not found")

    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    safe_filename = _report_filename_part(filename, "document")
    disposition = "attachment" if download else "inline"
    response_headers = {"Content-Disposition": f'{disposition}; filename="{safe_filename}"'}

    stored_bytes = load_stored_document_bytes(file_url)
    if stored_bytes:
        return Response(content=stored_bytes, media_type=media_type, headers=response_headers)

    if file_url.startswith("http://") or file_url.startswith("https://"):
        return RedirectResponse(url=file_url, status_code=307)

    file_path = Path(file_url).resolve()
    if not file_path.exists() or not file_path.is_file():
        # Ephemeral-disk loss (Railway redeploys when S3 is unavailable).
        # 410 surfaces a recoverable "re-upload from Workable" path; 404
        # would imply the candidate never had a CV at all.
        raise HTTPException(
            status_code=410,
            detail={
                "reason": "file_storage_unavailable",
                "message": "CV file expired from local storage. Re-upload from Workable to restore it.",
            },
        )

    return FileResponse(path=str(file_path), filename=filename, media_type=media_type, headers=response_headers)


@router.patch("/applications/{application_id}", response_model=ApplicationResponse)
def update_application(
    application_id: int,
    data: ApplicationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    updates = data.model_dump(exclude_unset=True)
    try:
        ensure_pipeline_fields(app)
        initialize_pipeline_event_if_missing(
            db,
            app=app,
            actor_type="system",
            actor_id=current_user.id,
            reason="Pipeline initialized before update",
        )
        expected_version = updates.get("expected_version")

        if "status" in updates and updates["status"] is not None:
            apply_legacy_status_update(
                db,
                app=app,
                status=updates["status"],
                actor_type="recruiter",
                actor_id=current_user.id,
                reason="Legacy status patch",
                expected_version=expected_version,
            )
        if "pipeline_stage" in updates and updates["pipeline_stage"] is not None:
            transition_stage(
                db,
                app=app,
                to_stage=updates["pipeline_stage"],
                source="recruiter",
                actor_type="recruiter",
                actor_id=current_user.id,
                reason="Application stage patch",
                expected_version=expected_version,
            )
        if "application_outcome" in updates and updates["application_outcome"] is not None:
            transition_outcome(
                db,
                app=app,
                to_outcome=updates["application_outcome"],
                actor_type="recruiter",
                actor_id=current_user.id,
                reason="Application outcome patch",
                expected_version=expected_version,
            )
        if "notes" in updates:
            app.notes = updates["notes"] or None
        if app.candidate:
            if "candidate_name" in updates and updates["candidate_name"] is not None:
                app.candidate.full_name = updates["candidate_name"]
            if "candidate_position" in updates and updates["candidate_position"] is not None:
                app.candidate.position = updates["candidate_position"]
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update application")
    app = get_application(application_id, current_user.organization_id, db)
    return application_to_response(app)


@router.get("/applications")
def list_applications_global(
    role_id: int | None = Query(default=None),
    role_ids: str | None = Query(default=None),
    source: str | None = Query(default=None, pattern="^(manual|workable)$"),
    pipeline_stage: str | None = Query(default=None),
    pipeline_stages: str | None = Query(default=None),
    application_outcome: str | None = Query(default=None),
    application_outcomes: str | None = Query(default=None),
    search: str | None = Query(default=None),
    nl_query: str | None = Query(default=None, max_length=500),
    view: str = Query(default="list", pattern="^(list|graph)$"),
    rerank: bool = Query(default=True),
    sort_by: str = Query(
        default="pre_screen_score",
        pattern="^(pre_screen_score|pipeline_stage_updated_at|created_at|taali_score|cv_match_score|cv_match_scored_at)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    min_pre_screen_score: float | None = Query(default=None, ge=0, le=100),
    min_taali_score: float | None = Query(default=None, ge=0, le=100),
    include_stage_counts: bool = Query(default=True),
    include_cv_text: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = perf_counter()

    # Natural-language search: when nl_query is set, the parser drives
    # filtering. The legacy `search` param is ignored (chips are
    # authoritative — see UI). We compute a parsed filter, narrow the
    # base query to its candidate-id set, then carry on with the existing
    # outcome / stage / sort / pagination path.
    nl_query_clean = (nl_query or "").strip()
    parsed_filter_payload = None
    nl_warnings: list[dict] = []
    nl_subgraph_payload = None
    nl_rerank_applied = False
    if nl_query_clean:
        from ...candidate_search import rate_limit as nl_rate_limit
        from ...candidate_search.runner import run_search

        if not nl_rate_limit.check_and_record(int(current_user.organization_id)):
            raise HTTPException(
                status_code=429,
                detail="Too many natural-language queries — try again in a minute.",
            )

        nl_base = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == current_user.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
        nl_result = run_search(
            db=db,
            organization_id=int(current_user.organization_id),
            nl_query=nl_query_clean,
            base_query=nl_base,
            rerank_enabled=bool(rerank),
            include_subgraph=(view == "graph"),
        )
        parsed_filter_payload = nl_result.parsed_filter.model_dump(mode="json")
        nl_warnings = [w.model_dump(mode="json") for w in nl_result.warnings]
        nl_rerank_applied = nl_result.rerank_applied
        nl_subgraph_payload = (
            nl_result.subgraph.model_dump(mode="json")
            if nl_result.subgraph is not None
            else None
        )

    base_query = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
    )
    # When NL filtering returned a (possibly empty) id set, narrow the
    # primary query before the standard filters apply.
    if nl_query_clean:
        nl_ids = nl_result.application_ids
        if not nl_ids:
            base_query = base_query.filter(CandidateApplication.id.in_([-1]))
        else:
            base_query = base_query.filter(CandidateApplication.id.in_(nl_ids))
    requested_role_ids = _parse_int_csv_filter(role_ids, field_name="role_ids")
    if role_id is not None:
        requested_role_ids = [int(role_id), *requested_role_ids]
    if requested_role_ids:
        unique_role_ids = sorted(set(requested_role_ids))
        if len(unique_role_ids) == 1:
            base_query = base_query.filter(CandidateApplication.role_id == unique_role_ids[0])
        else:
            base_query = base_query.filter(CandidateApplication.role_id.in_(unique_role_ids))
    base_query = _apply_application_source_filter(base_query, source)

    requested_outcomes = _parse_choice_csv_filter(
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
    if requested_outcomes:
        base_query = base_query.filter(CandidateApplication.application_outcome.in_(requested_outcomes))
    if search and not nl_query_clean:
        term = f"%{search.strip()}%"
        # Match across candidate name/email/position AND the role name —
        # the search box placeholder ("name, email, or role") had been
        # lying since v1; recruiters typing a job title saw zero results.
        # When nl_query is set the parsed filter is authoritative and the
        # legacy `search` param is ignored.
        base_query = (
            base_query.join(Candidate, Candidate.id == CandidateApplication.candidate_id)
            .outerjoin(Role, Role.id == CandidateApplication.role_id)
            .filter(
                Candidate.full_name.ilike(term)
                | Candidate.email.ilike(term)
                | Candidate.position.ilike(term)
                | Role.name.ilike(term)
            )
        )
    threshold = _normalize_taali_score_for_filter(min_taali_score)
    if threshold is not None:
        base_query = base_query.filter(
            CandidateApplication.taali_score_cache_100.is_not(None),
            CandidateApplication.taali_score_cache_100 >= threshold,
        )
    pre_screen_threshold = _normalize_taali_score_for_filter(min_pre_screen_score)
    if pre_screen_threshold is not None:
        base_query = base_query.filter(
            CandidateApplication.pre_screen_score_100.is_not(None),
            CandidateApplication.pre_screen_score_100 >= pre_screen_threshold,
        )

    stage_counts = _empty_stage_counts()
    if include_stage_counts:
        stage_rows = (
            base_query.with_entities(
                CandidateApplication.pipeline_stage,
                func.count(CandidateApplication.id),
            )
            .group_by(CandidateApplication.pipeline_stage)
            .all()
        )
        stage_counts = _build_stage_counts(stage_rows)

    requested_stages = _parse_choice_csv_filter(
        pipeline_stages,
        allowed=PIPELINE_STAGE_VALUES,
        field_name="pipeline_stages",
    )
    single_stage = str(pipeline_stage or "").strip().lower()
    if single_stage and single_stage != "all":
        if single_stage not in PIPELINE_STAGE_VALUES:
            raise HTTPException(status_code=422, detail=f"Invalid pipeline_stage value '{single_stage}'")
        if single_stage not in requested_stages:
            requested_stages.append(single_stage)

    filtered_query = base_query
    if requested_stages:
        filtered_query = filtered_query.filter(CandidateApplication.pipeline_stage.in_(requested_stages))

    total = filtered_query.order_by(None).count()
    page_ids = [
        int(row_id)
        for (row_id,) in (
            filtered_query.with_entities(CandidateApplication.id)
            .order_by(*_application_order_columns(sort_by, sort_order))
            .offset(offset)
            .limit(limit)
            .all()
        )
    ]
    rows: list[CandidateApplication] = []
    if page_ids:
        fetched = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.organization),
                joinedload(CandidateApplication.role),
                joinedload(CandidateApplication.interviews),
                joinedload(CandidateApplication.assessments).joinedload(Assessment.task),
            )
            .filter(CandidateApplication.id.in_(page_ids))
            .all()
        )
        by_id = {int(item.id): item for item in fetched}
        rows = [by_id[row_id] for row_id in page_ids if row_id in by_id]

    items = [
        application_list_payload(app, include_cv_text=include_cv_text)
        for app in rows
    ]
    duration_ms = (perf_counter() - started_at) * 1000.0
    logged_role_ids = sorted(set(requested_role_ids))
    logger.info(
        (
            "list_applications_global org_id=%s role_id=%s stage=%s outcome=%s search=%s "
            "source=%s total=%s limit=%s offset=%s sort_by=%s sort_order=%s include_stage_counts=%s duration_ms=%.1f request_id=%s"
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
    response_payload = {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
    if include_stage_counts:
        response_payload["stage_counts"] = stage_counts
    if nl_query_clean:
        response_payload["parsed_filter"] = parsed_filter_payload
        response_payload["nl_warnings"] = nl_warnings
        response_payload["nl_rerank_applied"] = nl_rerank_applied
        if view == "graph" and nl_subgraph_payload is not None:
            response_payload["subgraph"] = nl_subgraph_payload
    return response_payload


@router.get("/roles/{role_id}/pipeline")
def get_role_pipeline(
    role_id: int,
    stage: str | None = Query(default=None),
    stages: str | None = Query(default=None),
    source: str | None = Query(default=None, pattern="^(manual|workable)$"),
    search: str | None = Query(default=None),
    sort_by: str = Query(
        default="pre_screen_score",
        pattern="^(pre_screen_score|pipeline_stage_updated_at|created_at|taali_score|cv_match_score|cv_match_scored_at)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    min_pre_screen_score: float | None = Query(default=None, ge=0, le=100),
    min_taali_score: float | None = Query(default=None, ge=0, le=100),
    include_stage_counts: bool = Query(default=True),
    include_cv_text: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started_at = perf_counter()
    role = get_role(role_id, current_user.organization_id, db)
    base_query = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role.id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
        )
    )
    base_query = _apply_application_source_filter(base_query, source)
    if search:
        term = f"%{search.strip()}%"
        base_query = (
            base_query.join(Candidate, Candidate.id == CandidateApplication.candidate_id)
            .filter(
                Candidate.full_name.ilike(term)
                | Candidate.email.ilike(term)
                | Candidate.position.ilike(term)
            )
        )
    threshold = _normalize_taali_score_for_filter(min_taali_score)
    if threshold is not None:
        base_query = base_query.filter(
            CandidateApplication.taali_score_cache_100.is_not(None),
            CandidateApplication.taali_score_cache_100 >= threshold,
        )
    pre_screen_threshold = _normalize_taali_score_for_filter(min_pre_screen_score)
    if pre_screen_threshold is not None:
        base_query = base_query.filter(
            CandidateApplication.pre_screen_score_100.is_not(None),
            CandidateApplication.pre_screen_score_100 >= pre_screen_threshold,
        )

    stage_counts = _empty_stage_counts()
    if include_stage_counts:
        stage_rows = (
            base_query.with_entities(
                CandidateApplication.pipeline_stage,
                func.count(CandidateApplication.id),
            )
            .group_by(CandidateApplication.pipeline_stage)
            .all()
        )
        stage_counts = _build_stage_counts(stage_rows)

    requested_stages = _parse_choice_csv_filter(
        stages,
        allowed=PIPELINE_STAGE_VALUES,
        field_name="stages",
    )
    single_stage = str(stage or "").strip().lower()
    if single_stage and single_stage != "all":
        if single_stage not in PIPELINE_STAGE_VALUES:
            raise HTTPException(status_code=422, detail=f"Invalid stage value '{single_stage}'")
        if single_stage not in requested_stages:
            requested_stages.append(single_stage)

    filtered_query = base_query
    if requested_stages:
        filtered_query = filtered_query.filter(CandidateApplication.pipeline_stage.in_(requested_stages))

    total = filtered_query.order_by(None).count()
    page_ids = [
        int(row_id)
        for (row_id,) in (
            filtered_query.with_entities(CandidateApplication.id)
            .order_by(*_application_order_columns(sort_by, sort_order))
            .offset(offset)
            .limit(limit)
            .all()
        )
    ]
    rows: list[CandidateApplication] = []
    if page_ids:
        fetched = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.organization),
                joinedload(CandidateApplication.role),
                joinedload(CandidateApplication.interviews),
                joinedload(CandidateApplication.assessments).joinedload(Assessment.task),
            )
            .filter(CandidateApplication.id.in_(page_ids))
            .all()
        )
        by_id = {int(item.id): item for item in fetched}
        rows = [by_id[row_id] for row_id in page_ids if row_id in by_id]

    items = [
        application_list_payload(app, include_cv_text=include_cv_text)
        for app in rows
    ]
    active_candidates_count = (
        int(stage_counts.get("all", 0))
        if include_stage_counts
        else int(base_query.order_by(None).count())
    )
    last_candidate_activity_at = (
        db.query(
            func.max(
                func.coalesce(
                    CandidateApplication.pipeline_stage_updated_at,
                    CandidateApplication.updated_at,
                    CandidateApplication.created_at,
                )
            )
        )
        .filter(
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role.id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
        )
        .scalar()
    )
    duration_ms = (perf_counter() - started_at) * 1000.0
    logger.info(
        (
            "get_role_pipeline org_id=%s role_id=%s stage=%s search=%s total=%s limit=%s offset=%s "
            "source=%s sort_by=%s sort_order=%s include_stage_counts=%s duration_ms=%.1f request_id=%s"
        ),
        current_user.organization_id,
        role.id,
        ",".join(requested_stages) or stage,
        bool(search and search.strip()),
        total,
        limit,
        offset,
        source or "all",
        sort_by,
        sort_order,
        include_stage_counts,
        duration_ms,
        get_request_id(),
    )

    payload = {
        "role_id": role.id,
        "role_name": role.name,
        "stage": ",".join(requested_stages) if requested_stages else "all",
        "active_candidates_count": active_candidates_count,
        "last_candidate_activity_at": last_candidate_activity_at,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
    if include_stage_counts:
        payload["stage_counts"] = stage_counts
    return payload


@router.patch("/applications/{application_id}/stage", response_model=ApplicationResponse)
def update_application_stage(
    application_id: int,
    data: ApplicationStageUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    try:
        ensure_pipeline_fields(app)
        initialize_pipeline_event_if_missing(
            db,
            app=app,
            actor_type="system",
            actor_id=current_user.id,
            reason="Pipeline initialized before stage patch",
        )
        transition_stage(
            db,
            app=app,
            to_stage=data.pipeline_stage,
            source="recruiter",
            actor_type="recruiter",
            actor_id=current_user.id,
            reason=data.reason or "Recruiter stage update",
            idempotency_key=data.idempotency_key,
            expected_version=data.expected_version,
        )
        db.commit()
        db.refresh(app)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update application stage")
    return application_to_response(app)


@router.patch("/applications/{application_id}/outcome", response_model=ApplicationResponse)
def update_application_outcome(
    application_id: int,
    data: ApplicationOutcomeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    try:
        ensure_pipeline_fields(app)
        if data.expected_version is not None and int(data.expected_version) != int(app.version or 0):
            raise HTTPException(
                status_code=409,
                detail=f"Version mismatch: expected={data.expected_version}, current={app.version}",
            )
        existing_idempotent = None
        if str(data.idempotency_key or "").strip():
            existing_idempotent = (
                db.query(CandidateApplicationEvent.id)
                .filter(
                    CandidateApplicationEvent.application_id == app.id,
                    CandidateApplicationEvent.idempotency_key == str(data.idempotency_key).strip(),
                )
                .first()
            )
        if existing_idempotent:
            return application_to_response(app)
        initialize_pipeline_event_if_missing(
            db,
            app=app,
            actor_type="system",
            actor_id=current_user.id,
            reason="Pipeline initialized before outcome patch",
        )
        writeback_result = _sync_workable_outcome_change(
            db=db,
            app=app,
            target_outcome=data.application_outcome,
            current_user=current_user,
            reason=data.reason,
        )
        transition_outcome(
            db,
            app=app,
            to_outcome=data.application_outcome,
            actor_type="recruiter",
            actor_id=current_user.id,
            reason=data.reason or "Recruiter outcome update",
            idempotency_key=data.idempotency_key,
            expected_version=data.expected_version,
        )
        if writeback_result and writeback_result.get("success"):
            append_application_event(
                db,
                app=app,
                event_type="workable_reverted" if data.application_outcome == "open" else "workable_disqualified",
                actor_type="recruiter",
                actor_id=current_user.id,
                reason=data.reason or writeback_result.get("message") or "Workable outcome synced",
                metadata={
                    "action": writeback_result.get("action"),
                    "code": writeback_result.get("code"),
                    "workable_candidate_id": app.workable_candidate_id,
                    "workable_actor_member_id": (writeback_result.get("config") or {}).get("actor_member_id"),
                    "workable_disqualify_reason_id": (writeback_result.get("config") or {}).get("workable_disqualify_reason_id"),
                },
            )
        db.commit()
        db.refresh(app)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update application outcome")
    return application_to_response(app)


@router.get("/applications/{application_id}/events", response_model=list[ApplicationEventResponse])
def get_application_events(
    application_id: int,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    return list_application_events(
        db,
        organization_id=current_user.organization_id,
        application_id=app.id,
        limit=limit,
        offset=offset,
    )


@router.post("/applications/{application_id}/upload-cv", response_model=ApplicationCvUploadResponse)
def upload_application_cv(
    application_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    result = process_document_upload(
        upload=file,
        entity_id=application_id,
        doc_type="cv",
        allowed_extensions={"pdf", "docx"},
    )
    now = datetime.now(timezone.utc)
    app.cv_file_url = result["file_url"]
    app.cv_filename = result["filename"]
    app.cv_text = sanitize_text_for_storage(result["extracted_text"])
    app.cv_uploaded_at = now
    if app.candidate:
        app.candidate.cv_file_url = result["file_url"]
        app.candidate.cv_filename = result["filename"]
        app.candidate.cv_text = sanitize_text_for_storage(result["extracted_text"])
        app.candidate.cv_uploaded_at = now
    # Reset any prior score so the UI shows pending until the job completes.
    app.cv_match_score = None
    app.cv_match_details = None
    app.cv_match_scored_at = None
    _refresh_rank_score(app)
    refresh_application_score_cache(app, db=db)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to upload CV")
    # CV upload is a deliberate human action — implicit "score this".
    # Fan out scoring + interview pack + auto-reject pre-screen as
    # background tasks so the recruiter gets the page back immediately.
    on_application_created(app, score=True, score_force=True)
    return ApplicationCvUploadResponse(
        application_id=app.id,
        filename=result["filename"],
        text_preview=result["text_preview"],
        uploaded_at=now,
    )


@router.post("/applications/{application_id}/generate-taali-cv-ai", response_model=ApplicationDetailResponse)
def generate_taali_cv_ai(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate TAALI CV-vs-job fit score for an application.

    - If the application already has CV text, we just (re)compute the match.
    - If the application is linked to Workable and no CV is present, we attempt to download the resume
      from Workable, extract text, then compute the match.
    """
    app = get_application(application_id, current_user.organization_id, db)
    role = app.role
    if not role or not role_has_job_spec(role):
        raise HTTPException(status_code=400, detail="Upload job spec before generating TAALI score")
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()

    # If the candidate already has a CV stored, reuse it.
    if (not (app.cv_text or "").strip()) and app.candidate and (app.candidate.cv_text or "").strip():
        app.cv_file_url = app.candidate.cv_file_url
        app.cv_filename = app.candidate.cv_filename
        app.cv_text = app.candidate.cv_text
        app.cv_uploaded_at = app.candidate.cv_uploaded_at

    if not (app.cv_text or "").strip():
        if not org or not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
            raise HTTPException(status_code=400, detail="No CV found for this application (and Workable is not connected)")
        candidate_id = str(app.workable_candidate_id or "").strip()
        if not candidate_id:
            raise HTTPException(status_code=400, detail="No CV found for this application (and it is not linked to a Workable candidate)")

        fetched = _try_fetch_cv_from_workable(app, app.candidate, db, org)
        if not fetched:
            raise HTTPException(status_code=404, detail="No resume found on the Workable candidate profile")

    app.cv_match_score = None
    app.cv_match_details = None
    app.cv_match_scored_at = None
    try:
        job = enqueue_score(db, app, force=True)
    except Exception as exc:
        logger.exception("Failed to enqueue CV match scoring for application_id=%s", app.id)
        raise HTTPException(status_code=500, detail="Failed to enqueue CV scoring") from exc
    if job is None:
        raise HTTPException(
            status_code=400,
            detail="CV scoring could not start. Confirm the candidate has CV text, the role has a job spec, and scoring is configured.",
        )
    if str(getattr(job, "status", "")).lower() == "error":
        detail = (getattr(job, "error_message", "") or "").strip()
        raise HTTPException(
            status_code=500,
            detail=f"CV scoring failed: {detail}" if detail else "CV scoring failed",
        )
    _refresh_rank_score(app)
    refresh_application_score_cache(app, db=db)
    refresh_application_interview_support(app)
    if org:
        run_auto_reject_if_needed(
            db=db,
            org=org,
            app=app,
            role=app.role,
            actor_type="recruiter",
            actor_id=current_user.id,
        )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to generate TAALI score")

    app = get_application(app.id, current_user.organization_id, db)
    return ApplicationDetailResponse(**application_detail_payload(app, include_cv_text=True))


@router.post(
    "/applications/{application_id}/refresh-interview-support",
    response_model=ApplicationDetailResponse,
)
def refresh_application_interview_guidance(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-derive deterministic per-application interview guidance.

    Refreshes the screening pack, tech interview pack, screening / tech
    interview summaries, interview evidence summary, AND the candidate
    interview kit (computed from cv_match_v4 data). No Claude call — this
    is pure aggregation of existing scoring + transcript data. Persists the
    updated columns so downstream report builders see the fresh values.
    """
    app = get_application(application_id, current_user.organization_id, db)
    refresh_application_interview_support(
        app, organization=getattr(app, "organization", None)
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to refresh interview guidance")
    app = get_application(app.id, current_user.organization_id, db)
    return ApplicationDetailResponse(**application_detail_payload(app, include_cv_text=True))


# ---------------------------------------------------------------------------
# On-demand enrichment
# ---------------------------------------------------------------------------

@router.post("/applications/{application_id}/enrich")
def enrich_application_candidate(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch full candidate profile from Workable and populate all profile fields."""
    app = get_application(application_id, current_user.organization_id, db)
    candidate = app.candidate
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    candidate_wid = str(candidate.workable_candidate_id or "").strip()
    if not candidate_wid:
        raise HTTPException(status_code=400, detail="Not a Workable candidate")

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org or not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
        raise HTTPException(status_code=400, detail="Workable is not connected")

    workable = WorkableService(access_token=org.workable_access_token, subdomain=org.workable_subdomain)
    try:
        full_payload = workable.get_candidate(candidate_wid)
    except WorkableRateLimitError:
        raise HTTPException(status_code=502, detail="Workable rate limited. Please try again shortly.")

    if not full_payload:
        raise HTTPException(status_code=502, detail="Failed to fetch Workable candidate profile")

    candidate.workable_data = sanitize_json_for_storage(full_payload)
    extracted = _extract_candidate_fields(full_payload)
    for field, value in extracted.items():
        setattr(candidate, field, value)
    candidate.workable_enriched = True

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save enriched profile")

    app = get_application(application_id, current_user.organization_id, db)
    return application_to_response(app)


# ---------------------------------------------------------------------------
# CV-fetch helper (reusable)
# ---------------------------------------------------------------------------

def _try_fetch_cv_from_workable(
    app: CandidateApplication,
    candidate: Candidate,
    db: Session,
    org: Organization,
) -> bool:
    """Attempt to download CV from Workable for the given application. Returns True if successful."""
    candidate_wid = str(app.workable_candidate_id or candidate.workable_candidate_id or "").strip()
    if not candidate_wid:
        return False
    if not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
        return False

    workable = WorkableService(access_token=org.workable_access_token, subdomain=org.workable_subdomain)
    try:
        candidate_payload = workable.get_candidate(candidate_wid)
    except WorkableRateLimitError:
        return False
    if not candidate_payload:
        return False

    downloaded = workable.download_candidate_resume(candidate_payload)
    if not downloaded:
        return False

    filename, content = downloaded
    if not content or len(content) > MAX_FILE_SIZE:
        return False

    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    preview_only_exts = {"pdf", "png", "jpg", "jpeg", "webp"}
    text_exts = {"pdf", "docx", "txt"}
    if ext not in (text_exts | preview_only_exts):
        return False

    now = datetime.now(timezone.utc)
    local_path = save_file_locally(content=content, directory="cv", prefix=f"cv-{app.id or candidate.id}", ext=ext)
    file_url = local_path

    try:
        from ...services.s3_service import generate_s3_key, upload_to_s3
        s3_key = generate_s3_key("cv", app.id, filename)
        s3_url = upload_to_s3(local_path, s3_key)
        if s3_url:
            file_url = s3_url
    except Exception:
        pass

    extracted = sanitize_text_for_storage(extract_text(content, ext)) if ext in text_exts else ""
    if not extracted and ext not in preview_only_exts:
        return False

    app.cv_file_url = file_url
    app.cv_filename = filename
    app.cv_text = extracted
    app.cv_uploaded_at = now
    if candidate:
        candidate.cv_file_url = file_url
        candidate.cv_filename = filename
        candidate.cv_text = extracted
        candidate.cv_uploaded_at = now

    # Best-effort Workable score extraction
    raw_score, normalized_score, score_source = workable.extract_workable_score(candidate_payload=candidate_payload)
    if raw_score is not None or normalized_score is not None:
        app.workable_score_raw = raw_score
        app.workable_score = normalized_score
        app.workable_score_source = score_source

    # Best-effort CV section parsing (Haiku 4.5). Failure here doesn't
    # prevent the CV fetch from succeeding — the candidate page falls
    # back to raw text rendering when cv_sections is null or
    # parse_failed=True.
    if not extracted:
        return True

    try:
        from ...cv_parsing import parse_cv

        parsed = parse_cv(extracted)
        sections_blob = parsed.model_dump(mode="json")
        app.cv_sections = sections_blob
        if candidate:
            candidate.cv_sections = sections_blob
    except Exception:
        logger.exception(
            "CV section parsing failed for application_id=%s — falling back to raw text",
            app.id,
        )

    return True


# ---------------------------------------------------------------------------
# Batch scoring
# ---------------------------------------------------------------------------

# In-memory progress store keyed by role_id
_batch_score_progress: dict[int, dict] = {}
_batch_fetch_cvs_progress: dict[int, dict] = {}
_batch_pre_screen_progress: dict[int, dict] = {}
# Combined cascade progress (fetch → pre-screen → score) per role.
# Replaces the three separate dicts above for the new /process flow; the
# legacy dicts are still used by the deprecated single-action endpoints
# until those are fully removed.
_process_progress: dict[int, dict] = {}
# Org-wide graph sync — keyed by organization_id
_sync_graph_progress: dict[int, dict] = {}


# --------------------------------------------------------------------------- #
# Cooperative cancellation                                                     #
#                                                                              #
# Long-running batch jobs (score + fetch) check a Redis flag between           #
# iterations and bail out gracefully. Redis (vs. in-memory dict) so the        #
# Celery worker sees the same flag the API process set.                        #
#                                                                              #
# Flag key shapes:                                                             #
#   batch_score:cancel:{role_id}                                               #
#   batch_fetch_cvs:cancel:{role_id}                                           #
# 1-hour TTL prevents stuck cancels if the worker dies before clearing.        #
# --------------------------------------------------------------------------- #


_BATCH_SCORE_CANCEL_PREFIX = "batch_score:cancel:"
_BATCH_FETCH_CANCEL_PREFIX = "batch_fetch_cvs:cancel:"
_BATCH_META_PREFIX = "batch_score:meta:"
_BATCH_QUEUE_PREFIX = "batch_score:queued:"
_CANCEL_FLAG_TTL_SECONDS = 3600
_BATCH_META_TTL_SECONDS = 7200  # 2 hours — survives API restart during a batch
_BATCH_QUEUE_TTL_SECONDS = 7200


def _redis_client():
    """Lazy redis client. Returns None on errors so callers can no-op."""
    try:
        import redis  # type: ignore

        from ...platform.config import settings  # late import — avoids cycles

        return redis.Redis.from_url(settings.REDIS_URL)
    except Exception:
        logger.exception("Failed to build redis client for cancel flag")
        return None


def _set_cancel_flag(prefix: str, role_id: int) -> bool:
    """Set the cancel flag for a job. Returns True on success."""
    client = _redis_client()
    if client is None:
        return False
    try:
        client.set(f"{prefix}{role_id}", "1", ex=_CANCEL_FLAG_TTL_SECONDS)
        return True
    except Exception:
        logger.exception("Failed to set cancel flag for role_id=%s", role_id)
        return False


def _is_cancelled(prefix: str, role_id: int) -> bool:
    client = _redis_client()
    if client is None:
        return False
    try:
        return bool(client.get(f"{prefix}{role_id}"))
    except Exception:
        return False


def _clear_cancel_flag(prefix: str, role_id: int) -> None:
    client = _redis_client()
    if client is None:
        return
    try:
        client.delete(f"{prefix}{role_id}")
    except Exception:
        pass


def _write_batch_meta(role_id: int, *, total: int, started_at: datetime, include_scored: bool) -> None:
    """Persist batch start state to Redis so API process restarts don't lose it."""
    import json as _json
    client = _redis_client()
    if client is None:
        return
    try:
        client.set(
            f"{_BATCH_META_PREFIX}{role_id}",
            _json.dumps({
                "total": total,
                "started_at": started_at.isoformat(),
                "include_scored": bool(include_scored),
            }),
            ex=_BATCH_META_TTL_SECONDS,
        )
    except Exception:
        pass


def _read_batch_meta(role_id: int) -> dict | None:
    """Read persisted batch meta from Redis. Returns None if absent or parse fails."""
    import json as _json
    client = _redis_client()
    if client is None:
        return None
    try:
        raw = client.get(f"{_BATCH_META_PREFIX}{role_id}")
        if raw is None:
            return None
        return _json.loads(raw)
    except Exception:
        return None


def _delete_batch_meta(role_id: int) -> None:
    client = _redis_client()
    if client is None:
        return
    try:
        client.delete(f"{_BATCH_META_PREFIX}{role_id}")
    except Exception:
        pass


def _write_batch_queue(role_id: int, *, include_scored: bool, applied_after: str | None) -> None:
    """Persist a queued (waiting) batch request so it survives API restarts."""
    import json as _json
    client = _redis_client()
    if client is None:
        return
    try:
        client.set(
            f"{_BATCH_QUEUE_PREFIX}{role_id}",
            _json.dumps({"include_scored": bool(include_scored), "applied_after": applied_after}),
            ex=_BATCH_QUEUE_TTL_SECONDS,
        )
    except Exception:
        pass


def _read_batch_queue(role_id: int) -> dict | None:
    import json as _json
    client = _redis_client()
    if client is None:
        return None
    try:
        raw = client.get(f"{_BATCH_QUEUE_PREFIX}{role_id}")
        return _json.loads(raw) if raw else None
    except Exception:
        return None


def _clear_batch_queue(role_id: int) -> None:
    client = _redis_client()
    if client is None:
        return
    try:
        client.delete(f"{_BATCH_QUEUE_PREFIX}{role_id}")
    except Exception:
        pass


def is_batch_score_cancelled(role_id: int) -> bool:
    """Public — used by the Celery batch_score_role task to bail out."""
    return _is_cancelled(_BATCH_SCORE_CANCEL_PREFIX, role_id)


def is_batch_fetch_cancelled(role_id: int) -> bool:
    return _is_cancelled(_BATCH_FETCH_CANCEL_PREFIX, role_id)


def _run_batch_score(role_id: int, org_id: int, *, include_scored: bool = False, applied_after: str | None = None) -> None:
    """Background worker: score applications for a role (inline/dev path).

    By default, only unscored applications are processed. When include_scored=True,
    already-scored applications are re-scored as well. applied_after restricts to
    candidates whose Workable application date is on or after that ISO date.
    """
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            return
        role = db.query(Role).filter(Role.id == role_id, Role.organization_id == org_id).first()
        if not role:
            return

        apps_query = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.role),
                joinedload(CandidateApplication.interviews),
                joinedload(CandidateApplication.assessments).joinedload(Assessment.task),
            )
            .filter(
                CandidateApplication.role_id == role_id,
                CandidateApplication.organization_id == org_id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
        if not include_scored:
            apps_query = apps_query.filter(CandidateApplication.cv_match_score.is_(None))
        if applied_after:
            try:
                from ...models.candidate import Candidate as _Candidate
                cutoff = datetime.fromisoformat(applied_after)
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=timezone.utc)
                apps_query = (
                    apps_query
                    .join(_Candidate, CandidateApplication.candidate_id == _Candidate.id)
                    .filter(_Candidate.workable_created_at >= cutoff)
                )
            except (ValueError, Exception) as exc:
                logger.warning("_run_batch_score: invalid applied_after=%s: %s", applied_after, exc)
        apps = apps_query.all()

        total = len(apps)
        progress = _batch_score_progress.get(role_id, {})
        progress.update(
            {
                "total": total,
                "scored": 0,
                "errors": 0,
                "status": "running",
                "include_scored": bool(include_scored),
            }
        )
        _batch_score_progress[role_id] = progress

        job_spec_text = ((role.job_spec_text if role else None) or "").strip()

        for idx, app in enumerate(apps):
            try:
                # Fetch CV from Workable if missing
                if not (app.cv_text or "").strip():
                    if app.candidate and (app.candidate.cv_text or "").strip():
                        app.cv_file_url = app.candidate.cv_file_url
                        app.cv_filename = app.candidate.cv_filename
                        app.cv_text = app.candidate.cv_text
                        app.cv_uploaded_at = app.candidate.cv_uploaded_at
                    elif app.source == "workable":
                        _try_fetch_cv_from_workable(app, app.candidate, db, org)

                cv_text = (app.cv_text or "").strip()
                if not cv_text or not job_spec_text or not settings.ANTHROPIC_API_KEY:
                    progress["scored"] = idx + 1
                    continue

                # Idempotency: if not forcing a rescore, skip applications that
                # are already scored OR were filtered "Below threshold" by a
                # previous pre-screen run (with no CV change since). Cuts the
                # batch loop down to actual work that needs doing.
                if not include_scored:
                    if app.cv_match_score is not None:
                        progress["scored"] = idx + 1
                        continue
                    if (
                        (app.pre_screen_recommendation or "") == "Below threshold"
                        and app.pre_screen_run_at is not None
                        and (app.cv_uploaded_at is None or app.cv_uploaded_at <= app.pre_screen_run_at)
                    ):
                        progress["scored"] = idx + 1
                        continue

                # Route through the orchestrator: cache hits skip Claude, misses
                # call v4 (with criteria) or v3 (without). Inline path keeps the
                # legacy thread-loop behaviour intact when Celery is disabled.
                job = enqueue_score(db, app, force=include_scored)
                if job is not None and job.status == "error":
                    progress["errors"] = progress.get("errors", 0) + 1
                _refresh_rank_score(app)
                refresh_application_score_cache(app, db=db)
                refresh_application_interview_support(app)
                run_auto_reject_if_needed(
                    db=db,
                    org=org,
                    app=app,
                    role=role,
                    actor_type="system",
                )
                db.flush()
            except Exception:
                logger.exception("Batch score failed for application_id=%s", app.id)
                progress["errors"] = progress.get("errors", 0) + 1

            progress["scored"] = idx + 1
            _batch_score_progress[role_id] = progress

            if (idx + 1) % 5 == 0:
                try:
                    db.commit()
                except Exception:
                    db.rollback()

        try:
            db.commit()
        except Exception:
            db.rollback()

        progress["status"] = "completed"
        _batch_score_progress[role_id] = progress
    except Exception:
        logger.exception("Batch scoring failed for role_id=%s", role_id)
        progress = _batch_score_progress.get(role_id, {})
        progress["status"] = "failed"
        _batch_score_progress[role_id] = progress
    finally:
        db.close()


@router.post("/roles/{role_id}/batch-score")
def batch_score_role(
    role_id: int,
    include_scored: bool = Query(
        default=False,
        description="When true, re-score candidates even if they already have a CV match score.",
    ),
    applied_after: str | None = Query(
        default=None,
        description="ISO date (e.g. 2026-01-01). Only process candidates whose Workable application date is on or after this date.",
    ),
    dry_run: bool = Query(
        default=False,
        description="Return counts of what would be processed (fetch-CV / pre-screen / score), without starting a job.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start background batch scoring for a role.

    Default behavior scores only unscored applications. include_scored=true enables
    a full re-score pass for the role. applied_after filters to a specific applicant cohort.
    The cascade does fetch-CV (if missing) → pre-screen (if not run) → score, skipping
    each step that's already complete unless include_scored=true.
    """
    role = get_role(role_id, current_user.organization_id, db)
    if not role_has_job_spec(role):
        raise HTTPException(status_code=400, detail="Upload job spec before batch scoring")

    if dry_run:
        all_apps = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.role_id == role_id,
                CandidateApplication.organization_id == current_user.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
        )

        # The cascade is fetch-CV → pre-screen → score. So a candidate that
        # currently has no CV but has source=workable will be fetched, then
        # pre-screened, then scored. The dry_run counts must account for this
        # — otherwise pre_screen=0 looks wrong when the user is about to fetch
        # 5 CVs that will all need pre-screening.
        def _has_cv(a):
            return bool((a.cv_text or "").strip())

        def _will_have_cv_after_cascade(a):
            return _has_cv(a) or (a.source or "") == "workable"

        will_fetch = sum(1 for a in all_apps if not _has_cv(a) and (a.source or "") == "workable")

        # Pre-screen runs on candidates that will end up with a CV AND don't
        # have an up-to-date pre-screen result yet. For will-be-fetched
        # candidates that's always true (no CV → no pre-screen). For ones
        # that already have a CV, "stale" = CV uploaded after pre-screen ran.
        def _needs_pre_screen(a):
            if not _will_have_cv_after_cascade(a):
                return False
            if a.pre_screen_recommendation is None or a.pre_screen_run_at is None:
                return True
            if a.cv_uploaded_at is not None and a.cv_uploaded_at > a.pre_screen_run_at:
                return True
            return False

        will_pre_screen = sum(1 for a in all_apps if _needs_pre_screen(a))

        if include_scored:
            # Rescore everyone who'll have a CV.
            will_score = sum(1 for a in all_apps if _will_have_cv_after_cascade(a))
        else:
            # Score: must have CV (or be about to fetch), not already scored,
            # and not be Below threshold from a current pre-screen.
            def _needs_score(a):
                if not _will_have_cv_after_cascade(a):
                    return False
                if a.cv_match_score is not None:
                    # Already scored — only "stale CV" forces a rescore here.
                    if a.cv_match_scored_at is not None and a.cv_uploaded_at is not None and a.cv_uploaded_at > a.cv_match_scored_at:
                        return True
                    return False
                if (a.pre_screen_recommendation or "") == "Below threshold":
                    # Will-be-fetched candidates have rec=None so they pass
                    # this check; only candidates currently rejected get
                    # excluded.
                    if a.pre_screen_run_at is not None and (a.cv_uploaded_at is None or a.cv_uploaded_at <= a.pre_screen_run_at):
                        return False
                return True

            will_score = sum(1 for a in all_apps if _needs_score(a))

        return {
            "will_fetch_cv": int(will_fetch),
            "will_pre_screen": int(will_pre_screen),
            "will_score": int(will_score),
            "total": len(all_apps),
            "include_scored": bool(include_scored),
        }

    existing = _batch_score_progress.get(role_id, {})
    if existing.get("status") in {"running", "cancelling"}:
        # Queue the new request instead of rejecting — it auto-starts when the
        # active batch completes or is cancelled.
        _write_batch_queue(role_id, include_scored=include_scored, applied_after=applied_after)
        return {
            "status": "queued",
            "total": existing.get("total", 0),
            "scored": existing.get("scored", 0),
            "include_scored": bool(include_scored),
        }

    target_query = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.role_id == role_id,
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
    )
    if not include_scored:
        target_query = target_query.filter(CandidateApplication.cv_match_score.is_(None))
    target_count = target_query.count()

    if target_count == 0:
        return {
            "status": "nothing_to_score",
            "total": 0,
            "total_target": 0,
            "total_unscored": 0,
            "include_scored": bool(include_scored),
        }

    # Clear any stale cancel flag so Cancel → Re-score works without
    # the new batch being immediately killed by the old flag.
    _clear_cancel_flag(_BATCH_SCORE_CANCEL_PREFIX, role_id)

    batch_started_at = datetime.now(timezone.utc)
    _batch_score_progress[role_id] = {
        "total": target_count,
        "scored": 0,
        "errors": 0,
        "status": "running",
        "include_scored": bool(include_scored),
        "started_at": batch_started_at,
        "organization_id": current_user.organization_id,
        "role_name": str(getattr(role, "name", "") or ""),
    }
    # Mirror to Redis so the status endpoint survives an API process
    # restart mid-batch (in-process dict is wiped on restart).
    _write_batch_meta(role_id, total=target_count, started_at=batch_started_at, include_scored=bool(include_scored))

    if settings.MVP_DISABLE_CELERY:
        # Inline path keeps tests + dev environments working without a broker.
        thread = threading.Thread(
            target=_run_batch_score,
            args=(role_id, current_user.organization_id),
            kwargs={"include_scored": include_scored, "applied_after": applied_after},
            daemon=True,
        )
        thread.start()
    else:
        from ...tasks.scoring_tasks import batch_score_role as _celery_batch_score_role

        _celery_batch_score_role.delay(role_id, include_scored=include_scored, applied_after=applied_after)

    return {
        "status": "started",
        "total": target_count,
        "total_target": target_count,
        "total_unscored": target_count if not include_scored else 0,
        "include_scored": bool(include_scored),
    }


@router.post("/roles/{role_id}/applications/score-selected")
def score_selected_applications(
    role_id: int,
    payload: dict = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Enqueue CV scoring for a specific list of application IDs.

    Body: ``{"application_ids": [1, 2, 3], "force": false}``

    Default behaviour (force=false): skip applications whose inputs haven't
    changed — the cache layer guarantees a no-op for those, but explicitly
    skipping done rows avoids creating churn in the cv_score_jobs log.
    Pass force=true to re-enqueue even when score_status is done; the
    orchestrator still hits the cache, so this is cheap.
    """
    payload = payload or {}
    raw_ids = payload.get("application_ids") or []
    force = bool(payload.get("force"))
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="application_ids is required")
    try:
        application_ids = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="application_ids must be integers")

    get_role(role_id, current_user.organization_id, db)
    apps = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id.in_(application_ids),
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )

    enqueued = 0
    skipped = 0
    not_eligible = 0
    needs_cv_fetch: list[int] = []
    for app in apps:
        # Skip done rows on default runs — cache would no-op anyway, but this
        # keeps the cv_score_jobs log clean and respects "only rescore on
        # change" semantics.
        if (
            not force
            and app.cv_match_score is not None
            and (latest_score_status(db, app.id) == "done")
        ):
            skipped += 1
            continue

        # Workable applications without CV text yet: queue for background
        # fetch+score so the recruiter doesn't have to click Fetch CVs first.
        # The fetch happens off the request thread; the score is enqueued
        # immediately after the CV lands.
        if (
            not (app.cv_text or "").strip()
            and (app.source or "") == "workable"
        ):
            needs_cv_fetch.append(app.id)
            continue

        job = enqueue_score(db, app, force=force)
        if job is None:
            not_eligible += 1
        else:
            enqueued += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to enqueue scoring jobs")

    if needs_cv_fetch:
        threading.Thread(
            target=_run_fetch_then_score,
            args=(needs_cv_fetch, current_user.organization_id),
            kwargs={"force": force},
            daemon=True,
        ).start()

    return {
        "status": "enqueued",
        "requested": len(application_ids),
        "enqueued": enqueued,
        "skipped_unchanged": skipped,
        "not_eligible": not_eligible,
        "auto_fetching": len(needs_cv_fetch),
    }


@router.post("/roles/{role_id}/applications/fetch-cvs-selected")
def fetch_cvs_selected_applications(
    role_id: int,
    payload: dict = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch CVs from Workable for a specific list of application IDs.

    Body: ``{"application_ids": [1, 2, 3]}``

    Mirrors ``/roles/{role_id}/fetch-cvs`` but scoped to a recruiter's
    selection rather than the whole role. Runs in a background thread —
    this endpoint returns immediately. Already-CV'd apps are no-ops.
    """
    payload = payload or {}
    raw_ids = payload.get("application_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="application_ids is required")
    try:
        application_ids = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="application_ids must be integers")

    get_role(role_id, current_user.organization_id, db)
    org = (
        db.query(Organization)
        .filter(Organization.id == current_user.organization_id)
        .first()
    )
    if not org or not org.workable_connected:
        raise HTTPException(status_code=400, detail="Workable is not connected")

    apps = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id.in_(application_ids),
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )
    fetchable = [
        a.id
        for a in apps
        if not (a.cv_text or "").strip() and (a.source or "") == "workable"
    ]
    already_present = sum(1 for a in apps if (a.cv_text or "").strip())

    if fetchable:
        threading.Thread(
            target=_run_fetch_then_score,
            args=(fetchable, current_user.organization_id),
            kwargs={"score_after": False},
            daemon=True,
        ).start()

    return {
        "status": "started" if fetchable else "noop",
        "requested": len(application_ids),
        "fetching": len(fetchable),
        "already_present": already_present,
    }


@router.post("/roles/{role_id}/applications/refresh-interview-support-bulk")
def refresh_interview_support_bulk(
    role_id: int,
    payload: dict = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Refresh deterministic per-application interview guidance for many applications.

    Body: ``{"application_ids": [1, 2, 3]}``

    No Claude calls — pure aggregation of existing scoring + transcript data.
    Each application gets its screening_pack, tech_interview_pack, summaries,
    interview_evidence_summary, and the v4-derived candidate_interview_kit
    re-derived and persisted.
    """
    payload = payload or {}
    raw_ids = payload.get("application_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="application_ids is required")
    try:
        application_ids = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="application_ids must be integers")

    get_role(role_id, current_user.organization_id, db)
    apps = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id.in_(application_ids),
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )

    refreshed = 0
    for app in apps:
        try:
            refresh_application_interview_support(
                app, organization=getattr(app, "organization", None)
            )
            refreshed += 1
        except Exception:
            logger.exception(
                "Failed to refresh interview support for application_id=%s",
                app.id,
            )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to refresh interview support")

    return {
        "status": "refreshed",
        "requested": len(application_ids),
        "refreshed": refreshed,
    }


@router.get("/batch-score/active")
def get_active_batch_scores(
    current_user: User = Depends(get_current_user),
):
    """Return all roles with an active or recently-completed batch for this org.

    Used by the global jobs panel on mount to rediscover in-flight batches
    after navigation or page refresh without relying on local React state.
    """
    active = []
    for role_id, progress in list(_batch_score_progress.items()):
        if progress.get("organization_id") != current_user.organization_id:
            continue
        if progress.get("status") in {"running", "cancelling", "completed", "cancelled"}:
            active.append({
                "role_id": role_id,
                "role_name": progress.get("role_name", ""),
                "status": progress.get("status"),
                "total": progress.get("total", 0),
                "scored": progress.get("scored", 0),
            })
    return {"active": active}


@router.get("/roles/{role_id}/batch-score/status")
def batch_score_status(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll batch scoring progress for a role.

    Reads counts from the DB (cv_score_jobs + candidate_applications) so
    Celery-driven batches surface real progress. The previous version
    only read from an in-process dict that the worker can't update,
    leaving the recruiter stuck looking at "0/N scored" forever.
    """
    get_role(role_id, current_user.organization_id, db)
    progress = _batch_score_progress.get(role_id, {})
    total = int(progress.get("total", 0) or 0)
    started_at = progress.get("started_at")

    # If the in-process dict was wiped by an API restart, recover from Redis.
    if total == 0:
        meta = _read_batch_meta(role_id)
        if meta:
            total = int(meta.get("total", 0) or 0)
            started_at_raw = meta.get("started_at")
            if started_at_raw and started_at is None:
                try:
                    started_at = datetime.fromisoformat(started_at_raw)
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

    scored = 0
    errors = 0
    pre_screened_out = 0
    if total > 0 and started_at is not None:
        # Count terminal-state jobs for this role since the batch began.
        # ``cv_score_jobs`` is the source of truth — pre-screen-filtered
        # candidates have ``cache_hit="pre_screen_filtered"`` (those are
        # NOT counted as fully scored, only as filtered). Successful v9
        # runs land with status=done and a non-filtered cache_hit. Errors
        # show up as status=error.
        pre_screened_out = (
            db.query(CvScoreJob)
            .filter(
                CvScoreJob.role_id == role_id,
                CvScoreJob.cache_hit == "pre_screen_filtered",
                CvScoreJob.finished_at >= started_at,
            )
            .count()
        )
        scored = (
            db.query(CvScoreJob)
            .filter(
                CvScoreJob.role_id == role_id,
                CvScoreJob.status == SCORE_JOB_DONE,
                CvScoreJob.cache_hit != "pre_screen_filtered",
                CvScoreJob.finished_at >= started_at,
            )
            .count()
        )
        errors = (
            db.query(CvScoreJob)
            .filter(
                CvScoreJob.role_id == role_id,
                CvScoreJob.status == SCORE_JOB_ERROR,
                CvScoreJob.finished_at >= started_at,
            )
            .count()
        )

    # Mark completed when every targeted application has a terminal state.
    status = progress.get("status", "idle")
    if status == "idle" and total > 0 and started_at is not None:
        # Recovered from Redis after API restart — derive status from DB counts.
        active_jobs = (
            db.query(CvScoreJob)
            .filter(
                CvScoreJob.role_id == role_id,
                CvScoreJob.status.in_([SCORE_JOB_PENDING, SCORE_JOB_RUNNING]),
                CvScoreJob.queued_at >= started_at,
            )
            .count()
        )
        status = "running" if active_jobs > 0 else "completed"
    if total > 0 and (scored + errors + pre_screened_out) >= total:
        if status == "running":
            status = "completed"
            progress["status"] = status
            _batch_score_progress[role_id] = progress
            _delete_batch_meta(role_id)
        elif status == "cancelling":
            # All jobs are terminal — transition from cancelling to cancelled.
            # Errors already include the DB-marked-cancelled jobs, so this
            # fires once every pending job has been skipped or has completed.
            status = "cancelled"
            progress["status"] = status
            _batch_score_progress[role_id] = progress

    # If the active batch just completed/cancelled, auto-start the queued one.
    queued_params = _read_batch_queue(role_id)
    queued_next: dict | None = None
    if queued_params is not None:
        if status in {"completed", "cancelled"}:
            _clear_batch_queue(role_id)
            # Kick off the queued batch inline (same logic as the POST endpoint).
            _clear_cancel_flag(_BATCH_SCORE_CANCEL_PREFIX, role_id)
            q_include = bool(queued_params.get("include_scored"))
            q_after = queued_params.get("applied_after")
            q_query = (
                db.query(CandidateApplication)
                .filter(
                    CandidateApplication.role_id == role_id,
                    CandidateApplication.organization_id == current_user.organization_id,
                    CandidateApplication.deleted_at.is_(None),
                )
            )
            if not q_include:
                q_query = q_query.filter(CandidateApplication.cv_match_score.is_(None))
            q_count = q_query.count()
            if q_count > 0:
                q_started_at = datetime.now(timezone.utc)
                _batch_score_progress[role_id] = {
                    "total": q_count,
                    "scored": 0,
                    "errors": 0,
                    "status": "running",
                    "include_scored": q_include,
                    "started_at": q_started_at,
                    "organization_id": current_user.organization_id,
                }
                _write_batch_meta(role_id, total=q_count, started_at=q_started_at, include_scored=q_include)
                if settings.MVP_DISABLE_CELERY:
                    import threading as _threading
                    _threading.Thread(
                        target=_run_batch_score,
                        args=(role_id, current_user.organization_id),
                        kwargs={"include_scored": q_include, "applied_after": q_after},
                        daemon=True,
                    ).start()
                else:
                    from ...tasks.scoring_tasks import batch_score_role as _celery_batch_score_role
                    _celery_batch_score_role.delay(role_id, include_scored=q_include, applied_after=q_after)
                status = "running"
                queued_next = None  # now running, no longer queued
        else:
            queued_next = {"include_scored": queued_params.get("include_scored")}

    # Look up role name once (cheap; the in-progress dict may not have it
    # if the batch was started before this code path stored it).
    role_obj = db.query(Role).filter(
        Role.id == role_id, Role.organization_id == current_user.organization_id
    ).first()
    role_name = (role_obj.name if role_obj else None) or progress.get("role_name", "")

    return {
        "status": status,
        "total": total,
        "scored": scored,
        "errors": errors,
        "pre_screened_out": pre_screened_out,
        "include_scored": bool(progress.get("include_scored")),
        "pre_screen_enabled": bool(settings.ENABLE_PRE_SCREEN_GATE),
        "role_name": role_name,
        "queued": queued_next,
    }


@router.post("/roles/{role_id}/batch-score/cancel")
def cancel_batch_score(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a running or queued batch-score job.

    Two-layer cancellation:
    1. Redis flag — the batch_score_role task loop and individual
       score_application_job tasks check this and bail out.
    2. DB marking — all PENDING cv_score_jobs for this role are
       immediately set to error/cancelled_by_recruiter so any tasks
       already sitting in the Celery queue skip the Claude call when
       they're dequeued (worker checks job.status before calling the API).

    Also clears any queued (not-yet-started) batch so it doesn't
    auto-start after this cancel.
    """
    get_role(role_id, current_user.organization_id, db)

    # Layer 1: Redis cancel flag (stops the batch loop + future dequeues).
    set_ok = _set_cancel_flag(_BATCH_SCORE_CANCEL_PREFIX, role_id)

    # Layer 2: Immediately mark all pending score jobs as cancelled in the DB.
    # Workers check job.status before calling Claude — if it's not pending/stale
    # they skip processing. This is the definitive kill switch for tasks already
    # sitting in the Celery queue.
    now = datetime.now(timezone.utc)
    try:
        cancelled_count = (
            db.query(CvScoreJob)
            .filter(
                CvScoreJob.role_id == role_id,
                CvScoreJob.status == SCORE_JOB_PENDING,
            )
            .update(
                {
                    "status": SCORE_JOB_ERROR,
                    "error_message": "cancelled_by_recruiter",
                    "finished_at": now,
                },
                synchronize_session=False,
            )
        )
        db.commit()
    except Exception:
        logger.exception("Failed to mark pending jobs as cancelled for role_id=%s", role_id)
        db.rollback()
        cancelled_count = 0

    # Clear any queued (not-yet-started) batch so it doesn't auto-start.
    _clear_batch_queue(role_id)

    progress = _batch_score_progress.get(role_id, {})
    if progress.get("status") in {"running", "cancelling"}:
        progress["status"] = "cancelling"
        _batch_score_progress[role_id] = progress

    return {
        "ok": bool(set_ok),
        "role_id": role_id,
        "status": progress.get("status", "idle"),
        "pending_jobs_cancelled": cancelled_count,
    }


# ---------------------------------------------------------------------------
# Cross-role backfill (fan out batch-score across every role in an org)
# ---------------------------------------------------------------------------

_BACKFILL_META_KEY = "backfill:meta:{org_id}"
_BACKFILL_META_TTL = 86400  # 24 hours


@router.post("/batch-score-all")
def batch_score_all_roles(
    applied_after: str | None = Query(
        default=None,
        description="ISO date (e.g. 2026-01-01). Only score candidates whose Workable application date is on or after this date.",
    ),
    include_scored: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fan out batch-score across every active role in the org.

    Each role gets its own ``batch_score_role`` Celery task so they run in
    parallel (bounded by worker concurrency). Per-role progress is tracked
    via the existing ``/roles/{role_id}/batch-score/status`` endpoint.
    The response includes the full role list and per-role target counts so
    the caller can track progress across all roles.
    """
    from ...models.role import Role
    from ...models.candidate import Candidate

    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .all()
    )

    # Count scoreable apps per role (same filter as the task will use)
    dispatched = []
    skipped = []
    for role in roles:
        if not role_has_job_spec(role):
            skipped.append({"role_id": role.id, "reason": "no_job_spec"})
            continue

        count_q = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.role_id == role.id,
                CandidateApplication.organization_id == current_user.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
        if not include_scored:
            count_q = count_q.filter(CandidateApplication.cv_match_score.is_(None))
        if applied_after:
            try:
                from datetime import timezone as _tz
                cutoff = datetime.fromisoformat(applied_after)
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=_tz.utc)
                count_q = (
                    count_q
                    .join(Candidate, CandidateApplication.candidate_id == Candidate.id)
                    .filter(Candidate.workable_created_at >= cutoff)
                )
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid applied_after date: {applied_after}")

        target_count = count_q.count()
        if target_count == 0:
            skipped.append({"role_id": role.id, "reason": "nothing_to_score"})
            continue

        # Register in per-role progress dict so status endpoint returns data
        _clear_cancel_flag(_BATCH_SCORE_CANCEL_PREFIX, role.id)
        batch_started_at = datetime.now(timezone.utc)
        _batch_score_progress[role.id] = {
            "total": target_count,
            "scored": 0,
            "errors": 0,
            "status": "running",
            "include_scored": bool(include_scored),
            "started_at": batch_started_at,
        }
        _write_batch_meta(
            role.id,
            total=target_count,
            started_at=batch_started_at,
            include_scored=bool(include_scored),
        )

        if settings.MVP_DISABLE_CELERY:
            thread = threading.Thread(
                target=_run_batch_score,
                args=(role.id, current_user.organization_id),
                kwargs={"include_scored": include_scored, "applied_after": applied_after},
                daemon=True,
            )
            thread.start()
        else:
            from ...tasks.scoring_tasks import batch_score_role as _celery_batch_score_role
            _celery_batch_score_role.delay(
                role.id, include_scored=include_scored, applied_after=applied_after
            )

        dispatched.append({"role_id": role.id, "target": target_count})

    total_target = sum(d["target"] for d in dispatched)
    logger.info(
        "batch_score_all_roles: org=%s applied_after=%s dispatched=%d roles total_target=%d",
        current_user.organization_id, applied_after, len(dispatched), total_target,
    )

    # Persist backfill summary to Redis for status queries
    import json as _json
    client = _redis_client()
    if client:
        try:
            client.set(
                _BACKFILL_META_KEY.format(org_id=current_user.organization_id),
                _json.dumps({
                    "applied_after": applied_after,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "roles": dispatched,
                    "total_target": total_target,
                }),
                ex=_BACKFILL_META_TTL,
            )
        except Exception:
            pass

    return {
        "status": "dispatched",
        "roles_dispatched": len(dispatched),
        "roles_skipped": len(skipped),
        "total_target": total_target,
        "applied_after": applied_after,
        "dispatched": dispatched,
        "skipped": skipped,
    }


@router.get("/batch-score-all/status")
def batch_score_all_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregate status across all roles from the last batch-score-all run."""
    import json as _json
    client = _redis_client()
    meta = None
    if client:
        try:
            raw = client.get(_BACKFILL_META_KEY.format(org_id=current_user.organization_id))
            if raw:
                meta = _json.loads(raw)
        except Exception:
            pass

    if not meta:
        return {"status": "no_backfill", "roles": []}

    role_statuses = []
    total_scored = 0
    total_pre_screened_out = 0
    total_errors = 0
    total_target = int(meta.get("total_target", 0))
    all_complete = True

    for entry in meta.get("roles", []):
        role_id = entry["role_id"]
        progress = _batch_score_progress.get(role_id, {})
        status = progress.get("status", "idle")
        started_at = progress.get("started_at")

        if started_at is None:
            redis_meta = _read_batch_meta(role_id)
            if redis_meta and redis_meta.get("started_at"):
                try:
                    started_at = datetime.fromisoformat(redis_meta["started_at"])
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

        scored = errors = pre_screened_out = 0
        if started_at is not None:
            pre_screened_out = (
                db.query(CvScoreJob)
                .filter(
                    CvScoreJob.role_id == role_id,
                    CvScoreJob.cache_hit == "pre_screen_filtered",
                    CvScoreJob.finished_at >= started_at,
                )
                .count()
            )
            scored = (
                db.query(CvScoreJob)
                .filter(
                    CvScoreJob.role_id == role_id,
                    CvScoreJob.status == SCORE_JOB_DONE,
                    CvScoreJob.cache_hit != "pre_screen_filtered",
                    CvScoreJob.finished_at >= started_at,
                )
                .count()
            )
            errors = (
                db.query(CvScoreJob)
                .filter(
                    CvScoreJob.role_id == role_id,
                    CvScoreJob.status == SCORE_JOB_ERROR,
                    CvScoreJob.finished_at >= started_at,
                )
                .count()
            )

        total_scored += scored
        total_pre_screened_out += pre_screened_out
        total_errors += errors
        if status not in ("completed", "cancelled", "failed"):
            all_complete = False

        role_statuses.append({
            "role_id": role_id,
            "target": entry["target"],
            "scored": scored,
            "pre_screened_out": pre_screened_out,
            "errors": errors,
            "status": status,
        })

    processed = total_scored + total_errors + total_pre_screened_out
    return {
        "status": "completed" if all_complete else "running",
        "applied_after": meta.get("applied_after"),
        "started_at": meta.get("started_at"),
        "total_target": total_target,
        "total_scored": total_scored,
        "total_pre_screened_out": total_pre_screened_out,
        "total_errors": total_errors,
        "processed": processed,
        "roles": role_statuses,
    }


# ---------------------------------------------------------------------------
# Batch CV fetch (from Workable, no scoring)
# ---------------------------------------------------------------------------


def _run_fetch_then_score(
    application_ids: list[int],
    org_id: int,
    *,
    score_after: bool = True,
    force: bool = False,
) -> None:
    """Background worker: fetch CVs for a specific application list, then
    optionally enqueue scoring for each.

    Used by:
      - ``/roles/{role_id}/applications/score-selected`` when some selected
        applications are missing CV text — the endpoint returns immediately
        with ``auto_fetching: N`` and this thread fetches + scores in the
        background.
      - ``/roles/{role_id}/applications/fetch-cvs-selected`` for the
        standalone "Fetch CVs" bulk action (``score_after=False``).
    """
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            return
        apps = (
            db.query(CandidateApplication)
            .options(joinedload(CandidateApplication.candidate))
            .filter(
                CandidateApplication.id.in_(application_ids),
                CandidateApplication.organization_id == org_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
        )
        for app in apps:
            try:
                if not (app.cv_text or "").strip():
                    if app.candidate and (app.candidate.cv_text or "").strip():
                        # Candidate has a CV at the candidate level — promote it
                        # to the application row.
                        app.cv_file_url = app.candidate.cv_file_url
                        app.cv_filename = app.candidate.cv_filename
                        app.cv_text = app.candidate.cv_text
                        app.cv_uploaded_at = app.candidate.cv_uploaded_at
                    elif (app.source or "") == "workable":
                        _try_fetch_cv_from_workable(app, app.candidate, db, org)
                if score_after and (app.cv_text or "").strip():
                    enqueue_score(db, app, force=force)
            except Exception:
                logger.exception(
                    "Background fetch+score failed for application_id=%s", app.id
                )
        try:
            db.commit()
        except Exception:
            db.rollback()
    except Exception:
        logger.exception("_run_fetch_then_score failed for org_id=%s", org_id)
    finally:
        db.close()


def _run_batch_fetch_cvs(role_id: int, org_id: int) -> None:
    """Background worker: fetch CVs from Workable for applications missing cv_text."""
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            return
        role = db.query(Role).filter(Role.id == role_id, Role.organization_id == org_id).first()
        if not role:
            return

        apps = (
            db.query(CandidateApplication)
            .options(joinedload(CandidateApplication.candidate))
            .filter(
                CandidateApplication.role_id == role_id,
                CandidateApplication.organization_id == org_id,
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.source == "workable",
            )
            .all()
        )

        apps_to_fetch = [a for a in apps if not (a.cv_text or "").strip()]
        total = len(apps_to_fetch)
        progress = _batch_fetch_cvs_progress.get(role_id, {})
        progress.update({"total": total, "fetched": 0, "errors": 0, "status": "running"})
        _batch_fetch_cvs_progress[role_id] = progress

        for idx, app in enumerate(apps_to_fetch):
            # Cooperative cancel: bail out cleanly between candidates so a
            # recruiter who clicks Cancel doesn't have to wait for all
            # remaining Workable fetches to finish.
            if is_batch_fetch_cancelled(role_id):
                progress["status"] = "cancelled"
                _batch_fetch_cvs_progress[role_id] = progress
                _clear_cancel_flag(_BATCH_FETCH_CANCEL_PREFIX, role_id)
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                logger.info(
                    "_run_batch_fetch_cvs cancelled at %d/%d for role_id=%s",
                    idx, total, role_id,
                )
                return

            try:
                if not (app.cv_text or "").strip():
                    if app.candidate and (app.candidate.cv_text or "").strip():
                        app.cv_file_url = app.candidate.cv_file_url
                        app.cv_filename = app.candidate.cv_filename
                        app.cv_text = app.candidate.cv_text
                        app.cv_uploaded_at = app.candidate.cv_uploaded_at
                    elif app.source == "workable":
                        _try_fetch_cv_from_workable(app, app.candidate, db, org)
                progress["fetched"] = idx + 1
                _batch_fetch_cvs_progress[role_id] = progress
                if (idx + 1) % 3 == 0:
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
            except Exception:
                logger.exception("Batch fetch CV failed for application_id=%s", app.id)
                progress["errors"] = progress.get("errors", 0) + 1
                progress["fetched"] = idx + 1
                _batch_fetch_cvs_progress[role_id] = progress

        try:
            db.commit()
        except Exception:
            db.rollback()
        progress["status"] = "completed"
        _batch_fetch_cvs_progress[role_id] = progress
    except Exception:
        logger.exception("Batch fetch CVs failed for role_id=%s", role_id)
        progress = _batch_fetch_cvs_progress.get(role_id, {})
        progress["status"] = "failed"
        _batch_fetch_cvs_progress[role_id] = progress
    finally:
        db.close()


@router.post("/roles/{role_id}/fetch-cvs")
def batch_fetch_cvs_role(
    role_id: int,
    dry_run: bool = Query(default=False, description="Return the count of applications that need a CV fetched, without starting a job."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch CVs from Workable for all applications in this role that don't have CV text yet."""
    role = get_role(role_id, current_user.organization_id, db)
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org or not org.workable_connected:
        raise HTTPException(status_code=400, detail="Workable is not connected")

    if dry_run:
        to_fetch_count = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.role_id == role_id,
                CandidateApplication.organization_id == current_user.organization_id,
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.source == "workable",
            )
            .filter(
                (CandidateApplication.cv_text.is_(None)) | (CandidateApplication.cv_text == "")
            )
            .count()
        )
        return {"will_fetch": int(to_fetch_count)}

    existing = _batch_fetch_cvs_progress.get(role_id, {})
    if existing.get("status") == "running":
        return {"status": "already_running", "total": existing.get("total", 0), "fetched": existing.get("fetched", 0)}

    to_fetch = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.role_id == role_id,
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.source == "workable",
        )
        .all()
    )
    to_fetch_count = sum(1 for a in to_fetch if not (a.cv_text or "").strip())
    thread = threading.Thread(
        target=_run_batch_fetch_cvs,
        args=(role_id, current_user.organization_id),
        daemon=True,
    )
    thread.start()
    return {"status": "started", "total": to_fetch_count}


@router.get("/roles/{role_id}/fetch-cvs/status")
def batch_fetch_cvs_status(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll batch CV fetch progress for a role."""
    role = get_role(role_id, current_user.organization_id, db)
    progress = _batch_fetch_cvs_progress.get(role_id, {})
    return {
        "status": progress.get("status", "idle"),
        "role_name": role.name,
        "total": progress.get("total", 0),
        "fetched": progress.get("fetched", 0),
        "errors": progress.get("errors", 0),
    }


@router.post("/roles/{role_id}/fetch-cvs/cancel")
def cancel_batch_fetch_cvs(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cooperatively cancel a running CV-fetch batch.

    Same pattern as /batch-score/cancel: sets a Redis flag the loop
    checks between candidates. The current Workable HTTP request
    finishes; subsequent ones are skipped.
    """
    get_role(role_id, current_user.organization_id, db)
    set_ok = _set_cancel_flag(_BATCH_FETCH_CANCEL_PREFIX, role_id)
    progress = _batch_fetch_cvs_progress.get(role_id, {})
    if progress.get("status") == "running":
        progress["status"] = "cancelling"
        _batch_fetch_cvs_progress[role_id] = progress
    return {
        "ok": bool(set_ok),
        "role_id": role_id,
        "status": progress.get("status", "idle"),
    }


# ---------------------------------------------------------------------------
# Batch pre-screen — runs the cheap pre-screen LLM only (no full v3 score).
# Same threading + in-memory progress pattern as batch-score.
# ---------------------------------------------------------------------------


def _select_pre_screen_targets(
    db: Session, *, role_id: int, organization_id: int, refresh: bool
):
    """Return (apps_query, count_only) for pre-screen.

    refresh=False  → only apps that need pre-screen (no run yet OR CV is newer
                     than last pre-screen) AND have a CV.
    refresh=True   → all apps with a CV in this role.
    """
    from ...services.pre_screening_service import application_needs_pre_screen

    base = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.role_id == role_id,
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.cv_text.isnot(None),
            CandidateApplication.cv_text != "",
        )
    )
    if refresh:
        return base.all()
    # Pre-filter cheaply at SQL: never_run OR stale-cv
    candidates = base.all()
    return [a for a in candidates if application_needs_pre_screen(a)]


def _run_batch_pre_screen(role_id: int, org_id: int, *, refresh: bool = False) -> None:
    """Background worker: run pre-screen LLM only on selected apps."""
    from ...services.pre_screening_service import execute_pre_screen_only

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        role = db.query(Role).filter(Role.id == role_id, Role.organization_id == org_id).first()
        if not org or not role:
            return
        apps = _select_pre_screen_targets(
            db, role_id=role_id, organization_id=org_id, refresh=refresh
        )
        total = len(apps)
        progress = _batch_pre_screen_progress.get(role_id, {})
        progress.update({"total": total, "processed": 0, "errors": 0, "status": "running", "refresh": bool(refresh)})
        _batch_pre_screen_progress[role_id] = progress

        for idx, app in enumerate(apps):
            try:
                result = execute_pre_screen_only(app)
                if result.get("status") == "error":
                    progress["errors"] = progress.get("errors", 0) + 1
                # Auto-reject hook for "Below threshold" — same as the regular
                # score path, so manual pre-screen runs trigger Workable
                # disqualify when configured.
                run_auto_reject_if_needed(
                    db=db, org=org, app=app, role=role, actor_type="system"
                )
                db.flush()
            except Exception:
                logger.exception("Batch pre-screen failed for application_id=%s", app.id)
                progress["errors"] = progress.get("errors", 0) + 1
            progress["processed"] = idx + 1
            _batch_pre_screen_progress[role_id] = progress
            if (idx + 1) % 5 == 0:
                try:
                    db.commit()
                except Exception:
                    db.rollback()

        try:
            db.commit()
        except Exception:
            db.rollback()
        progress["status"] = "completed"
        _batch_pre_screen_progress[role_id] = progress
    except Exception:
        logger.exception("Batch pre-screen failed for role_id=%s", role_id)
        progress = _batch_pre_screen_progress.get(role_id, {})
        progress["status"] = "failed"
        _batch_pre_screen_progress[role_id] = progress
    finally:
        db.close()


@router.post("/roles/{role_id}/batch-pre-screen")
def batch_pre_screen_role(
    role_id: int,
    refresh: bool = Query(default=False, description="Re-run pre-screen for all applications, not just unscreened/stale."),
    dry_run: bool = Query(default=False, description="Return the count of applications that would be processed, without starting a job."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start background pre-screen for a role.

    Default: only applications that have a CV and either have never been
    pre-screened or whose CV was uploaded after the last pre-screen.
    refresh=true: all applications with a CV in the role.
    dry_run=true: returns ``{will_process, total_with_cv, total_without_cv}``
    so the UI can populate a confirmation dialog.
    """
    role = get_role(role_id, current_user.organization_id, db)
    if not role_has_job_spec(role):
        raise HTTPException(status_code=400, detail="Upload job spec before batch pre-screen")

    if dry_run:
        targets = _select_pre_screen_targets(
            db, role_id=role_id, organization_id=current_user.organization_id, refresh=refresh
        )
        without_cv = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.role_id == role_id,
                CandidateApplication.organization_id == current_user.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .filter(
                (CandidateApplication.cv_text.is_(None)) | (CandidateApplication.cv_text == "")
            )
            .count()
        )
        return {
            "will_process": len(targets),
            "total_without_cv": int(without_cv),
            "refresh": bool(refresh),
        }

    existing = _batch_pre_screen_progress.get(role_id, {})
    if existing.get("status") == "running":
        return {
            "status": "already_running",
            "total": existing.get("total", 0),
            "processed": existing.get("processed", 0),
        }

    # Count for the response; the worker recomputes its own list to avoid
    # races between this count and what it processes.
    target_count = len(
        _select_pre_screen_targets(
            db, role_id=role_id, organization_id=current_user.organization_id, refresh=refresh
        )
    )
    if target_count == 0:
        return {"status": "nothing_to_pre_screen", "total": 0, "refresh": bool(refresh)}

    _batch_pre_screen_progress[role_id] = {
        "total": target_count,
        "processed": 0,
        "errors": 0,
        "status": "running",
        "refresh": bool(refresh),
        "started_at": datetime.now(timezone.utc),
    }
    thread = threading.Thread(
        target=_run_batch_pre_screen,
        args=(role_id, current_user.organization_id),
        kwargs={"refresh": refresh},
        daemon=True,
    )
    thread.start()
    return {"status": "started", "total": target_count, "refresh": bool(refresh)}


@router.get("/roles/{role_id}/batch-pre-screen/status")
def batch_pre_screen_status(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = get_role(role_id, current_user.organization_id, db)
    progress = _batch_pre_screen_progress.get(role_id, {})
    return {
        "status": progress.get("status", "idle"),
        "role_name": role.name,
        "total": progress.get("total", 0),
        "processed": progress.get("processed", 0),
        "errors": progress.get("errors", 0),
        "refresh": bool(progress.get("refresh", False)),
    }


# ---------------------------------------------------------------------------
# Org-wide graph sync — projects Postgres candidates to Neo4j via Graphiti.
# Filter: has CV, and either never synced OR CV is newer than last sync.
# Uses a per-org threading worker, status by organization_id.
# ---------------------------------------------------------------------------


def _select_graph_sync_candidates(
    db: Session, *, organization_id: int, refresh: bool
) -> list[int]:
    """Return ordered list of candidate ids to (re)sync to the graph."""
    from ...models.candidate import Candidate
    from ...models.graph_sync_state import GraphSyncState

    q = (
        db.query(Candidate.id, Candidate.cv_uploaded_at, GraphSyncState.last_synced_at)
        .outerjoin(GraphSyncState, GraphSyncState.candidate_id == Candidate.id)
        .filter(
            Candidate.organization_id == organization_id,
            Candidate.deleted_at.is_(None),
            Candidate.cv_text.isnot(None),
            Candidate.cv_text != "",
        )
    )
    out: list[int] = []
    for cid, cv_uploaded_at, last_synced_at in q.all():
        if refresh:
            out.append(int(cid))
            continue
        if last_synced_at is None:
            out.append(int(cid))
            continue
        # Stale: CV uploaded after last sync.
        if cv_uploaded_at is not None and cv_uploaded_at > last_synced_at:
            out.append(int(cid))
    return out


def _run_sync_graph(org_id: int, *, refresh: bool = False) -> None:
    """Background worker: project candidates → Neo4j and update graph_sync_state."""
    from ...candidate_graph import sync as graph_sync
    from ...candidate_graph import client as graph_client

    db = SessionLocal()
    try:
        if not graph_client.is_configured():
            progress = _sync_graph_progress.get(org_id, {})
            progress["status"] = "failed"
            progress["error"] = "neo4j_not_configured"
            _sync_graph_progress[org_id] = progress
            return

        candidate_ids = _select_graph_sync_candidates(
            db, organization_id=org_id, refresh=refresh
        )
        total = len(candidate_ids)
        progress = _sync_graph_progress.get(org_id, {})
        progress.update({"total": total, "synced": 0, "errors": 0, "status": "running", "refresh": bool(refresh)})
        _sync_graph_progress[org_id] = progress

        # Delegate per-candidate work to the existing sync module so logic
        # stays in one place. ``sync_candidate`` is idempotent (Graphiti
        # dedupes by content fingerprint) and updates graph_sync_state on
        # success.
        from ...models.candidate import Candidate

        for idx, cid in enumerate(candidate_ids):
            try:
                cand = db.query(Candidate).filter(Candidate.id == cid).first()
                if cand is None:
                    progress["errors"] = progress.get("errors", 0) + 1
                else:
                    sent = graph_sync.sync_candidate(cand, db=db, include_cv_text=True)
                    if sent == 0:
                        # Treat as error if Graphiti dropped everything (likely
                        # due to LLM extraction failure / API credit issues).
                        progress["errors"] = progress.get("errors", 0) + 1
            except Exception:
                logger.exception("Graph sync failed for candidate_id=%s", cid)
                progress["errors"] = progress.get("errors", 0) + 1
            progress["synced"] = idx + 1
            _sync_graph_progress[org_id] = progress
            if (idx + 1) % 5 == 0:
                try:
                    db.commit()
                except Exception:
                    db.rollback()
        try:
            db.commit()
        except Exception:
            db.rollback()
        progress["status"] = "completed"
        _sync_graph_progress[org_id] = progress
    except Exception:
        logger.exception("Sync graph failed for org_id=%s", org_id)
        progress = _sync_graph_progress.get(org_id, {})
        progress["status"] = "failed"
        _sync_graph_progress[org_id] = progress
    finally:
        db.close()


@router.post("/candidates/sync-graph")
def sync_graph_org(
    refresh: bool = Query(default=False, description="Re-sync all candidates with a CV (not just new/stale)."),
    dry_run: bool = Query(default=False, description="Return counts without starting a job."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Org-wide: sync candidates with a CV to the knowledge graph (Neo4j via Graphiti).

    Default: candidates never synced OR with a CV uploaded after the last sync.
    refresh=true: all candidates with a CV in the org.
    """
    from ...candidate_graph import client as graph_client

    org_id = current_user.organization_id
    if dry_run:
        from ...models.candidate import Candidate
        from ...models.graph_sync_state import GraphSyncState

        new_or_stale = _select_graph_sync_candidates(
            db, organization_id=org_id, refresh=False
        )
        total_with_cv = (
            db.query(Candidate)
            .filter(
                Candidate.organization_id == org_id,
                Candidate.deleted_at.is_(None),
                Candidate.cv_text.isnot(None),
                Candidate.cv_text != "",
            )
            .count()
        )
        synced_count = (
            db.query(GraphSyncState)
            .join(Candidate, Candidate.id == GraphSyncState.candidate_id)
            .filter(Candidate.organization_id == org_id)
            .count()
        )
        return {
            "will_process": len(new_or_stale) if not refresh else int(total_with_cv),
            "total_with_cv": int(total_with_cv),
            "already_synced": int(synced_count),
            "stale_or_new": len(new_or_stale),
            "refresh": bool(refresh),
            "neo4j_configured": graph_client.is_configured(),
        }

    if not graph_client.is_configured():
        raise HTTPException(status_code=400, detail="Neo4j is not configured for this deployment.")

    existing = _sync_graph_progress.get(org_id, {})
    if existing.get("status") == "running":
        return {
            "status": "already_running",
            "total": existing.get("total", 0),
            "synced": existing.get("synced", 0),
        }

    candidate_ids = _select_graph_sync_candidates(
        db, organization_id=org_id, refresh=refresh
    )
    if not candidate_ids:
        return {"status": "nothing_to_sync", "total": 0, "refresh": bool(refresh)}

    _sync_graph_progress[org_id] = {
        "total": len(candidate_ids),
        "synced": 0,
        "errors": 0,
        "status": "running",
        "refresh": bool(refresh),
        "started_at": datetime.now(timezone.utc),
    }
    thread = threading.Thread(
        target=_run_sync_graph,
        args=(org_id,),
        kwargs={"refresh": refresh},
        daemon=True,
    )
    thread.start()
    return {"status": "started", "total": len(candidate_ids), "refresh": bool(refresh)}


@router.get("/candidates/sync-graph/status")
def sync_graph_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll the org-wide graph sync progress."""
    progress = _sync_graph_progress.get(current_user.organization_id, {})
    return {
        "status": progress.get("status", "idle"),
        "total": progress.get("total", 0),
        "synced": progress.get("synced", 0),
        "errors": progress.get("errors", 0),
        "refresh": bool(progress.get("refresh", False)),
        "error": progress.get("error"),
    }


# ---------------------------------------------------------------------------
# Unified "Process candidates" endpoint.
#
# One endpoint that runs the cascade: fetch CVs (if missing) → pre-screen
# (if needed) → score (new or all). Each step is independently toggleable
# via the request body. Replaces the 5-button toolbar with a single button
# + multi-select dialog on the frontend.
#
# Why a new endpoint instead of orchestrating the three legacy ones:
# - Combined progress (one toaster row instead of three)
# - Cascade-aware fetch tracking (fetched vs unavailable, distinct from
#   processed-iterations)
# - Single source of truth for "is anything running for this role?"
#
# The legacy /fetch-cvs, /batch-pre-screen, /batch-score endpoints stay
# as backwards-compatible wrappers used by callers we haven't migrated.
# ---------------------------------------------------------------------------


_PROCESS_CANCEL_PREFIX = "process_role:cancel:"


def is_process_cancelled(role_id: int) -> bool:
    return _is_cancelled(_PROCESS_CANCEL_PREFIX, role_id)


def _empty_process_progress() -> dict:
    return {
        "status": "idle",
        "role_name": None,
        "current_step": None,
        "fetch": {"attempted": 0, "fetched": 0, "unavailable": 0, "errors": 0, "total": 0},
        "pre_screen": {"total": 0, "processed": 0, "errors": 0},
        "score": {"total": 0, "scored": 0, "filtered": 0, "errors": 0, "mode": "none"},
    }


def _process_dry_run(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    fetch_cvs: bool,
    pre_screen: bool,
    refresh_pre_screen: bool,
    score_mode: str,
) -> dict:
    """Compute counts for each cascade step without starting the worker.

    Cascade-aware: when ``fetch_cvs`` is on, the pre-screen and score counts
    include candidates that will end up with a CV after the fetch step.
    """
    from ...services.pre_screening_service import application_needs_pre_screen

    apps = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate))
        .filter(
            CandidateApplication.role_id == role_id,
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )

    def has_cv(a):
        return bool((a.cv_text or "").strip())

    def will_have_cv(a):
        if has_cv(a):
            return True
        if not fetch_cvs:
            return False
        # Will be fetched only if source=workable.
        return (a.source or "") == "workable"

    # Fetch step
    will_fetch = (
        sum(1 for a in apps if not has_cv(a) and (a.source or "") == "workable")
        if fetch_cvs else 0
    )
    no_cv_no_workable = sum(
        1 for a in apps if not has_cv(a) and (a.source or "") != "workable"
    )

    # Pre-screen step
    if refresh_pre_screen:
        # Refresh = run for everyone who'll have a CV, regardless of prior result.
        will_pre_screen = sum(1 for a in apps if will_have_cv(a))
    elif pre_screen:
        will_pre_screen = sum(
            1 for a in apps
            if will_have_cv(a)
            and (
                # never run, or about to be fetched (so the existing rec is None anyway),
                # or stale (CV uploaded after pre-screen ran).
                a.pre_screen_recommendation is None
                or a.pre_screen_run_at is None
                or (a.cv_uploaded_at is not None and a.cv_uploaded_at > a.pre_screen_run_at)
            )
        )
    else:
        will_pre_screen = 0

    # Score step
    if score_mode == "all":
        will_score = sum(1 for a in apps if will_have_cv(a))
    elif score_mode == "new":
        def needs_score(a):
            if not will_have_cv(a):
                return False
            if a.cv_match_score is not None:
                # Already scored — only stale CV would force a rescore (we
                # don't auto-rescore on stale CV in score=new mode).
                return False
            # Below-threshold candidates whose pre-screen is still valid: skip.
            if (a.pre_screen_recommendation or "") == "Below threshold":
                if a.pre_screen_run_at is not None and (
                    a.cv_uploaded_at is None or a.cv_uploaded_at <= a.pre_screen_run_at
                ):
                    return False
            return True
        will_score = sum(1 for a in apps if needs_score(a))
    else:
        will_score = 0

    return {
        "fetch_cvs": {
            "will_attempt": int(will_fetch),
            "no_cv_no_workable": int(no_cv_no_workable),
        },
        "pre_screen": {
            "will_run": int(will_pre_screen),
            "refresh": bool(refresh_pre_screen),
        },
        "score": {
            "will_run": int(will_score),
            "mode": score_mode,
        },
        "total_candidates": len(apps),
    }


def _run_process(
    role_id: int,
    org_id: int,
    *,
    fetch_cvs: bool,
    pre_screen: bool,
    refresh_pre_screen: bool,
    score_mode: str,
) -> None:
    """Background worker: cascade fetch → pre-screen → score for one role.

    Updates ``_process_progress[role_id]`` in real time so the status endpoint
    can report combined progress.
    """
    from ...services.pre_screening_service import (
        application_needs_pre_screen,
        execute_pre_screen_only,
    )

    db = SessionLocal()
    progress = _process_progress.get(role_id) or _empty_process_progress()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        role = db.query(Role).filter(Role.id == role_id, Role.organization_id == org_id).first()
        if not org or not role:
            progress["status"] = "failed"
            _process_progress[role_id] = progress
            return

        progress["role_name"] = role.name
        progress["status"] = "running"
        progress["current_step"] = None
        _process_progress[role_id] = progress

        # ── Step 1: Fetch CVs ────────────────────────────────────────────
        if fetch_cvs:
            progress["current_step"] = "fetch"
            apps_to_fetch = (
                db.query(CandidateApplication)
                .options(joinedload(CandidateApplication.candidate))
                .filter(
                    CandidateApplication.role_id == role_id,
                    CandidateApplication.organization_id == org_id,
                    CandidateApplication.deleted_at.is_(None),
                    CandidateApplication.source == "workable",
                )
                .all()
            )
            apps_to_fetch = [a for a in apps_to_fetch if not (a.cv_text or "").strip()]
            progress["fetch"]["total"] = len(apps_to_fetch)
            _process_progress[role_id] = progress

            for idx, app in enumerate(apps_to_fetch):
                if is_process_cancelled(role_id):
                    progress["status"] = "cancelled"
                    _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
                    _process_progress[role_id] = progress
                    return
                try:
                    success = False
                    if app.candidate and (app.candidate.cv_text or "").strip():
                        # CV already on the candidate row — copy it onto the app.
                        app.cv_file_url = app.candidate.cv_file_url
                        app.cv_filename = app.candidate.cv_filename
                        app.cv_text = app.candidate.cv_text
                        app.cv_uploaded_at = app.candidate.cv_uploaded_at
                        success = True
                    elif (app.source or "") == "workable":
                        success = bool(_try_fetch_cv_from_workable(app, app.candidate, db, org))
                    if success:
                        progress["fetch"]["fetched"] += 1
                    else:
                        progress["fetch"]["unavailable"] += 1
                except Exception:
                    logger.exception("Process fetch failed for application_id=%s", app.id)
                    progress["fetch"]["errors"] += 1
                progress["fetch"]["attempted"] = idx + 1
                _process_progress[role_id] = progress
                if (idx + 1) % 3 == 0:
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
            try:
                db.commit()
            except Exception:
                db.rollback()

        # ── Step 2: Pre-screen ───────────────────────────────────────────
        if pre_screen or refresh_pre_screen:
            progress["current_step"] = "pre_screen"
            apps = _select_pre_screen_targets(
                db, role_id=role_id, organization_id=org_id, refresh=refresh_pre_screen
            )
            progress["pre_screen"]["total"] = len(apps)
            _process_progress[role_id] = progress

            for idx, app in enumerate(apps):
                if is_process_cancelled(role_id):
                    progress["status"] = "cancelled"
                    _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
                    _process_progress[role_id] = progress
                    return
                try:
                    result = execute_pre_screen_only(app)
                    if result.get("status") == "error":
                        progress["pre_screen"]["errors"] += 1
                    run_auto_reject_if_needed(
                        db=db, org=org, app=app, role=role, actor_type="system"
                    )
                    db.flush()
                except Exception:
                    logger.exception("Process pre-screen failed for application_id=%s", app.id)
                    progress["pre_screen"]["errors"] += 1
                progress["pre_screen"]["processed"] = idx + 1
                _process_progress[role_id] = progress
                if (idx + 1) % 5 == 0:
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
            try:
                db.commit()
            except Exception:
                db.rollback()

        # ── Step 3: Score ────────────────────────────────────────────────
        if score_mode in ("new", "all"):
            progress["current_step"] = "score"
            progress["score"]["mode"] = score_mode
            include_scored = score_mode == "all"
            apps_query = (
                db.query(CandidateApplication)
                .options(
                    joinedload(CandidateApplication.candidate),
                    joinedload(CandidateApplication.role),
                    joinedload(CandidateApplication.interviews),
                    joinedload(CandidateApplication.assessments).joinedload(Assessment.task),
                )
                .filter(
                    CandidateApplication.role_id == role_id,
                    CandidateApplication.organization_id == org_id,
                    CandidateApplication.deleted_at.is_(None),
                )
            )
            if not include_scored:
                apps_query = apps_query.filter(CandidateApplication.cv_match_score.is_(None))
            apps = apps_query.all()
            progress["score"]["total"] = len(apps)
            _process_progress[role_id] = progress

            job_spec_text = ((role.job_spec_text if role else None) or "").strip()
            for idx, app in enumerate(apps):
                if is_process_cancelled(role_id):
                    progress["status"] = "cancelled"
                    _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
                    _process_progress[role_id] = progress
                    return
                try:
                    cv_text = (app.cv_text or "").strip()
                    if not cv_text or not job_spec_text or not settings.ANTHROPIC_API_KEY:
                        # Can't score without inputs — count as filtered for visibility.
                        progress["score"]["filtered"] += 1
                        progress["score"]["scored"] = idx + 1
                        _process_progress[role_id] = progress
                        continue
                    if not include_scored:
                        if app.cv_match_score is not None:
                            progress["score"]["scored"] = idx + 1
                            _process_progress[role_id] = progress
                            continue
                        if (
                            (app.pre_screen_recommendation or "") == "Below threshold"
                            and app.pre_screen_run_at is not None
                            and (app.cv_uploaded_at is None or app.cv_uploaded_at <= app.pre_screen_run_at)
                        ):
                            progress["score"]["filtered"] += 1
                            progress["score"]["scored"] = idx + 1
                            _process_progress[role_id] = progress
                            continue
                    job = enqueue_score(db, app, force=include_scored)
                    if job is not None and job.status == "error":
                        progress["score"]["errors"] += 1
                    _refresh_rank_score(app)
                    refresh_application_score_cache(app, db=db)
                    refresh_application_interview_support(app)
                    run_auto_reject_if_needed(
                        db=db, org=org, app=app, role=role, actor_type="system"
                    )
                    db.flush()
                except Exception:
                    logger.exception("Process score failed for application_id=%s", app.id)
                    progress["score"]["errors"] += 1
                progress["score"]["scored"] = idx + 1
                _process_progress[role_id] = progress
                if (idx + 1) % 5 == 0:
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
            try:
                db.commit()
            except Exception:
                db.rollback()

        progress["current_step"] = None
        progress["status"] = "completed"
        _process_progress[role_id] = progress
    except Exception:
        logger.exception("Process cascade failed for role_id=%s", role_id)
        progress["status"] = "failed"
        _process_progress[role_id] = progress
    finally:
        db.close()


@router.post("/roles/{role_id}/process")
def process_role(
    role_id: int,
    payload: dict = Body(default={}),
    dry_run: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run the cascade: fetch CVs → pre-screen → score, all configurable.

    Body:
      - ``fetch_cvs`` (bool, default false)
      - ``pre_screen`` (bool, default false)
      - ``refresh_pre_screen`` (bool, default false) — overrides pre_screen,
        forces re-run for every candidate with a CV
      - ``score`` ("none" | "new" | "all", default "none")

    With ``?dry_run=true`` returns the per-step counts that would result.
    """
    role = get_role(role_id, current_user.organization_id, db)
    fetch_cvs = bool(payload.get("fetch_cvs"))
    pre_screen = bool(payload.get("pre_screen"))
    refresh_pre_screen = bool(payload.get("refresh_pre_screen"))
    score_mode = str(payload.get("score") or "none").lower()
    if score_mode not in ("none", "new", "all"):
        raise HTTPException(status_code=400, detail="score must be one of: none, new, all")
    if not (fetch_cvs or pre_screen or refresh_pre_screen or score_mode != "none"):
        raise HTTPException(status_code=400, detail="Pick at least one step to run")

    if (pre_screen or refresh_pre_screen or score_mode != "none") and not role_has_job_spec(role):
        raise HTTPException(status_code=400, detail="Upload job spec before pre-screen or scoring")

    if dry_run:
        counts = _process_dry_run(
            db,
            role_id=role_id,
            organization_id=current_user.organization_id,
            fetch_cvs=fetch_cvs,
            pre_screen=pre_screen,
            refresh_pre_screen=refresh_pre_screen,
            score_mode=score_mode,
        )
        counts["role_name"] = role.name
        return counts

    # Already running for this role? Return the current state — UI can decide
    # whether to surface "already running" or queue a follow-up.
    existing = _process_progress.get(role_id, {})
    if existing.get("status") in {"running", "cancelling"}:
        return {"status": "already_running", **existing}

    progress = _empty_process_progress()
    progress.update({
        "status": "running",
        "role_name": role.name,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "score": {**progress["score"], "mode": score_mode},
    })
    _process_progress[role_id] = progress
    _clear_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)

    thread = threading.Thread(
        target=_run_process,
        args=(role_id, current_user.organization_id),
        kwargs={
            "fetch_cvs": fetch_cvs,
            "pre_screen": pre_screen,
            "refresh_pre_screen": refresh_pre_screen,
            "score_mode": score_mode,
        },
        daemon=True,
    )
    thread.start()
    return {"status": "started", "role_name": role.name, **progress}


@router.get("/roles/{role_id}/process/status")
def process_role_status(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Combined cascade progress for the unified Process action."""
    get_role(role_id, current_user.organization_id, db)
    progress = _process_progress.get(role_id) or _empty_process_progress()
    return progress


@router.post("/roles/{role_id}/process/cancel")
def process_role_cancel(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_role(role_id, current_user.organization_id, db)
    _set_cancel_flag(_PROCESS_CANCEL_PREFIX, role_id)
    progress = _process_progress.get(role_id, {})
    if progress.get("status") == "running":
        progress["status"] = "cancelling"
        _process_progress[role_id] = progress
    return {"ok": True, "role_id": role_id, "status": progress.get("status", "idle")}


# ---------------------------------------------------------------------------
# Add role_name to the legacy status endpoints so the toaster can label rows
# with the role name instead of "Role #N".
# ---------------------------------------------------------------------------


def _attach_role_name(progress: dict, role_name: str | None) -> dict:
    out = dict(progress or {})
    if role_name:
        out["role_name"] = role_name
    return out


@router.post("/applications/{application_id}/assessments", status_code=status.HTTP_201_CREATED)
def create_assessment_for_application(
    application_id: int,
    data: AssessmentFromApplicationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == app.role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if not any(task.id == data.task_id for task in (role.tasks or [])):
        raise HTTPException(status_code=400, detail="Task is not linked to this role")
    task = db.query(Task).filter(
        Task.id == data.task_id,
        (Task.organization_id == current_user.organization_id) | (Task.organization_id == None),  # noqa: E711
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    creation_gate = get_assessment_creation_gate(
        current_user.organization_id,
        db,
        lock_organization=True,
    )
    if not creation_gate.get("can_create"):
        raise HTTPException(status_code=402, detail=creation_gate.get("message"))
    existing = _latest_active_assessment_for_application(app, db)
    if existing is not None:
        raise _assessment_create_conflict_response(existing)

    try:
        ensure_pipeline_fields(app)
        initialize_pipeline_event_if_missing(
            db,
            app=app,
            actor_type="system",
            actor_id=current_user.id,
            reason="Pipeline initialized before assessment create",
        )
        transition_stage(
            db,
            app=app,
            to_stage="invited",
            source="recruiter",
            actor_type="recruiter",
            actor_id=current_user.id,
            reason="Assessment invite created",
        )
        append_application_event(
            db,
            app=app,
            event_type="assessment_invite_sent",
            actor_type="recruiter",
            actor_id=current_user.id,
            reason="Task sent",
            metadata={
                "task_id": data.task_id,
                "duration_minutes": data.duration_minutes,
            },
        )
        assessment = _create_application_assessment(
            app=app,
            role=role,
            task=task,
            duration_minutes=data.duration_minutes,
            current_user=current_user,
            db=db,
        )
        refresh_application_score_cache(app, db=db)
        db.commit()
    except AssessmentRepositoryError:
        db.rollback()
        logger.exception("Assessment repository provisioning failed for application_id=%s", app.id)
        raise HTTPException(status_code=500, detail="Failed to initialize assessment repository")
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        if _is_active_role_assessment_integrity_error(exc):
            existing = _latest_active_assessment_for_application(app, db)
            if existing is not None:
                raise _assessment_create_conflict_response(existing)
        logger.exception("Failed to create assessment for application_id=%s", app.id)
        raise HTTPException(status_code=500, detail="Failed to create assessment")
    return assessment_to_response(assessment, db)


@router.post("/applications/{application_id}/assessments/retake", status_code=status.HTTP_201_CREATED)
def retake_assessment_for_application(
    application_id: int,
    data: AssessmentRetakeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(Role.id == app.role_id, Role.organization_id == current_user.organization_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if not any(task.id == data.task_id for task in (role.tasks or [])):
        raise HTTPException(status_code=400, detail="Task is not linked to this role")
    task = db.query(Task).filter(
        Task.id == data.task_id,
        (Task.organization_id == current_user.organization_id) | (Task.organization_id == None),  # noqa: E711
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    existing = _latest_active_assessment_for_application(app, db)
    if existing is None:
        raise HTTPException(status_code=400, detail="No valid assessment exists for this candidate and role")
    creation_gate = get_assessment_creation_gate(
        current_user.organization_id,
        db,
        exclude_assessment_id=existing.id,
        lock_organization=True,
    )
    if not creation_gate.get("can_create"):
        raise HTTPException(status_code=402, detail=creation_gate.get("message"))

    try:
        ensure_pipeline_fields(app)
        initialize_pipeline_event_if_missing(
            db,
            app=app,
            actor_type="system",
            actor_id=current_user.id,
            reason="Pipeline initialized before assessment retake",
        )
        transition_stage(
            db,
            app=app,
            to_stage="invited",
            source="recruiter",
            actor_type="recruiter",
            actor_id=current_user.id,
            reason="Assessment retake created",
        )
        append_application_event(
            db,
            app=app,
            event_type="assessment_retake_sent",
            actor_type="recruiter",
            actor_id=current_user.id,
            reason="Task retake sent",
            metadata={
                "task_id": data.task_id,
                "duration_minutes": data.duration_minutes,
                "void_reason": data.void_reason,
                "previous_assessment_id": existing.id,
            },
        )
        assessment = _create_application_assessment(
            app=app,
            role=role,
            task=task,
            duration_minutes=data.duration_minutes,
            current_user=current_user,
            db=db,
            void_existing=existing,
            void_reason=data.void_reason,
        )
        refresh_application_score_cache(app, db=db)
        db.commit()
    except AssessmentRepositoryError:
        db.rollback()
        logger.exception("Assessment retake provisioning failed for application_id=%s", app.id)
        raise HTTPException(status_code=500, detail="Failed to initialize assessment repository")
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        if _is_active_role_assessment_integrity_error(exc):
            existing = _latest_active_assessment_for_application(app, db)
            if existing is not None:
                raise _assessment_create_conflict_response(existing)
        logger.exception("Failed to retake assessment for application_id=%s", app.id)
        raise HTTPException(status_code=500, detail="Failed to create retake assessment")
    return assessment_to_response(assessment, db)
