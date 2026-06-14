from __future__ import annotations

import re
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from ...deps import get_current_user
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate_application import CandidateApplication
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...services.ai_assisted_evaluator import generate_ai_suggestions
from ...services.candidate_feedback_engine import (
    build_client_assessment_report_payload,
    build_client_assessment_summary_pdf,
    build_interview_debrief_payload,
)
from ...services.evaluation_result_service import (
    build_evaluation_result,
    normalize_stored_evaluation_result,
)
from ...components.assessments.repository import utcnow

router = APIRouter()


def _is_completed(assessment: Assessment) -> bool:
    raw = getattr(assessment.status, "value", assessment.status)
    return str(raw or "").lower() in {
        AssessmentStatus.COMPLETED.value,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value,
    }


def _report_filename_part(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", str(value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned or fallback


@router.get("/{assessment_id}/report.pdf")
def download_assessment_report_pdf(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a client-facing PDF report without external dependencies."""
    assessment = (
        db.query(Assessment)
        .options(
            joinedload(Assessment.candidate),
            joinedload(Assessment.task),
            joinedload(Assessment.role),
            joinedload(Assessment.application),
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

    candidate_name = (assessment.candidate.full_name if assessment.candidate else None) or (
        assessment.candidate.email if assessment.candidate else "candidate"
    )
    organization_name = assessment.organization.name if getattr(assessment, "organization", None) else ""
    payload = build_client_assessment_report_payload(
        db,
        assessment,
        organization_name=organization_name,
    )
    final_pdf = build_client_assessment_summary_pdf(payload)
    role_name = (
        (assessment.role.name if getattr(assessment, "role", None) else None)
        or getattr(assessment, "role_name", None)
        or "Role"
    )
    filename = (
        f"{_report_filename_part(role_name, 'Role')}-"
        f"{_report_filename_part(candidate_name, 'Candidate')}.pdf"
    )
    return Response(
        content=final_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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

    # Optimistic lock: two recruiters editing the same evaluation must not
    # silently clobber each other. The client echoes the ``version`` it loaded;
    # if it no longer matches the stored one, 409 so it can reload & merge.
    stored_eval = assessment.manual_evaluation if isinstance(assessment.manual_evaluation, dict) else {}
    stored_version = int(stored_eval.get("version", 0) or 0)
    expected_version = body.get("expected_version")
    if expected_version is not None:
        try:
            expected_version_int = int(expected_version)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="expected_version must be an integer")
        if expected_version_int != stored_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This evaluation was updated by someone else. "
                    "Reload to see the latest before saving again."
                ),
            )

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

    if isinstance(evaluation_result, dict):
        evaluation_result["version"] = stored_version + 1
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
        "version": stored_version + 1,
    }


@router.post("/{assessment_id}/rescore")
def rescore_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-run automated scoring on a completed assessment.

    For when scoring hard-failed or only partially completed (e.g. a transient
    Anthropic error mid-submit, surfaced as ``scoring_failed``/``scoring_partial``).
    Reuses the same submit pipeline against the candidate's last submitted code;
    note it re-runs the task test runner, so the candidate's workspace/sandbox
    must still be reachable for the test counts to be meaningful.
    """
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
    if not _is_completed(assessment):
        raise HTTPException(
            status_code=409,
            detail="Only a completed assessment can be re-scored.",
        )

    # Recover the candidate's last submitted code from the stored snapshots.
    final_code = ""
    snapshots = assessment.code_snapshots if isinstance(assessment.code_snapshots, list) else []
    for snap in reversed(snapshots):
        if isinstance(snap, dict) and "final" in snap:
            final_code = snap.get("final") or ""
            break

    # Reset failure flags and re-open so the submit pipeline's atomic claim passes.
    assessment.scoring_failed = False
    assessment.scoring_partial = False
    assessment.status = AssessmentStatus.IN_PROGRESS
    db.commit()

    from ...components.assessments.service import submit_assessment as _submit_service

    try:
        _submit_service(assessment, final_code, int(assessment.tab_switch_count or 0), db)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        assessment.scoring_failed = True
        try:
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=502, detail=f"Rescore failed: {exc}") from exc

    db.refresh(assessment)
    return {
        "success": True,
        "assessment_id": int(assessment.id),
        "taali_score": getattr(assessment, "taali_score", None),
        "assessment_score": getattr(assessment, "assessment_score", None),
        "scoring_partial": bool(getattr(assessment, "scoring_partial", False)),
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
            joinedload(Assessment.application).joinedload(CandidateApplication.interviews),
            joinedload(Assessment.application).joinedload(CandidateApplication.organization),
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
