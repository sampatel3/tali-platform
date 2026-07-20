from __future__ import annotations

import json
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    append_assessment_timeline_event,
    bind_candidate_session,
    ensure_utc,
    utcnow,
    validate_assessment_token,
)
from ...components.assessments.task_snapshot import (
    freeze_assessment_task,
    task_view_for_assessment,
)
from ...components.assessments.submission_runtime import build_submission_receipt
from ...components.assessments.service import (
    CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE,
    _enforce_artifact_first_task,
    _sandbox_workspace_is_ready,
    _workspace_repo_root,
    enforce_active_or_timeout,
    enforce_not_paused,
    get_assessment_start_gate,
    start_or_resume_assessment,
    store_cv_upload,
    submit_assessment as _submit_assessment,
)
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.task import Task
from ...platform.config import settings
from ...platform.database import get_db
from ...services.task_repo_service import normalize_repo_files
from ...schemas.assessment import (
    AssessmentStart,
    AssessmentStartRequest,
    DemoBookingRequest,
    DemoBookingResponse,
    DemoAssessmentStartRequest,
    SubmitRequest,
)

from .candidate_claude_chat_routes import router as candidate_claude_chat_router
from .candidate_auth import (
    require_candidate_request_proof,
    validate_runtime_candidate_session,
)
from .candidate_proof import (
    PROOF_KEY_ID_HEADER,
    PROOF_NONCE_HEADER,
    PROOF_SIGNATURE_HEADER,
    PROOF_TIMESTAMP_HEADER,
    bind_candidate_proof_key,
    headers_from_values,
    request_path_and_query,
    verify_and_consume_candidate_start_proof,
)
from .candidate_workspace import (
    MAX_CANDIDATE_SNAPSHOT_FILES,
    execution_stdout_text,
    sanitize_repo_path,
)

router = APIRouter()
router.include_router(candidate_claude_chat_router)

logger = logging.getLogger(__name__)


DEMO_ORG_SLUG = "taali-demo"
DEMO_ORG_NAME = "TAALI Demo Leads"
DEMO_TRACK_TASK_KEYS = {
    # Current flagships plus aliases retained for existing demo links.
    "data_eng_bronze_ingestion": "data_eng_bronze_ingestion",
    "ai_eng_genai_production_readiness": "ai_eng_genai_production_readiness",
    # Backward-compatible aliases (route to current tasks; legacy keys removed from repo).
    "data_eng_aws_glue_pipeline_recovery": "data_eng_bronze_ingestion",
    "data_eng_super_platform_crisis": "data_eng_bronze_ingestion",
    "ai_eng_super_production_launch": "ai_eng_genai_production_readiness",
    "data_eng_a_pipeline_reliability": "data_eng_bronze_ingestion",
    "data_eng_b_cdc_fix": "data_eng_bronze_ingestion",
    "data_eng_c_backfill_schema": "data_eng_bronze_ingestion",
    "backend-reliability": "data_eng_bronze_ingestion",
    "frontend-debugging": "data_eng_bronze_ingestion",
    "data-pipeline": "data_eng_bronze_ingestion",
}
DEMO_TRACK_KEYS = set(DEMO_TRACK_TASK_KEYS.keys())
_MAX_CANDIDATE_CHAT_HISTORY = 60


def _load_assessment_task(
    assessment: Assessment,
    db: Session,
    *,
    freeze_if_missing: bool = True,
) -> object:
    live_task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not live_task:
        raise HTTPException(status_code=404, detail="Task not found")
    if (
        not freeze_if_missing
        and getattr(assessment, "task_spec_snapshot", None) is None
        and not getattr(assessment, "task_spec_snapshot_sha256", None)
    ):
        return live_task
    try:
        return task_view_for_assessment(assessment, live_task)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="This assessment's task definition could not be verified. Please contact the hiring team.",
        ) from exc


def _candidate_chat_history(raw_prompts: object) -> list[dict[str, object]]:
    """Return only the transcript fields the candidate UI actually renders."""
    if not isinstance(raw_prompts, list):
        return []
    safe_history: list[dict[str, object]] = []
    for raw_entry in raw_prompts[-_MAX_CANDIDATE_CHAT_HISTORY:]:
        if not isinstance(raw_entry, dict):
            continue
        safe_history.append(
            {
                "message": str(raw_entry.get("message") or ""),
                "response": str(raw_entry.get("response") or ""),
                "opener": bool(raw_entry.get("opener", False)),
            }
        )
    return safe_history


def _sandbox_repo_exists(sandbox: object, repo_root: str) -> bool:
    result = sandbox.run_code(
        "import json, pathlib\n"
        f"repo_root = pathlib.Path({repo_root!r})\n"
        "print(json.dumps({'exists': repo_root.exists(), 'is_dir': repo_root.is_dir()}))\n"
    )
    try:
        lines = execution_stdout_text(result).strip().splitlines()
        payload = json.loads(lines[-1]) if lines else {}
    except Exception:
        logger.exception("Failed to inspect sandbox repo root=%s", repo_root)
        return False
    return bool(payload.get("exists")) and bool(payload.get("is_dir"))


def _ensure_assessment_workspace_ready(e2b: object, sandbox: object, assessment: Assessment, task: Task) -> str:
    repo_root = _workspace_repo_root(task)
    if _sandbox_repo_exists(sandbox, repo_root) and _sandbox_workspace_is_ready(sandbox, task):
        return repo_root
    # Runtime routes operate only after start. Recreating the task baseline at
    # this point would silently erase candidate work if E2B returned a fresh or
    # damaged sandbox, so reconnect failure is terminal for this request.
    raise HTTPException(
        status_code=503,
        detail="The existing workspace could not be reconnected. Please retry; no replacement workspace was created.",
    )


def _connect_assessment_sandbox(e2b: object, assessment: Assessment, task: Task, db: Session) -> tuple[object, str]:
    if assessment.e2b_session_id:
        try:
            sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
        except Exception as exc:
            logger.warning(
                "Could not reconnect existing candidate sandbox assessment_id=%s",
                assessment.id,
            )
            raise HTTPException(
                status_code=503,
                detail="The existing workspace could not be reconnected. Please retry; no replacement workspace was created.",
            ) from exc
    else:
        raise HTTPException(
            status_code=503,
            detail="The assessment workspace session is unavailable. Please contact the hiring team.",
        )

    repo_root = _ensure_assessment_workspace_ready(e2b, sandbox, assessment, task)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to persist assessment sandbox/session state assessment_id=%s", assessment.id)
    return sandbox, repo_root


def _ensure_demo_org(db: Session):
    from ...models.organization import Organization

    org = db.query(Organization).filter(Organization.slug == DEMO_ORG_SLUG).first()
    if org:
        return org

    org = Organization(name=DEMO_ORG_NAME, slug=DEMO_ORG_SLUG, plan="pay_per_use")
    db.add(org)
    try:
        db.commit()
    except Exception:
        db.rollback()
        org = db.query(Organization).filter(Organization.slug == DEMO_ORG_SLUG).first()
        if org:
            return org
        raise HTTPException(status_code=500, detail="Failed to initialize demo organization")

    db.refresh(org)
    return org


def _resolve_demo_task(db: Session, org_id: int, track: str) -> Task | None:
    task_key = DEMO_TRACK_TASK_KEYS.get(track)
    if task_key:
        org_task = (
            db.query(Task)
            .filter(
                Task.is_active == True,  # noqa: E712
                Task.organization_id == org_id,
                Task.task_key == task_key,
            )
            .order_by(Task.id.asc())
            .first()
        )
        if org_task:
            return org_task

        global_task = (
            db.query(Task)
            .filter(
                Task.is_active == True,  # noqa: E712
                Task.organization_id == None,  # noqa: E711
                Task.task_key == task_key,
            )
            .order_by(Task.id.asc())
            .first()
        )
        if global_task:
            return global_task

    return None


def _upsert_demo_candidate(
    *,
    db: Session,
    org_id: int,
    full_name: str,
    position: str | None,
    email: str,
    work_email: str | None,
    company_name: str,
    company_size: str,
    marketing_consent: bool,
    lead_source: str,
    workable_data_updates: dict[str, object] | None = None,
) -> Candidate:
    normalized_email = str(email).strip().lower()
    normalized_work_email = str(work_email).strip().lower() if work_email else None

    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.organization_id == org_id,
            Candidate.email == normalized_email,
        )
        .first()
    )
    if not candidate:
        candidate = Candidate(
            organization_id=org_id,
            email=normalized_email,
        )
        db.add(candidate)
        db.flush()

    existing_workable_data = candidate.workable_data if isinstance(candidate.workable_data, dict) else {}

    candidate.full_name = full_name
    candidate.position = position
    candidate.work_email = normalized_work_email
    candidate.company_name = company_name
    candidate.company_size = company_size
    candidate.lead_source = lead_source
    candidate.marketing_consent = bool(marketing_consent)
    candidate.workable_data = {
        **existing_workable_data,
        **(workable_data_updates or {}),
    }
    return candidate


def _candidate_start_response(raw: object, *, include_token: bool = False) -> object:
    """Strip control-plane fields from the candidate start DTO."""
    if not isinstance(raw, dict):
        return raw
    response = dict(raw)
    if not include_token:
        response.pop("token", None)
    for key in (
        "sandbox_id",
        "repo_url",
        "branch_name",
        "clone_command",
        "terminal_mode",
        "terminal_capabilities",
    ):
        response.pop(key, None)
    response["ai_prompts"] = _candidate_chat_history(response.get("ai_prompts"))

    task_payload = response.get("task")
    if isinstance(task_payload, dict):
        candidate_task = dict(task_payload)
        for key in (
            "task_key",
            "evaluation_rubric",
            "extra_data",
            "claude_budget_limit_usd",
        ):
            candidate_task.pop(key, None)
        # Live start returns a blank manifest; the editor fetches one file at a time.
        manifest = normalize_repo_files(candidate_task.get("repo_structure"))
        safe_manifest: dict[str, str] = {}
        for path, content in manifest.items():
            safe_path = sanitize_repo_path(path)
            if safe_path:
                safe_manifest[safe_path] = str(content or "") if include_token else ""
            if len(safe_manifest) >= MAX_CANDIDATE_SNAPSHOT_FILES:
                break
        if not include_token:
            candidate_task["starter_code"] = ""
        candidate_task["repo_structure"] = {"files": safe_manifest}
        response["task"] = candidate_task
    return response


@router.post(
    "/token/{token}/start",
    response_model=AssessmentStart,
    response_model_exclude_none=True,
)
async def start_assessment(
    token: str,
    request: Request,
    payload: AssessmentStartRequest | None = None,
    x_assessment_key_id: str | None = Header(None, alias=PROOF_KEY_ID_HEADER),
    x_assessment_proof_timestamp: str | None = Header(None, alias=PROOF_TIMESTAMP_HEADER),
    x_assessment_proof_nonce: str | None = Header(None, alias=PROOF_NONCE_HEADER),
    x_assessment_proof: str | None = Header(None, alias=PROOF_SIGNATURE_HEADER),
    db: Session = Depends(get_db),
):
    """Candidate starts or resumes an assessment via token."""
    token_target = (
        db.query(Assessment.id, Assessment.is_voided)
        .filter(Assessment.token == token)
        .first()
    )
    if not token_target:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    if bool(token_target.is_voided):
        raise HTTPException(status_code=400, detail="assessment_voided")
    session_key = payload.candidate_session_key if payload is not None else None
    if not session_key:
        raise HTTPException(status_code=422, detail="candidate_session_key is required")
    candidate_key_id = payload.candidate_proof_key_id if payload is not None else None
    candidate_public_jwk = payload.candidate_proof_public_jwk if payload is not None else None
    if not candidate_key_id:
        raise HTTPException(status_code=422, detail="candidate_proof_key_id is required")
    if not candidate_public_jwk:
        raise HTTPException(status_code=422, detail="candidate_proof_public_jwk is required")
    proof_headers = headers_from_values(
        key_id=x_assessment_key_id,
        timestamp=x_assessment_proof_timestamp,
        nonce=x_assessment_proof_nonce,
        signature=x_assessment_proof,
    )
    admission = verify_and_consume_candidate_start_proof(
        token=token,
        session_key=session_key,
        candidate_key_id=candidate_key_id,
        candidate_public_jwk=candidate_public_jwk,
        headers=proof_headers,
        method=request.method,
        path_and_query=request_path_and_query(request),
        raw_body=await request.body(),
    )
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == admission.assessment_id)
        .with_for_update()
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    session_was_bound = bind_candidate_session(assessment, session_key)
    proof_key_was_bound = bind_candidate_proof_key(
        assessment,
        candidate_key_id=candidate_key_id,
        candidate_public_jwk=admission.normalized_public_jwk,
    )
    if session_was_bound:
        append_assessment_timeline_event(assessment, "candidate_session_bound")
    if proof_key_was_bound:
        append_assessment_timeline_event(
            assessment,
            "candidate_proof_key_bound",
            {"key_id": candidate_key_id},
        )
    db.flush()
    try:
        return _candidate_start_response(start_or_resume_assessment(assessment, db))
    except HTTPException as exc:
        if exc.status_code == 402:
            raise HTTPException(status_code=402, detail=CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE) from exc
        raise


@router.get("/token/{token}/preview")
def preview_assessment(token: str, db: Session = Depends(get_db)):
    """Return candidate-facing task context without starting the assessment timer."""
    assessment = db.query(Assessment).filter(Assessment.token == token).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    if bool(getattr(assessment, "is_voided", False)):
        raise HTTPException(status_code=400, detail="assessment_voided")
    if assessment.expires_at and ensure_utc(assessment.expires_at) < utcnow():
        raise HTTPException(status_code=400, detail="Assessment link has expired")

    task = _load_assessment_task(assessment, db, freeze_if_missing=False)

    start_gate = get_assessment_start_gate(assessment, db)

    # Funnel telemetry: stamp the FIRST preview view (repeat visits don't
    # rewrite it) so opened-email → previewed → started is a visible chain.
    if getattr(assessment, "preview_viewed_at", None) is None:
        assessment.preview_viewed_at = utcnow()
        append_assessment_timeline_event(
            assessment,
            "preview_viewed",
            {"can_start": bool(start_gate.get("can_start"))},
        )
        try:
            db.commit()
        except Exception:
            logger.exception("Failed to commit preview_viewed assessment_id=%s", assessment.id)
            db.rollback()

    return {
        "candidate_name": getattr(getattr(assessment, "candidate", None), "full_name", None),
        "organization_name": getattr(getattr(assessment, "organization", None), "name", None),
        "expires_at": assessment.expires_at,
        "duration_minutes": assessment.duration_minutes,
        "allow_external_clipboard": bool(
            getattr(assessment, "allow_external_clipboard", False)
        ),
        "start_gate": {
            "can_start": bool(start_gate.get("can_start")),
            "reason": start_gate.get("reason"),
            "message": start_gate.get("message"),
        },
        "task": {
            "role": task.role,
            "duration_minutes": assessment.duration_minutes,
        },
    }


@router.post(
    "/demo/start",
    response_model=AssessmentStart,
    response_model_exclude_none=True,
)
def start_demo_assessment(
    data: DemoAssessmentStartRequest,
    db: Session = Depends(get_db),
):
    """Create a demo lead + assessment and start the normal runtime session."""
    if not settings.LIVE_ASSESSMENT_DEMO_ENABLED:
        # The public product walkthrough is fixture-backed and never needs a
        # paid E2B/Claude runtime. Fail closed so this legacy unauthenticated
        # endpoint cannot be used as an infrastructure-spend primitive.
        raise HTTPException(status_code=404, detail="Not found")
    track = str(data.assessment_track or "").strip().lower()
    if track not in DEMO_TRACK_KEYS:
        raise HTTPException(status_code=400, detail="Unsupported demo assessment track")

    org = _ensure_demo_org(db)
    task = _resolve_demo_task(db, org.id, track)
    if not task:
        raise HTTPException(status_code=503, detail="No demo assessment task is available yet")

    candidate = _upsert_demo_candidate(
        db=db,
        org_id=org.id,
        full_name=data.full_name,
        position=data.position,
        email=data.email,
        work_email=data.work_email,
        company_name=data.company_name,
        company_size=data.company_size,
        marketing_consent=bool(data.marketing_consent),
        lead_source="landing_demo",
        workable_data_updates={
            "demo_track": track,
            "marketing_consent": bool(data.marketing_consent),
        },
    )

    normalized_email = str(data.email).strip().lower()
    normalized_work_email = str(data.work_email).strip().lower() if data.work_email else None

    _enforce_artifact_first_task(task)
    assessment = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        token=secrets.token_urlsafe(32),
        duration_minutes=task.duration_minutes or 30,
        expires_at=utcnow() + timedelta(days=settings.ASSESSMENT_EXPIRY_DAYS),
        is_demo=True,
        demo_track=track,
        demo_profile={
            "full_name": data.full_name,
            "position": data.position,
            "email": normalized_email,
            "work_email": normalized_work_email,
            "company_name": data.company_name,
            "company_size": data.company_size,
            "marketing_consent": bool(data.marketing_consent),
            "lead_source": "landing_demo",
        },
    )
    freeze_assessment_task(assessment, task)
    bind_candidate_session(assessment, data.candidate_session_key)
    append_assessment_timeline_event(assessment, "candidate_session_bound")
    db.add(assessment)
    try:
        db.commit()
    except Exception:
        logger.exception("Failed to commit demo assessment creation")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create demo assessment")

    db.refresh(assessment)
    return _candidate_start_response(
        start_or_resume_assessment(assessment, db),
        include_token=True,
    )


@router.post("/demo/request", response_model=DemoBookingResponse)
def request_demo_walkthrough(
    data: DemoBookingRequest,
    db: Session = Depends(get_db),
):
    """Store a public demo-booking lead without starting a candidate runtime session."""
    org = _ensure_demo_org(db)
    candidate = _upsert_demo_candidate(
        db=db,
        org_id=org.id,
        full_name=data.full_name,
        position=data.position,
        email=data.email,
        work_email=data.work_email,
        company_name=data.company_name,
        company_size=data.company_size,
        marketing_consent=bool(data.marketing_consent),
        lead_source="book_demo",
        workable_data_updates={
            "demo_request": {
                "requested_at": utcnow().isoformat(),
                "source": "book_demo_page",
                "marketing_consent": bool(data.marketing_consent),
            },
        },
    )
    try:
        db.commit()
    except Exception:
        logger.exception("Failed to commit demo request")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save demo request")

    db.refresh(candidate)
    return DemoBookingResponse(candidate_id=candidate.id)


@router.post("/{assessment_id}/upload-cv")
def upload_assessment_cv(
    assessment_id: int,
    file: UploadFile = File(...),
    token: str = Form(...),
    x_assessment_session: str | None = Header(None, description="Live candidate browser session key"),
    db: Session = Depends(get_db),
):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if bool(getattr(assessment, "is_voided", False)):
        raise HTTPException(status_code=400, detail="assessment_voided")
    if not secrets.compare_digest(assessment.token or "", token or ""):
        raise HTTPException(status_code=401, detail="Invalid assessment token")
    if assessment.status not in {AssessmentStatus.PENDING, AssessmentStatus.IN_PROGRESS}:
        raise HTTPException(status_code=400, detail="Assessment already submitted")
    enforce_not_paused(assessment)
    if assessment.status == AssessmentStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=409,
            detail="CV evidence is frozen once the assessment starts",
        )
    return store_cv_upload(assessment, file, db)


@router.post("/token/{token}/upload-cv")
def upload_assessment_cv_by_token(
    token: str,
    file: UploadFile = File(...),
    x_assessment_session: str | None = Header(None, description="Live candidate browser session key"),
    db: Session = Depends(get_db),
):
    assessment = db.query(Assessment).filter(Assessment.token == token).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    if bool(getattr(assessment, "is_voided", False)):
        raise HTTPException(status_code=400, detail="assessment_voided")
    if assessment.status not in {AssessmentStatus.PENDING, AssessmentStatus.IN_PROGRESS}:
        raise HTTPException(status_code=400, detail="Assessment already submitted")
    enforce_not_paused(assessment)
    if assessment.status == AssessmentStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=409,
            detail="CV evidence is frozen once the assessment starts",
        )
    return store_cv_upload(assessment, file, db)


@router.post("/{assessment_id}/submit")
def submit_assessment_endpoint(
    assessment_id: int,
    data: SubmitRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    x_assessment_session: str | None = Header(None, description="Live candidate browser session key"),
    db: Session = Depends(get_db),
    _request_proof: None = Depends(require_candidate_request_proof),
):
    """Freeze candidate work and return its durable submission receipt."""
    assessment = (
        db.query(Assessment)
        .filter(
            Assessment.id == assessment_id,
            Assessment.is_voided.is_(False),
        )
        .first()
    )
    if assessment is None:
        raise HTTPException(status_code=404, detail="Active assessment not found")
    validate_assessment_token(assessment, x_assessment_token)
    validate_runtime_candidate_session(assessment, x_assessment_session)
    if data.repo_files:
        raise HTTPException(status_code=400, detail="Bulk repository replacement is disabled")
    task = _load_assessment_task(assessment, db)
    if assessment.status in {
        AssessmentStatus.COMPLETED,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
    }:
        try:
            return build_submission_receipt(assessment, task)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409,
                detail="Assessment ended without a recoverable submission receipt",
            ) from exc
    if assessment.status != AssessmentStatus.IN_PROGRESS:
        raise HTTPException(status_code=404, detail="Active assessment not found")

    enforce_not_paused(assessment)
    # If the clock ran out before this request landed, freeze on the timeout
    # path. Unlike every other runtime action, submit can acknowledge that
    # successful freeze with the same durable receipt as an idempotent retry.
    try:
        enforce_active_or_timeout(assessment, db)
    except HTTPException as exc:
        if (
            exc.status_code != 409
            or assessment.status != AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
        ):
            raise
        try:
            return build_submission_receipt(assessment, task)
        except RuntimeError as receipt_exc:
            raise HTTPException(
                status_code=409,
                detail="Assessment ended without a recoverable submission receipt",
            ) from receipt_exc
    return _submit_assessment(
        assessment,
        data.final_code,
        data.tab_switch_count,
        db,
        defer_scoring=True,
    )
