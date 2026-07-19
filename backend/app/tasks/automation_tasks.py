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
from datetime import datetime, timedelta, timezone

from .celery_app import celery_app

logger = logging.getLogger(__name__)


def _automatic_role_work_block_reason(db, role) -> str | None:
    """Return why queued autonomous role work must not start now.

    A role can be paused, turned off, locally closed, or closed in its linked
    ATS after a broker accepts a task but before the worker begins provider
    work. Re-authorize at execution time so those transitions stop *new* model
    spend; an already-started provider request may still settle normally.
    """

    from ..services.role_execution_guard import automatic_role_action_block_reason

    return automatic_role_action_block_reason(role, db=db)


def _set_activation_focus_state(
    role, *, status: str, error: str | None = None, retry_after: timedelta | None = None
) -> None:
    """Update the activation-owned interview-focus recovery marker."""
    provisioning = (
        dict(role.assessment_task_provisioning)
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    now = datetime.now(timezone.utc)
    state = (
        dict(provisioning.get("interview_focus_provisioning"))
        if isinstance(provisioning.get("interview_focus_provisioning"), dict)
        else {}
    )
    state.update(
        {
            "status": str(status),
            "last_error": str(error)[:2000] if error else None,
            "next_attempt_at": (
                (now + retry_after).isoformat() if retry_after else None
            ),
            "updated_at": now.isoformat(),
        }
    )
    provisioning["interview_focus_provisioning"] = state
    role.assessment_task_provisioning = provisioning


def _set_activation_tech_state(
    role, *, status: str, error: str | None = None, retry_after: timedelta | None = None
) -> None:
    provisioning = (
        dict(role.assessment_task_provisioning)
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    now = datetime.now(timezone.utc)
    state = (
        dict(provisioning.get("tech_questions_provisioning"))
        if isinstance(provisioning.get("tech_questions_provisioning"), dict)
        else {}
    )
    state.update(
        {
            "status": str(status),
            "last_error": str(error)[:2000] if error else None,
            "next_attempt_at": (
                (now + retry_after).isoformat() if retry_after else None
            ),
            "updated_at": now.isoformat(),
        }
    )
    provisioning["tech_questions_provisioning"] = state
    role.assessment_task_provisioning = provisioning


@celery_app.task(
    name="app.tasks.automation_tasks.regenerate_role_tech_questions",
    bind=True,
    max_retries=0,
)
def regenerate_role_tech_questions(self, role_id: int) -> dict:
    """Refresh the role-level cached tech screening questions.

    Dispatched from ``mark_role_scores_stale`` after a job-spec or
    criteria change. Async because the LLM call adds ~2-3s of latency
    and we don't want to slow down the recruiter's PATCH /roles or the
    chip-CRUD endpoints by that much.

    Idempotent: ``get_or_regenerate`` checks the signature and skips the
    LLM call if nothing changed since the previous run, so a burst of
    chip edits collapses to one effective regen once they settle.
    """
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.provider_usage_admission import serialize_provider_work
    from ..services.role_tech_questions_service import get_or_regenerate

    db = SessionLocal()
    try:
        serialize_provider_work(
            db,
            scope="role_tech_questions",
            entity_id=int(role_id),
        )
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            return {"status": "skipped", "reason": "role_not_found", "role_id": role_id}
        role_block = _automatic_role_work_block_reason(db, role)
        if role_block:
            return {
                "status": "skipped",
                "reason": "role_not_runnable",
                "detail": role_block,
                "role_id": role_id,
            }
        try:
            result = get_or_regenerate(db, role)
        except Exception:
            logger.exception(
                "regenerate_role_tech_questions failed role_id=%s", role_id
            )
            db.rollback()
            role = db.query(Role).filter(Role.id == role_id).first()
            if role is not None:
                _set_activation_tech_state(
                    role,
                    status="retry_wait",
                    error="tech_question_generation_failed",
                    retry_after=timedelta(minutes=5),
                )
                db.commit()
            return {"status": "error", "role_id": role_id}
        if role.tech_questions_signature:
            _set_activation_tech_state(role, status="succeeded")
        else:
            _set_activation_tech_state(
                role,
                status="retry_wait",
                error="tech-question generation did not produce a current cache",
                retry_after=timedelta(minutes=5),
            )
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("regenerate_role_tech_questions commit failed role_id=%s", role_id)
            return {"status": "error", "role_id": role_id}
        return {
            "status": "ok",
            "role_id": role_id,
            "questions_count": len(result) if isinstance(result, list) else 0,
        }
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.automation_tasks.generate_role_interview_focus",
    bind=True,
    max_retries=0,
)
def generate_role_interview_focus(
    self, role_id: int, *, requires_running_agent: bool = False
) -> dict:
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
    from ..services.provider_usage_admission import serialize_provider_work
    from ..services.role_criteria_service import render_role_intent_block

    db = SessionLocal()
    try:
        serialize_provider_work(
            db,
            scope="role_interview_focus",
            entity_id=int(role_id),
        )
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None:
            return {"status": "skipped", "reason": "role_not_found", "role_id": role_id}
        from ..services.workspace_agent_control import workspace_agent_is_paused

        if workspace_agent_is_paused(
            db,
            organization_id=int(role.organization_id),
        ):
            return {
                "status": "skipped",
                "reason": "workspace_paused",
                "role_id": role_id,
            }
        if requires_running_agent:
            role_block = _automatic_role_work_block_reason(db, role)
            if role_block:
                return {
                    "status": "skipped",
                    "reason": "role_not_runnable",
                    "detail": role_block,
                    "role_id": role_id,
                }
        job_spec_text = (role.job_spec_text or "").strip()
        if not job_spec_text:
            return {"status": "skipped", "reason": "no_job_spec", "role_id": role_id}
        # Publish/spec-update paths clear this cache before dispatch. A duplicate
        # delivery after the first worker committed is therefore a true no-op,
        # not a second paid generation.
        if isinstance(role.interview_focus, dict) and role.interview_focus:
            _set_activation_focus_state(role, status="succeeded")
            db.commit()
            return {"status": "skipped", "reason": "already_generated", "role_id": role_id}
        if not settings.ANTHROPIC_API_KEY:
            _set_activation_focus_state(
                role,
                status="retry_wait",
                error="ANTHROPIC_API_KEY is not configured",
                retry_after=timedelta(hours=1),
            )
            db.commit()
            return {"status": "skipped", "reason": "no_api_key", "role_id": role_id}

        try:
            interview_focus = generate_interview_focus_sync(
                job_spec_text=job_spec_text,
                api_key=settings.ANTHROPIC_API_KEY,
                model=settings.resolved_claude_scoring_model,
                additional_requirements=render_role_intent_block(role) or None,
                metering={
                    "feature": "interview_focus",
                    "organization_id": getattr(role, "organization_id", None),
                    "role_id": int(role.id),
                    "entity_id": f"role:{role.id}",
                    "trace_id": f"interview-focus:role:{role.id}",
                    "db": db,
                },
            )
        except Exception:
            logger.exception("generate_role_interview_focus failed role_id=%s", role_id)
            db.rollback()
            role = db.query(Role).filter(Role.id == role_id).first()
            if role is not None:
                _set_activation_focus_state(
                    role,
                    status="retry_wait",
                    error="interview_focus_generation_failed",
                    retry_after=timedelta(minutes=5),
                )
                db.commit()
            return {"status": "error", "role_id": role_id}

        if not interview_focus:
            _set_activation_focus_state(
                role,
                status="retry_wait",
                error="provider returned no interview focus",
                retry_after=timedelta(minutes=5),
            )
            db.commit()
            return {"status": "no_output", "role_id": role_id}

        role.interview_focus = interview_focus
        role.interview_focus_generated_at = datetime.now(timezone.utc)
        templates = build_role_interview_pack_templates(role)
        role.screening_pack_template = templates.get("screening")
        role.tech_interview_pack_template = templates.get("tech_stage_2")
        _set_activation_focus_state(role, status="succeeded")
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
        # This row lock spans only deterministic assembly from persisted role,
        # CV, assessment, and cached role-question data. The refresh helper
        # performs no provider/LLM calls, so serialization prevents stale pack
        # overwrites without holding a hot row across network latency or spend.
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == application_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .populate_existing()
            .with_for_update(of=CandidateApplication)
            .one_or_none()
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
    import hashlib
    import json

    from ..models.candidate_application import CandidateApplication
    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.workable_op_runner import (
        OP_AUTO_REJECT,
        enqueue_workable_op,
    )

    db = SessionLocal()
    try:
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == application_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .one_or_none()
        )
        if app is None:
            return {"status": "skipped", "reason": "not_found", "application_id": application_id}
        # HARD GUARD: a `sourced` prospect is pre-applied — un-scored and never in
        # the decision queue. Skip auto-reject entirely (the decision-creation
        # emitters also refuse a sourced app; this avoids the wasted evaluation).
        if (app.pipeline_stage or "").strip().lower() == "sourced":
            return {"status": "skipped", "reason": "sourced_prospect", "application_id": application_id}
        if str(app.application_outcome or "open").strip().lower() != "open":
            return {
                "status": "skipped",
                "reason": "application_closed",
                "application_id": application_id,
            }
        org = db.get(Organization, int(app.organization_id))
        role = db.get(Role, int(app.role_id)) if app.role_id is not None else None
        # Collapse duplicate broker deliveries for one exact policy/input
        # snapshot while allowing a later pre-screen or role-policy revision to
        # enqueue fresh work. Only a SHA-256 digest is persisted in the public
        # dispatch receipt; provider credentials/config values never leave the
        # encrypted operation payload rail.
        signature_payload = {
            "application_id": int(app.id),
            "application_version": int(app.version or 1),
            "pre_screen_run_at": (
                app.pre_screen_run_at.isoformat() if app.pre_screen_run_at else None
            ),
            "genuine_pre_screen_score": app.genuine_pre_screen_score_100,
            "cv_match_score": app.cv_match_score,
            "pre_screen_decision": (
                (app.pre_screen_evidence or {}).get("decision")
                if isinstance(app.pre_screen_evidence, dict)
                else None
            ),
            "role_version": int(role.version or 1) if role is not None else None,
            "agent_enabled": bool(role.agentic_mode_enabled) if role is not None else False,
            "role_paused_at": (
                role.agent_paused_at.isoformat()
                if role is not None and role.agent_paused_at
                else None
            ),
            "auto_reject": bool(role.auto_reject) if role is not None else False,
            "auto_reject_pre_screen": (
                bool(role.auto_reject_pre_screen) if role is not None else False
            ),
            "score_threshold": role.score_threshold if role is not None else None,
            "workable_candidate_id": app.workable_candidate_id,
            "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
            "workspace_control_version": (
                int(org.agent_workspace_control_version or 1)
                if org is not None
                else None
            ),
        }
        signature = hashlib.sha256(
            json.dumps(
                signature_payload,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        receipt_key = f"auto-reject:{int(app.id)}:{signature[:40]}"
        try:
            job_run_id = enqueue_workable_op(
                organization_id=int(app.organization_id),
                op_type=OP_AUTO_REJECT,
                payload={
                    "application_id": int(app.id),
                    "actor_type": str(actor_type or "auto")[:32],
                    "receipt_key": receipt_key,
                },
                scope_id=int(app.id),
                dispatch_key=receipt_key,
            )
        except Exception:
            logger.exception(
                "run_application_auto_reject enqueue failed application_id=%s",
                application_id,
            )
            return {
                "status": "error_enqueue",
                "application_id": application_id,
            }
        return {
            "status": "queued",
            "application_id": application_id,
            "job_run_id": int(job_run_id),
            "receipt_key": receipt_key,
        }
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.automation_tasks.parse_application_cv_sections",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=600,
    time_limit=720,
)
def parse_application_cv_sections(
    self,
    application_id: int,
    *,
    force: bool = False,
    origin: str | None = None,
    outbox_id: int | None = None,
) -> dict:
    """Parse an application's stored CV text into structured ``cv_sections``.

    Enqueued by ``on_application_created`` whenever an application is
    ingested with raw ``cv_text`` but no parsed sections — the dominant
    case is the Workable bulk sync, which stores the extracted text but (by
    design) makes no synchronous Claude call in the sync loop. Running the
    parse here keeps the candidate page's Skills / Experience / Education
    blocks structured instead of falling back to a naive split-by-heading
    render of the raw (often column-scrambled) PDF text.

    ``origin`` is required execution authority. Autonomous ATS/native work
    fresh-checks that the linked role agent is enabled and unpaused before any
    provider call. Explicit recruiter upload is allowed while the agent is
    held. Missing/unknown legacy messages fail closed.

    Idempotent: no-ops when ``cv_sections`` is already populated (unless
    ``force``). Retries a few times when the row or its ``cv_text`` isn't
    visible yet — the enqueue can race the sync transaction's commit.
    """
    from sqlalchemy.orm import joinedload

    from ..cv_parsing.apply import parse_and_store_cv_sections
    from ..cv_parsing.origins import (
        AUTONOMOUS_CV_PARSE_ORIGINS,
        normalize_cv_parse_origin,
    )
    from ..models.candidate_application import CandidateApplication
    from ..platform.database import SessionLocal

    normalized_origin = normalize_cv_parse_origin(origin)
    if normalized_origin is None:
        return {
            "status": "skipped",
            "reason": "unknown_origin",
            "application_id": application_id,
        }

    db = SessionLocal()
    try:
        app = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.role),
            )
            .filter(CandidateApplication.id == application_id)
            .first()
        )
        durable_outbox_id = None
        if normalized_origin == "ats_ingest":
            from ..services.ats_cv_parse_outbox import resolve_cv_parse_outbox_id

            durable_outbox_id = resolve_cv_parse_outbox_id(
                db,
                application_id=int(application_id),
                outbox_id=int(outbox_id) if outbox_id is not None else None,
            )
        # The enqueue can land before the ingest transaction commits, so a
        # missing row / missing text is "not yet" rather than "never" —
        # retry a few times before giving up.
        if app is None:
            if durable_outbox_id is not None:
                from ..services.ats_cv_parse_outbox import record_cv_parse_failure

                status = record_cv_parse_failure(
                    db,
                    outbox_id=durable_outbox_id,
                    error="Application is unavailable",
                    terminal=True,
                )
                return {"status": status, "application_id": application_id}
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=30)
            return {"status": "skipped", "reason": "not_found", "application_id": application_id}

        if app.deleted_at is not None:
            if durable_outbox_id is not None:
                from ..services.ats_cv_parse_outbox import record_cv_parse_failure

                record_cv_parse_failure(
                    db,
                    outbox_id=durable_outbox_id,
                    error="Application was deleted",
                    terminal=True,
                )
            return {
                "status": "skipped",
                "reason": "application_deleted",
                "application_id": application_id,
            }

        if normalized_origin in AUTONOMOUS_CV_PARSE_ORIGINS:
            role = app.role
            role_block = (
                "role is unavailable"
                if role is None or getattr(role, "deleted_at", None) is not None
                else _automatic_role_work_block_reason(db, role)
            )
            if role_block:
                if durable_outbox_id is not None:
                    from ..services.ats_cv_parse_outbox import (
                        record_cv_parse_authority_blocked,
                    )

                    record_cv_parse_authority_blocked(
                        db,
                        outbox_id=durable_outbox_id,
                        reason=role_block,
                    )
                return {
                    "status": "skipped",
                    "reason": "role_not_runnable",
                    "detail": role_block,
                    "application_id": application_id,
                }

        if app.cv_sections is not None and not force:
            if durable_outbox_id is not None:
                from ..services.ats_cv_parse_outbox import record_cv_parse_success

                record_cv_parse_success(db, outbox_id=durable_outbox_id)
            return {"status": "skipped", "reason": "already_parsed", "application_id": application_id}

        if not (app.cv_text or "").strip() and not ((app.candidate.cv_text if app.candidate else "") or "").strip():
            if durable_outbox_id is not None:
                from ..services.ats_cv_parse_outbox import record_cv_parse_missing_text

                status = record_cv_parse_missing_text(
                    db, outbox_id=durable_outbox_id
                )
                return {"status": status, "application_id": application_id}
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=30)
            return {"status": "skipped", "reason": "no_cv_text", "application_id": application_id}

        if durable_outbox_id is not None:
            from ..services.ats_cv_parse_outbox import claim_cv_parse_attempt

            claim = claim_cv_parse_attempt(
                db,
                application_id=int(application_id),
                outbox_id=durable_outbox_id,
            )
            if not claim.get("claimed"):
                return {
                    "status": "skipped",
                    "reason": claim.get("reason"),
                    "application_id": application_id,
                }

        wrote = parse_and_store_cv_sections(app, db=db, force=force)
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error(
                "CV parse commit failed application_id=%s error_code=result_commit_failed error_type=%s",
                application_id,
                type(exc).__name__,
            )
            if durable_outbox_id is not None:
                from ..services.ats_cv_parse_outbox import record_cv_parse_failure

                record_cv_parse_failure(
                    db,
                    outbox_id=durable_outbox_id,
                    error="CV parse result commit failed",
                )
            return {"status": "error", "application_id": application_id}
        if durable_outbox_id is not None:
            from ..services.ats_cv_parse_outbox import (
                cached_failure_for_application,
                record_cv_parse_failure,
                record_cv_parse_success,
            )

            if wrote:
                durable_status = record_cv_parse_success(
                    db, outbox_id=durable_outbox_id
                )
            else:
                error, terminal = cached_failure_for_application(app)
                durable_status = record_cv_parse_failure(
                    db,
                    outbox_id=durable_outbox_id,
                    error=error,
                    terminal=terminal,
                )
            return {
                "status": durable_status,
                "application_id": application_id,
            }
        return {
            "status": "ok" if wrote else "no_sections",
            "application_id": application_id,
        }
    finally:
        db.close()
