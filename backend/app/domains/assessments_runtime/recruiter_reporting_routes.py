from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from ...deps import get_current_user
from ...models.assessment import Assessment
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...services.ai_assisted_evaluator import generate_ai_suggestions
from ...services.evaluation_result_service import (
    build_evaluation_result,
    normalize_stored_evaluation_result,
)
from ...components.assessments.repository import utcnow

router = APIRouter()


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
    existing_timeline.append(
        {
            "time": utcnow().isoformat(),
            "event": "Recruiter note",
            "prompt": note,
        }
    )
    assessment.timeline = existing_timeline
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save note")
    return {"success": True, "timeline": assessment.timeline}


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
    return generate_ai_suggestions(payload)
