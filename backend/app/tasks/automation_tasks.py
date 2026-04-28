"""Automation Celery tasks fired on platform events.

These tasks fire automatically when something happens on the platform
(new role + JD attached, application created from any source, assessment
submitted, etc.) — they are NOT for CV scoring. CV match scoring lives in
``scoring_tasks`` and is triggered exclusively by recruiter actions
(Score / Rescore / Score selected).

Tasks here run on the default ``celery`` queue (taali-worker) so the
dedicated ``taali-worker-scoring`` service stays focused on recruiter-
triggered match work and a Workable sync that ingests N candidates can't
starve a recruiter who clicks "Score selected".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.automation_tasks.generate_role_interview_focus",
    bind=True,
    max_retries=0,
)
def generate_role_interview_focus(self, role_id: int) -> dict:
    """Regenerate the interview-focus packet for a role.

    Fired when a JD is attached or re-uploaded. Writes the focus blob and
    the screening / tech interview pack templates back onto the role row.
    Returns ``{"status": "..."}`` for log readability.
    """
    from ..models.role import Role
    from ..platform.config import settings
    from ..platform.database import SessionLocal
    from ..services.interview_focus_service import generate_interview_focus_sync
    from ..services.interview_support_service import build_role_interview_pack_templates

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            return {"status": "skipped", "reason": "role_not_found", "role_id": role_id}
        job_spec_text = (role.job_spec_text or "").strip()
        if not job_spec_text:
            return {"status": "skipped", "reason": "no_job_spec", "role_id": role_id}
        if not settings.ANTHROPIC_API_KEY:
            return {"status": "skipped", "reason": "no_api_key", "role_id": role_id}

        try:
            interview_focus = generate_interview_focus_sync(
                job_spec_text=job_spec_text,
                api_key=settings.ANTHROPIC_API_KEY,
                model=settings.resolved_claude_scoring_model,
                additional_requirements=(role.additional_requirements or "").strip() or None,
            )
        except Exception:
            logger.exception("generate_role_interview_focus failed role_id=%s", role_id)
            return {"status": "error", "role_id": role_id}

        if not interview_focus:
            return {"status": "no_output", "role_id": role_id}

        role.interview_focus = interview_focus
        role.interview_focus_generated_at = datetime.now(timezone.utc)
        templates = build_role_interview_pack_templates(role)
        role.screening_pack_template = templates.get("screening")
        role.tech_interview_pack_template = templates.get("tech_stage_2")
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("commit failed for generate_role_interview_focus role_id=%s", role_id)
            return {"status": "error_commit", "role_id": role_id}
        return {"status": "ok", "role_id": role_id}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.automation_tasks.generate_application_interview_pack",
    bind=True,
    max_retries=0,
)
def generate_application_interview_pack(self, application_id: int) -> dict:
    """Regenerate the screening + tech interview packs for an application.

    Fired when an application is created (any source) or its inputs change
    (CV uploaded, assessment submitted with new transcript, etc.). The
    packs are stored on the application row; pipeline list reads them
    from cache without making Claude calls per row.
    """
    from ..models.candidate_application import CandidateApplication
    from ..platform.database import SessionLocal
    from ..services.interview_support_service import refresh_application_interview_support

    db = SessionLocal()
    try:
        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .first()
        )
        if app is None:
            return {"status": "skipped", "reason": "not_found", "application_id": application_id}
        try:
            refresh_application_interview_support(
                app,
                organization=getattr(app, "organization", None),
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "generate_application_interview_pack failed application_id=%s", application_id
            )
            return {"status": "error", "application_id": application_id}
        return {"status": "ok", "application_id": application_id}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.automation_tasks.run_application_auto_reject",
    bind=True,
    max_retries=0,
)
def run_application_auto_reject(
    self, application_id: int, *, actor_type: str = "auto"
) -> dict:
    """Run the auto-reject pre-screen for an application.

    The pre-screen decides whether to disqualify the candidate when their
    score falls below the role's auto-reject threshold. Splitting this
    out of the Workable sync loop means the sync can ingest 100 candidates
    in one pass without holding a worker on N sequential pre-screens.
    """
    from ..domains.assessments_runtime.pipeline_service import append_application_event
    from ..models.candidate_application import CandidateApplication
    from ..models.organization import Organization
    from ..platform.database import SessionLocal
    from ..services.application_automation_service import run_auto_reject_if_needed

    db = SessionLocal()
    try:
        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .first()
        )
        if app is None:
            return {"status": "skipped", "reason": "not_found", "application_id": application_id}
        org = (
            db.query(Organization)
            .filter(Organization.id == app.organization_id)
            .first()
        )
        role = app.role
        try:
            result = run_auto_reject_if_needed(
                db=db,
                org=org,
                app=app,
                role=role,
                actor_type=actor_type,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "run_application_auto_reject failed application_id=%s", application_id
            )
            return {"status": "error", "application_id": application_id}

        if result.get("performed"):
            append_application_event(
                db,
                app=app,
                event_type="workable_auto_reject_applied",
                actor_type=actor_type,
                reason=str(result.get("reason") or "Auto reject applied"),
                metadata={
                    "pre_screen_score": (result.get("snapshot") or {}).get("pre_screen_score"),
                    "threshold_100": (result.get("config") or {}).get("threshold_100"),
                },
            )
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "commit failed for run_application_auto_reject application_id=%s",
                application_id,
            )
            return {"status": "error_commit", "application_id": application_id}
        return {
            "status": "ok",
            "application_id": application_id,
            "performed": bool(result.get("performed")),
        }
    finally:
        db.close()
