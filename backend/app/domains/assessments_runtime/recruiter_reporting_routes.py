from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from ...deps import get_current_user
from ...models.assessment import Assessment, AssessmentStatus
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...services.ai_assisted_evaluator import generate_ai_suggestions
from ...services.candidate_feedback_engine import (
    build_candidate_feedback_payload,
    build_interview_debrief_payload,
)
from ...services.evaluation_result_service import (
    build_evaluation_result,
    normalize_stored_evaluation_result,
)
from ...components.assessments.repository import utcnow
from ...components.notifications.service import send_candidate_feedback_ready_sync

router = APIRouter()


def _is_completed(assessment: Assessment) -> bool:
    raw = getattr(assessment.status, "value", assessment.status)
    return str(raw or "").lower() in {
        AssessmentStatus.COMPLETED.value,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value,
    }


def _candidate_feedback_link(token: str) -> str:
    return f"{settings.FRONTEND_URL}/assessment/{token}/feedback"


def _dispatch_candidate_feedback_email(
    *,
    candidate_email: str,
    candidate_name: str,
    org_name: str,
    role_title: str,
    feedback_link: str,
) -> None:
    if settings.MVP_DISABLE_CELERY:
        send_candidate_feedback_ready_sync(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            org_name=org_name,
            role_title=role_title,
            feedback_link=feedback_link,
        )
        return
    from ...tasks.assessment_tasks import send_candidate_feedback_ready_email

    send_candidate_feedback_ready_email.delay(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        org_name=org_name,
        role_title=role_title,
        feedback_link=feedback_link,
        request_id=get_request_id(),
    )


@router.get("/{assessment_id}/report.pdf")
def download_assessment_report_pdf(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a branded PDF report without external dependencies."""
    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    candidate_name = (assessment.candidate.full_name if assessment.candidate else None) or (
        assessment.candidate.email if assessment.candidate else "Candidate"
    )
    task_name = assessment.task.name if assessment.task else "Assessment"
    status_value = assessment.status.value if hasattr(assessment.status, "value") else str(assessment.status)
    analytics = assessment.prompt_analytics or {}
    components = analytics.get("component_scores", {}) if isinstance(analytics, dict) else {}
    heuristics = analytics.get("heuristics", {}) if isinstance(analytics, dict) else {}
    fraud_flags = assessment.prompt_fraud_flags or []

    component_lines = []
    for key in (
        "tests",
        "code_quality",
        "prompt_quality",
        "prompt_efficiency",
        "independence",
        "context_utilization",
        "design_thinking",
        "debugging_strategy",
        "written_communication",
    ):
        if key in components:
            component_lines.append(f"  - {key}: {components[key]}")
    if not component_lines:
        component_lines.append("  - No component scores available")

    h_focus = (heuristics.get("browser_focus_ratio") or {}).get("ratio")
    h_tab = (heuristics.get("tab_switch_count") or {}).get("count")
    h_first = (heuristics.get("time_to_first_prompt") or {}).get("value")
    analytics_lines = [
        f"  - Browser focus ratio: {h_focus if h_focus is not None else 'N/A'}",
        f"  - Tab switches: {h_tab if h_tab is not None else (assessment.tab_switch_count or 0)}",
        f"  - Time to first prompt (s): {h_first if h_first is not None else 'N/A'}",
    ]

    fraud_lines = [
        f"  - {f.get('type')}: {f.get('evidence', '')} (confidence={f.get('confidence')})"
        for f in fraud_flags
    ]
    if not fraud_lines:
        fraud_lines = ["  - None detected"]

    body_text = (
        "TAALI - AI-Augmented Technical Assessment Report\\n"
        "============================================\\n"
        f"Assessment ID: {assessment.id}\\n"
        f"Candidate: {candidate_name}\\n"
        f"Task: {task_name}\\n"
        f"Status: {status_value}\\n"
        f"Overall Score: {assessment.score if assessment.score is not None else 'N/A'}/10\\n"
        f"Calibration Score: {assessment.calibration_score if assessment.calibration_score is not None else 'N/A'}/10\\n"
        f"Tests Passed: {assessment.tests_passed or 0}/{assessment.tests_total or 0}\\n"
        f"Code Quality: {assessment.code_quality_score if assessment.code_quality_score is not None else 'N/A'}\\n"
        "\\n"
        "Component Score Breakdown\\n"
        "-------------------------\\n"
        f"{chr(10).join(component_lines)}\\n"
        "\\n"
        "Prompt Analytics Summary\\n"
        "------------------------\\n"
        f"{chr(10).join(analytics_lines)}\\n"
        "\\n"
        "Fraud / Proctoring Flags\\n"
        "------------------------\\n"
        f"{chr(10).join(fraud_lines)}\\n"
    )
    escaped = body_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 50 780 Td ({escaped.replace(chr(10), ') Tj T* (')}) Tj ET"
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    )
    content = stream.encode("latin-1", errors="ignore")
    pdf += (
        f"5 0 obj << /Length {len(content)} >> stream\n".encode("ascii") + content + b"\nendstream endobj\n"
    )
    xref_pos = len(pdf)
    xref = (
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000241 00000 n \n"
        b"0000000311 00000 n \n"
    )
    trailer = f"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    final_pdf = pdf + xref + trailer
    return Response(
        content=final_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="assessment-{assessment.id}.pdf"'},
    )


@router.patch("/{assessment_id}/manual-evaluation")
def update_manual_evaluation(
    assessment_id: int,
    body: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save manual rubric evaluation (excellent/good/poor per category + evidence)."""
    assessment = (
        db.query(Assessment)
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    category_scores = body.get("category_scores")
    if category_scores is not None and not isinstance(category_scores, dict):
        raise HTTPException(status_code=400, detail="category_scores must be an object")

    rubric = (assessment.task.evaluation_rubric if assessment.task else None) or {}
    try:
        evaluation_result = build_evaluation_result(
            assessment_id=assessment.id,
            completed_due_to_timeout=bool(getattr(assessment, "completed_due_to_timeout", False)),
            evaluation_rubric=rubric,
            body=body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    assessment.manual_evaluation = evaluation_result
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save manual evaluation")
    normalized = normalize_stored_evaluation_result(
        assessment.manual_evaluation,
        assessment_id=assessment.id,
        completed_due_to_timeout=bool(getattr(assessment, "completed_due_to_timeout", False)),
        evaluation_rubric=rubric,
    )
    return {
        "success": True,
        "manual_evaluation": normalized,
        "evaluation_result": normalized,
    }


@router.post("/{assessment_id}/notes")
def add_assessment_note(
    assessment_id: int,
    body: Dict[str, str],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="note is required")
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.organization_id == current_user.organization_id,
    ).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    existing_timeline = assessment.timeline or []
    timestamp = utcnow().isoformat()
    existing_timeline.append(
        {
            "event_type": "note",
            "type": "note",
            "timestamp": timestamp,
            "time": utcnow().isoformat(),
            "event": "Recruiter note",
            "text": note,
            "prompt": note,
            "author": (current_user.full_name or current_user.email or "Recruiter"),
        }
    )
    assessment.timeline = existing_timeline
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save note")
    return {"success": True, "timeline": assessment.timeline}


@router.post("/{assessment_id}/finalize-candidate-feedback")
def finalize_candidate_feedback(
    assessment_id: int,
    body: Dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payload = body or {}
    force_regenerate = bool(payload.get("force_regenerate", False))
    send_email = bool(payload.get("send_email", True))
    resend_email = bool(payload.get("resend_email", False))
    include_feedback = bool(payload.get("include_feedback", True))

    assessment = (
        db.query(Assessment)
        .options(
            joinedload(Assessment.candidate),
            joinedload(Assessment.task),
            joinedload(Assessment.role),
            joinedload(Assessment.organization),
        )
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if not _is_completed(assessment):
        raise HTTPException(status_code=400, detail="Assessment must be completed before feedback finalization")

    org = assessment.organization
    org_feedback_enabled = bool(getattr(org, "candidate_feedback_enabled", True))
    assessment_feedback_enabled = bool(getattr(assessment, "candidate_feedback_enabled", True))
    if not org_feedback_enabled or not assessment_feedback_enabled:
        raise HTTPException(status_code=403, detail="Candidate feedback is disabled for this organization")

    should_generate = (
        force_regenerate
        or not bool(getattr(assessment, "candidate_feedback_ready", False))
        or not isinstance(getattr(assessment, "candidate_feedback_json", None), dict)
    )
    if should_generate:
        generated_payload = build_candidate_feedback_payload(
            db,
            assessment,
            organization_name=(org.name if org and org.name else "Company"),
        )
        assessment.candidate_feedback_json = generated_payload
        assessment.candidate_feedback_generated_at = utcnow()
        assessment.candidate_feedback_ready = True
    else:
        generated_payload = assessment.candidate_feedback_json or {}

    feedback_link = _candidate_feedback_link(assessment.token)
    email_dispatched = False
    if send_email and assessment.candidate and assessment.candidate.email:
        should_send_email = (
            resend_email
            or force_regenerate
            or assessment.candidate_feedback_sent_at is None
        )
        if should_send_email:
            role_title = (
                (assessment.role.name if assessment.role else None)
                or (assessment.task.name if assessment.task else None)
                or "technical assessment"
            )
            _dispatch_candidate_feedback_email(
                candidate_email=assessment.candidate.email,
                candidate_name=(assessment.candidate.full_name or assessment.candidate.email),
                org_name=(org.name if org and org.name else "your company"),
                role_title=role_title,
                feedback_link=feedback_link,
            )
            assessment.candidate_feedback_sent_at = utcnow()
            email_dispatched = True

    try:
        db.commit()
        db.refresh(assessment)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to finalize candidate feedback")

    return {
        "success": True,
        "feedback_ready": bool(assessment.candidate_feedback_ready),
        "feedback_generated_at": assessment.candidate_feedback_generated_at,
        "feedback_sent_at": assessment.candidate_feedback_sent_at,
        "feedback_url": feedback_link,
        "email_dispatched": email_dispatched,
        "feedback": generated_payload if include_feedback else None,
    }


@router.post("/{assessment_id}/interview-debrief")
def generate_interview_debrief(
    assessment_id: int,
    body: Dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payload = body or {}
    force_regenerate = bool(payload.get("force_regenerate", False))

    assessment = (
        db.query(Assessment)
        .options(
            joinedload(Assessment.candidate),
            joinedload(Assessment.task),
            joinedload(Assessment.role),
        )
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if not _is_completed(assessment):
        raise HTTPException(status_code=400, detail="Assessment must be completed before generating interview debrief")

    cached = (
        isinstance(getattr(assessment, "interview_debrief_json", None), dict)
        and not force_regenerate
    )
    if cached:
        return {
            "success": True,
            "cached": True,
            "generated_at": assessment.interview_debrief_generated_at,
            "interview_debrief": assessment.interview_debrief_json,
        }

    debrief = build_interview_debrief_payload(assessment)
    assessment.interview_debrief_json = debrief
    assessment.interview_debrief_generated_at = utcnow()

    try:
        db.commit()
        db.refresh(assessment)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to generate interview debrief")

    return {
        "success": True,
        "cached": False,
        "generated_at": assessment.interview_debrief_generated_at,
        "interview_debrief": debrief,
    }


@router.post("/{assessment_id}/ai-eval-suggestions")
def ai_eval_suggestions(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """V2 scaffold: AI suggests rubric scores/evidence; human reviewer decides final scores."""
    if not settings.AI_ASSISTED_EVAL_ENABLED:
        raise HTTPException(status_code=404, detail="AI-assisted evaluation is disabled")

    assessment = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    payload = {
        "evaluation_rubric": (assessment.task.evaluation_rubric if assessment.task else {}) or {},
        "chat_log": assessment.ai_prompts or [],
        "git_evidence": getattr(assessment, "git_evidence", {}) or {},
        "test_results": assessment.test_results or {},
    }
    try:
        return generate_ai_suggestions(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
