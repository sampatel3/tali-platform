import logging
from .celery_app import celery_app
from ..platform.config import settings

logger = logging.getLogger(__name__)


# Email tasks (send_assessment_email / send_results_email) live in
# app.components.notifications.tasks. Do NOT re-export them here: that module
# imports celery_app from this package, so a top-level back-import creates a
# circular import that breaks request-time email dispatch (the importer hits
# a partially-initialized notifications.tasks). Import them from the canonical
# module instead. (There is no candidate feedback-ready email — Taali does not
# email candidates about feedback; see the taali-no-candidate-job-emails policy.)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=120)
def post_results_to_workable(self, access_token: str, subdomain: str, candidate_id: str, assessment_data: dict, member_id: str | None = None, request_id: str | None = None):
    """Post assessment results to Workable candidate profile."""
    from ..domains.integrations_notifications.adapters import build_workable_adapter

    if not (member_id or "").strip():
        logger.info(
            "Skipping Workable result post for candidate %s — no actor member configured",
            candidate_id,
            extra={"request_id": request_id or self.request.id},
        )
        return {"success": False, "skipped": True}

    try:
        workable_svc = build_workable_adapter(access_token=access_token, subdomain=subdomain)
        result = workable_svc.post_assessment_result(candidate_id=candidate_id, member_id=member_id, assessment_data=assessment_data)
        if not result["success"]:
            raise Exception(result.get("error", "Workable post failed"))
        logger.info(f"Results posted to Workable for candidate {candidate_id}", extra={"request_id": request_id or self.request.id})
        return result
    except Exception as exc:
        logger.error(f"Failed to post to Workable: {exc}", extra={"request_id": request_id or self.request.id})
        raise self.retry(exc=exc)


@celery_app.task
def cleanup_expired_assessments():
    """Periodic hygiene: expire PENDING assessments whose invite window lapsed.

    IN_PROGRESS assessments are deliberately NOT touched here. A candidate who
    starts then walks away is captured + SCORED by ``finalize_timed_out_assessments``
    (server-side timer enforcement) so their effort is never discarded. This task
    used to mark stale IN_PROGRESS rows EXPIRED and throw the work away — e.g. a
    candidate who coded for 72 minutes showed up to the recruiter as a blank
    "expired" with no result. E2B sandboxes auto-expire on their own, so there is
    nothing to reap here for IN_PROGRESS rows.
    """
    from datetime import datetime, timezone
    from sqlalchemy.orm import Session
    from ..platform.database import SessionLocal
    from ..models.assessment import Assessment, AssessmentStatus

    logger.info("Running expired assessment cleanup")
    db: Session = SessionLocal()
    try:
        expired = db.query(Assessment).filter(
            Assessment.status == AssessmentStatus.PENDING,
            Assessment.expires_at < datetime.now(timezone.utc),
        ).all()

        count = 0
        for assessment in expired:
            assessment.status = AssessmentStatus.EXPIRED
            count += 1

        db.commit()
        logger.info(f"Cleaned up {count} expired pending assessments")
    except Exception as e:
        logger.error(f"Cleanup task failed: {e}")
        db.rollback()
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=1, default_retry_delay=300)
def repair_generated_task_after_battle_failure(
    self, task_id: int, organization_id: int
):
    """Metered, bounded in-place re-authoring from battle-test feedback.

    At most two model-backed repair attempts are allowed across the lifetime of
    a generated draft. Successful repairs preserve the Task id/role link, reset
    a durable battle-test intent, and immediately re-test. Exhaustion is the
    genuine HITL boundary (the recruiter can inspect/skip the assessment).
    """
    import uuid
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import Session

    from ..models.role import Role, role_tasks
    from ..models.task import Task
    from ..platform.config import settings
    from ..platform.database import SessionLocal
    from ..services.task_battle_test import (
        BATTLE_TEST_MAX_REPAIR_ATTEMPTS,
        BATTLE_TEST_REPAIR_EXHAUSTED,
        BATTLE_TEST_REPAIR_FAILED,
        BATTLE_TEST_REPAIR_RETRY_WAIT,
        BATTLE_TEST_REPAIRING,
        apply_battle_test_repair,
        battle_test_provisioning_action,
        battle_test_repair_feedback,
        reconstruct_generated_task_spec,
    )
    from ..services.task_provisioning_service import (
        _provision_repo_best_effort,
        _role_jd_text,
        _slugify,
    )
    from ..services.task_spec_generator import revise_task_spec

    db: Session = SessionLocal()
    claim_token: str | None = None
    model_attempts = 0
    try:
        from ..services.workspace_agent_control import (
            workspace_agent_control_snapshot,
        )

        workspace_paused, _workspace_control_version = (
            workspace_agent_control_snapshot(
                db,
                organization_id=int(organization_id),
                lock=True,
            )
        )
        if workspace_paused:
            db.rollback()
            return {"status": "deferred", "reason": "workspace_paused"}

        task = (
            db.query(Task)
            .filter(Task.id == int(task_id), Task.organization_id == int(organization_id))
            .with_for_update()
            .one_or_none()
        )
        if task is None:
            return {"status": "skipped", "reason": "task_not_found"}
        if battle_test_provisioning_action(task) != "repair":
            state = (
                task.extra_data.get("battle_test_provisioning", {})
                if isinstance(task.extra_data, dict)
                else {}
            )
            return {"status": "noop", "reason": state.get("status") or "not_due"}

        extra = dict(task.extra_data) if isinstance(task.extra_data, dict) else {}
        state = (
            dict(extra.get("battle_test_provisioning"))
            if isinstance(extra.get("battle_test_provisioning"), dict)
            else {}
        )
        model_attempts = int(state.get("repair_attempts") or 0)
        if model_attempts >= BATTLE_TEST_MAX_REPAIR_ATTEMPTS:
            state["status"] = BATTLE_TEST_REPAIR_EXHAUSTED
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            extra["battle_test_provisioning"] = state
            task.extra_data = extra
            db.commit()
            return {"status": "repair_exhausted", "repair_attempts": model_attempts}

        claim_token = uuid.uuid4().hex
        state.update(
            {
                "status": BATTLE_TEST_REPAIRING,
                "claim_token": claim_token,
                "last_error": None,
                "next_attempt_at": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        extra["battle_test_provisioning"] = state
        task.extra_data = extra
        db.commit()

        api_key = str(getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not configured for automated task repair"
            )

        role_id = db.execute(
            role_tasks.select()
            .with_only_columns(role_tasks.c.role_id)
            .where(role_tasks.c.task_id == int(task_id))
            .limit(1)
        ).scalar_one_or_none()
        if role_id is None:
            raise RuntimeError("generated task is no longer linked to its role")
        role = (
            db.query(Role)
            .filter(
                Role.id == int(role_id),
                Role.organization_id == int(organization_id),
            )
            .one_or_none()
        )
        if role is None:
            raise RuntimeError("generated task is no longer linked to its role")

        # Count immediately before the metered generator call. Configuration
        # failures consume no repair budget; every provider-backed re-author
        # attempt does, whether or not it returns a valid spec.
        task = (
            db.query(Task)
            .filter(Task.id == int(task_id), Task.organization_id == int(organization_id))
            .with_for_update()
            .populate_existing()
            .one()
        )
        extra = dict(task.extra_data) if isinstance(task.extra_data, dict) else {}
        state = dict(extra.get("battle_test_provisioning") or {})
        if str(state.get("claim_token") or "") != claim_token:
            db.rollback()
            return {"status": "superseded"}
        model_attempts = int(state.get("repair_attempts") or 0) + 1
        state["repair_attempts"] = model_attempts
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        extra["battle_test_provisioning"] = state
        task.extra_data = extra
        db.commit()
        db.refresh(task)

        failed_report = (
            dict(task.extra_data.get("battle_test"))
            if isinstance(task.extra_data, dict)
            and isinstance(task.extra_data.get("battle_test"), dict)
            else {}
        )
        feedback = battle_test_repair_feedback(failed_report)
        result = revise_task_spec(
            prior_spec=reconstruct_generated_task_spec(task),
            feedback=feedback,
            role_name=str(role.name or "Role"),
            role_slug=_slugify(str(role.name or "Role")),
            jd_text=_role_jd_text(role),
            api_key=api_key,
            organization_id=int(organization_id),
            role_id=int(role.id),
            # Global repair budget is two provider calls across task retries;
            # keep the generator's inner validation loop to one call here.
            max_attempts=1,
        )
        if not result.valid or not result.spec:
            errors = "; ".join(result.errors[:3]) or "invalid repaired spec"
            no_provider_call = any(
                marker in error.lower()
                for error in result.errors
                for marker in (
                    "insufficient usage credits",
                    "insufficient role monthly budget",
                    "usage reservation failed",
                )
            )
            if no_provider_call:
                # Reservation/configuration failures did not spend or invoke a
                # model, so do not burn one of the two content-repair attempts.
                task = (
                    db.query(Task)
                    .filter(
                        Task.id == int(task_id),
                        Task.organization_id == int(organization_id),
                    )
                    .with_for_update()
                    .populate_existing()
                    .one()
                )
                extra = dict(task.extra_data) if isinstance(task.extra_data, dict) else {}
                state = dict(extra.get("battle_test_provisioning") or {})
                if str(state.get("claim_token") or "") == claim_token:
                    model_attempts = max(0, model_attempts - 1)
                    state["repair_attempts"] = model_attempts
                    extra["battle_test_provisioning"] = state
                    task.extra_data = extra
                    db.commit()
            raise RuntimeError(f"automated task repair was invalid: {errors}")

        task = (
            db.query(Task)
            .filter(Task.id == int(task_id), Task.organization_id == int(organization_id))
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if task is None:
            return {"status": "superseded", "reason": "task_removed"}
        state = (
            dict(task.extra_data.get("battle_test_provisioning"))
            if isinstance(task.extra_data, dict)
            and isinstance(task.extra_data.get("battle_test_provisioning"), dict)
            else {}
        )
        if str(state.get("claim_token") or "") != claim_token:
            db.rollback()
            return {"status": "superseded"}
        apply_battle_test_repair(
            task,
            result.spec,
            feedback=feedback,
            failed_report=failed_report,
            repair_attempts=model_attempts,
        )
        db.commit()
        db.refresh(task)
        _provision_repo_best_effort(task)
        try:
            battle_test_generated_task.delay(int(task.id), int(organization_id))
        except Exception:
            logger.exception(
                "repaired-task battle-test kick failed task=%s; sweep will recover",
                task.id,
            )
        return {
            "status": "repaired",
            "task_id": int(task.id),
            "repair_attempts": model_attempts,
        }
    except Exception as exc:
        db.rollback()
        retries = int(getattr(self.request, "retries", 0) or 0)
        max_retries = int(self.max_retries or 0)
        countdown = 300
        exhausted = model_attempts >= BATTLE_TEST_MAX_REPAIR_ATTEMPTS
        if claim_token:
            task = (
                db.query(Task)
                .filter(Task.id == int(task_id), Task.organization_id == int(organization_id))
                .with_for_update()
                .one_or_none()
            )
            if task is not None:
                extra = dict(task.extra_data) if isinstance(task.extra_data, dict) else {}
                state = dict(extra.get("battle_test_provisioning") or {})
                if str(state.get("claim_token") or "") == claim_token:
                    if exhausted:
                        status = BATTLE_TEST_REPAIR_EXHAUSTED
                        next_attempt_at = None
                    elif retries < max_retries:
                        status = BATTLE_TEST_REPAIR_RETRY_WAIT
                        next_attempt_at = datetime.now(timezone.utc) + timedelta(
                            seconds=countdown
                        )
                    else:
                        status = BATTLE_TEST_REPAIR_FAILED
                        next_attempt_at = datetime.now(timezone.utc) + timedelta(hours=1)
                    state.update(
                        {
                            "status": status,
                            "last_error": "assessment_task_repair_failed",
                            "next_attempt_at": (
                                next_attempt_at.isoformat() if next_attempt_at else None
                            ),
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    extra["battle_test_provisioning"] = state
                    task.extra_data = extra
                    db.commit()
        if exhausted:
            logger.error(
                "automated battle-test repair exhausted task=%s attempts=%s: %s",
                task_id,
                model_attempts,
                exc,
            )
            return {
                "status": "repair_exhausted",
                "task_id": int(task_id),
                "repair_attempts": model_attempts,
                "reason": "assessment_task_repair_failed",
            }
        if retries < max_retries:
            logger.warning(
                "automated task repair retry task=%s retry=%s/%s: %s",
                task_id,
                retries + 1,
                max_retries,
                exc,
            )
            raise self.retry(exc=exc, countdown=countdown)
        logger.exception("automated task repair failed task=%s", task_id)
        return {
            "status": "repair_failed",
            "task_id": int(task_id),
            "reason": "assessment_task_repair_failed",
            "retry_after_seconds": 3600,
        }
    finally:
        db.close()


@celery_app.task
def finalize_timed_out_assessments(limit: int = 25):
    """Server-side timer enforcement: capture + score IN_PROGRESS assessments
    whose working timer has expired but whose candidate never submitted.

    The in-app ``enforce_active_or_timeout`` gate is pull-based — it only fires on
    a candidate request, so a candidate who works then closes the tab is never
    finalized. Without this sweep their effort is lost (the row lingers IN_PROGRESS
    and ``cleanup_expired_assessments`` used to discard it). Here we finalize each
    timed-out row through the real submit pipeline so it reaches the recruiter as a
    COMPLETED_DUE_TO_TIMEOUT result and wakes the enabled role agent after commit.
    Anthropic/E2B-heavy → routed to the ``scoring`` queue (see ``_TASK_ROUTES``).
    ``limit`` bounds per-tick work.
    """
    from sqlalchemy.orm import Session
    from ..platform.database import SessionLocal
    from ..models.assessment import Assessment, AssessmentStatus
    from ..components.assessments.repository import time_remaining_seconds
    from ..components.assessments.service import finalize_timed_out_assessment

    db: Session = SessionLocal()
    finalized = 0
    scoring_failed = 0
    skipped = 0
    try:
        rows = (
            db.query(Assessment)
            .filter(
                Assessment.status == AssessmentStatus.IN_PROGRESS,
                Assessment.is_voided.is_(False),
                Assessment.is_demo.is_(False),
                Assessment.started_at.isnot(None),
            )
            .order_by(Assessment.started_at.asc())
            .limit(limit)
            .all()
        )
        for assessment in rows:
            # Timer math (pause-aware) lives in repository.time_remaining_seconds;
            # only finalize rows whose working time is genuinely exhausted. A paused
            # assessment keeps time on the clock and is left alone.
            if time_remaining_seconds(assessment) > 0:
                skipped += 1
                continue
            try:
                result = finalize_timed_out_assessment(assessment, db)
            except Exception:
                logger.exception(
                    "finalize_timed_out_assessments: crash assessment_id=%s", assessment.id
                )
                db.rollback()
                scoring_failed += 1
                continue
            if result.get("status") == "finalized":
                finalized += 1
                if result.get("scoring_failed"):
                    scoring_failed += 1
        logger.info(
            "Timed-out assessment finalize sweep: finalized=%d scoring_failed=%d skipped=%d",
            finalized, scoring_failed, skipped,
        )
        return {"finalized": finalized, "scoring_failed": scoring_failed, "skipped": skipped}
    finally:
        db.close()


@celery_app.task
def assessment_provisioning_healthcheck():
    """Proactively verify the GitHub credential that assessment repo provisioning
    depends on.

    A 401 from GitHub silently blocks EVERY candidate from starting an assessment
    — repo provisioning runs at both send and start, so an expired GITHUB_TOKEN
    surfaces to candidates only as "Failed to initialize assessment repository"
    and takes the funnel to zero with no other signal (the 2026-06-25 incident:
    the token expired ~5 days before anyone noticed). This beat alerts loudly
    (structured log + Sentry) so it can't recur silently.
    """
    from ..services.github_credentials import verify_github_credentials

    result = verify_github_credentials(org=settings.GITHUB_ORG, token=settings.GITHUB_TOKEN)
    if not result.get("ok"):
        logger.error(
            "assessment_provisioning_unhealthy: GitHub credential check failed "
            "(status=%s org=%s) — candidates cannot start assessments until "
            "GITHUB_TOKEN is rotated. detail=%s",
            result.get("status_code"), settings.GITHUB_ORG, result.get("detail"),
            extra={"event": "assessment_provisioning_unhealthy", "check": result},
        )
        try:  # surface to Sentry if configured (main.py inits it)
            import sentry_sdk

            sentry_sdk.capture_message(
                f"Assessment provisioning DOWN: GitHub returned "
                f"{result.get('status_code')} on org {settings.GITHUB_ORG} — "
                f"GITHUB_TOKEN is invalid/expired; no candidate can start an "
                f"assessment until it is rotated on all services.",
                level="error",
            )
        except Exception:  # pragma: no cover — never let alerting break the run
            pass
    else:
        logger.info(
            "assessment_provisioning_healthcheck ok (org=%s mock=%s)",
            settings.GITHUB_ORG, result.get("mock", False),
        )
    return result


@celery_app.task
def send_assessment_expiry_reminders():
    """Daily reminder: notify candidates whose pending assessments expire soon."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy.orm import Session, joinedload

    from ..domains.integrations_notifications.adapters import build_email_adapter
    from ..models.assessment import Assessment, AssessmentStatus
    from ..platform.database import SessionLocal

    if not (settings.RESEND_API_KEY or "").strip():
        return {"status": "skipped", "reason": "resend_not_configured"}

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=6)
    window_end = now - timedelta(days=5)

    db: Session = SessionLocal()
    sent = 0
    failed = 0
    skipped = 0
    try:
        pending = (
            db.query(Assessment)
            .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
            .filter(
                Assessment.status == AssessmentStatus.PENDING,
                Assessment.created_at > window_start,
                Assessment.created_at <= window_end,
                Assessment.expires_at != None,  # noqa: E711
                Assessment.expires_at > now,
            )
            .all()
        )
        email_svc = build_email_adapter()
        for assessment in pending:
            candidate_email = (
                (assessment.candidate.email if assessment.candidate else None)
                or None
            )
            if not candidate_email:
                skipped += 1
                continue
            candidate_name = (
                (assessment.candidate.full_name if assessment.candidate else None)
                or candidate_email
            )
            task_name = (assessment.task.name if assessment.task else None) or "Technical assessment"
            expiry_text = assessment.expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            assessment_link = f"{settings.FRONTEND_URL}/assessment/{assessment.id}?token={assessment.token}"
            result = email_svc.send_assessment_expiry_reminder(
                candidate_email=candidate_email,
                candidate_name=candidate_name,
                task_name=task_name,
                assessment_link=assessment_link,
                expiry_text=expiry_text,
            )
            if result.get("success"):
                sent += 1
            else:
                failed += 1
        logger.info(
            "Assessment expiry reminders complete: sent=%d failed=%d skipped=%d",
            sent,
            failed,
            skipped,
        )
        return {"status": "ok", "sent": sent, "failed": failed, "skipped": skipped}
    finally:
        db.close()


@celery_app.task
def send_assessment_nudges():
    """Mid-window nudges off the delivery-tracking data (flag-gated).

    Two segments the funnel now distinguishes, each nudged at 48h and at
    most ONE nudge per assessment ever (the expiry reminder owns the end
    of the window):

    - ``delivered_not_opened`` — the invite landed but was never opened.
    - ``opened_not_started`` — opened or previewed, but Start never clicked.

    Assessment-scoped only (consistent with the invite + expiry reminders;
    Taali never emails candidates about the job). Gated by
    ``ASSESSMENT_NUDGES_ENABLED`` (default off) so turning the sequence on
    is a deliberate step once invite volume resumes.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy.orm import Session, joinedload

    from ..components.assessments.repository import (
        append_assessment_timeline_event,
        ensure_utc,
    )
    from ..domains.integrations_notifications.adapters import build_email_adapter
    from ..models.assessment import Assessment, AssessmentStatus
    from ..platform.database import SessionLocal

    if not getattr(settings, "ASSESSMENT_NUDGES_ENABLED", False):
        return {"status": "skipped", "reason": "flag_off"}
    if not (settings.RESEND_API_KEY or "").strip():
        return {"status": "skipped", "reason": "resend_not_configured"}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=48)

    db: Session = SessionLocal()
    sent = 0
    failed = 0
    skipped = 0
    try:
        pending = (
            db.query(Assessment)
            .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
            .filter(
                Assessment.status == AssessmentStatus.PENDING,
                Assessment.is_voided.is_(False),
                Assessment.is_demo.is_(False),
                Assessment.invite_sent_at != None,  # noqa: E711
                Assessment.invite_sent_at <= cutoff,
                Assessment.expires_at != None,  # noqa: E711
                # Leave the final day to the expiry reminder — one voice at a time.
                Assessment.expires_at > now + timedelta(hours=24),
            )
            .all()
        )
        email_svc = build_email_adapter()
        for assessment in pending:
            already_nudged = any(
                isinstance(e, dict) and e.get("event_type") == "nudge_sent"
                for e in (assessment.timeline or [])
            )
            if already_nudged:
                skipped += 1
                continue
            opened_at = ensure_utc(assessment.invite_opened_at or assessment.preview_viewed_at)
            delivered_at = ensure_utc(assessment.invite_delivered_at)
            if opened_at is not None and opened_at <= cutoff:
                kind = "opened_not_started"
            elif opened_at is None and delivered_at is not None and delivered_at <= cutoff:
                kind = "delivered_not_opened"
            else:
                skipped += 1
                continue
            candidate_email = (
                assessment.candidate.email if assessment.candidate else None
            ) or None
            if not candidate_email:
                skipped += 1
                continue
            candidate_name = (
                assessment.candidate.full_name if assessment.candidate else None
            ) or candidate_email
            task_name = (assessment.task.name if assessment.task else None) or "Technical assessment"
            expiry_text = assessment.expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            assessment_link = f"{settings.FRONTEND_URL}/assessment/{assessment.id}?token={assessment.token}"
            result = email_svc.send_assessment_nudge(
                candidate_email=candidate_email,
                candidate_name=candidate_name,
                task_name=task_name,
                assessment_link=assessment_link,
                kind=kind,
                expiry_text=expiry_text,
            )
            if result.get("success"):
                append_assessment_timeline_event(
                    assessment, "nudge_sent", {"kind": kind, "email_id": result.get("email_id")}
                )
                db.commit()
                sent += 1
            else:
                failed += 1
        logger.info(
            "Assessment nudges complete: sent=%d failed=%d skipped=%d",
            sent, failed, skipped,
        )
        return {"status": "ok", "sent": sent, "failed": failed, "skipped": skipped}
    finally:
        db.close()


# Note: ``sync_workable_orgs`` (every-30-min full sync of every job AND
# every candidate AND every CV download) was removed on 2026-05-20. It
# was the source of the constant rate-limiting and the starvation bug
# (``workable_last_sync_at`` debounce starved by ``sync_starred_roles``
# 's writes — see PR #194). Sync is now split per-cadence: jobs every
# 15 min (jobs_only), starred-role candidates every 5 min, agent-mode
# candidates every 5 min, everything else once nightly.

# Single per-org mutex shared by all four Workable sync tasks. Two tasks
# touching the same Workable token at the same time used to share-rate-
# limit each other into 429s (each ``sync_org`` calls ``list_open_jobs``
# which fires 5 endpoint hits, and per-candidate prefetches fan out
# further). A single mutex means only one task type is talking to
# Workable for a given org at a time. If a task can't get the lock it
# skips that fire — the next Beat tick (5-15 min away) will retry.
_WORKABLE_ORG_MUTEX_KEY_PREFIX = "celery:lock:workable_org_sync"
# Fallback TTL for a mutex acquired WITHOUT a heartbeat. No live caller uses
# this path: every sync task and the op path now acquire with ``heartbeat=True``
# (short TTL + renew-while-alive) so a worker SIGKILLed mid-run frees the lock
# in ~2 min instead of leaking it for the full TTL and blocking every Workable
# write for the org until then. Do NOT acquire the Workable mutex without a
# heartbeat.
_WORKABLE_ORG_MUTEX_TTL_SECONDS = 1800

# The op path (``run_workable_op_task``) AND all four sync tasks acquire with
# this SHORT TTL plus a heartbeat thread that re-extends it while the holder is
# alive. A worker killed mid-run (deploy SIGKILL) takes the heartbeat thread
# down with it, so the lock auto-expires in ~2 min instead of leaking for the
# full static TTL above and blocking every Workable write for the org until
# then. The interval is a third of the TTL so a single missed beat never
# expires a live lock.
_WORKABLE_OP_MUTEX_TTL_SECONDS = 120
_WORKABLE_OP_MUTEX_HEARTBEAT_SECONDS = 40


def _workable_mutex_heartbeat(client, key: str, ttl_seconds: int, stop_event) -> None:
    """Re-extend the mutex TTL every interval until released (or the process
    dies). ``expire`` only touches an existing key, so a beat racing with
    ``delete`` in release can never resurrect a freed lock."""
    interval = max(1, min(_WORKABLE_OP_MUTEX_HEARTBEAT_SECONDS, ttl_seconds // 3))
    while not stop_event.wait(interval):
        try:
            client.expire(key, ttl_seconds)
        except Exception:
            logger.exception("workable mutex heartbeat failed key=%s", key)
            return


def _acquire_workable_org_mutex(
    org_id: int,
    *,
    source: str,
    ttl: int | None = None,
    heartbeat: bool = False,
    namespace: str = _WORKABLE_ORG_MUTEX_KEY_PREFIX,
):
    """Acquire the per-org Workable mutex shared across all sync tasks + ops.

    ``source`` is a short label (``"jobs"`` / ``"starred"`` / ``"agent"`` /
    ``"nightly"`` / ``"workable_op:<op>"``) recorded as the lock value so we
    can see in Redis which task is holding the lock when debugging.

    ``heartbeat=True`` (op path) acquires with the short op TTL and spawns a
    daemon thread that renews it while the holder lives — deploy-safe. Sync
    callers leave it off and get the static 30-min TTL.

    ``namespace`` is the Redis key prefix; it defaults to the Workable lock so
    every existing caller is unchanged. The Bullhorn sync passes its own
    namespace so Bullhorn and Workable syncs for the same org don't contend on
    one lock (they talk to different APIs with independent rate budgets).

    Returns the handle on success, ``None`` if held by another task,
    ``False`` on Redis failure (caller treats as "run unguarded").
    """
    ttl_seconds = int(
        ttl
        if ttl is not None
        else (_WORKABLE_OP_MUTEX_TTL_SECONDS if heartbeat else _WORKABLE_ORG_MUTEX_TTL_SECONDS)
    )
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        key = f"{namespace}:{org_id}"
        if not client.set(key, source, nx=True, ex=ttl_seconds):
            return None
        stop_event = None
        if heartbeat:
            import threading

            stop_event = threading.Event()
            threading.Thread(
                target=_workable_mutex_heartbeat,
                args=(client, key, ttl_seconds, stop_event),
                name=f"workable-mutex-hb:{org_id}",
                daemon=True,
            ).start()
        return (client, key, stop_event)
    except Exception:
        logger.exception(
            "Failed to acquire workable-org mutex org_id=%s source=%s; running unguarded",
            org_id,
            source,
        )
        return False


def _release_workable_org_mutex(handle) -> None:
    if not handle:
        return
    try:
        client, key = handle[0], handle[1]
        stop_event = handle[2] if len(handle) > 2 else None
        if stop_event is not None:
            stop_event.set()  # stop the heartbeat before freeing the key
        client.delete(key)
    except Exception:
        logger.exception("Failed to release workable-org mutex")


# ---------------------------------------------------------------------------
# Op-priority signal. User-facing Workable writes (decision approvals /
# overrides) are tiny and latency-sensitive, but they share the per-org mutex
# with the periodic candidate syncs — which hold it for tens of minutes while
# walking a rate-limited candidate list. With no fairness, a steady drip of
# 5-min syncs starves an approve batch until it times out ("Workable lock
# timeout"). This flag lets a pending op tell the syncs to stand aside: it's
# set when an op is enqueued and refreshed while the op waits for the lock; the
# sync tasks skip an org whose flag is set, and an in-flight ``sync_org`` yields
# the lock at the next job boundary. Short TTL so it self-clears once the op
# finishes (no explicit clear) — the mutex still guarantees correctness if the
# flag is ever missed.
_WORKABLE_OP_PENDING_KEY_PREFIX = "celery:lock:workable_op_pending"
_WORKABLE_OP_PENDING_TTL_SECONDS = 120


def mark_workable_op_pending(org_id: int) -> None:
    """Signal that a user-facing Workable write is queued/waiting for this org
    so the periodic syncs yield the per-org mutex. Best-effort; never raises."""
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        client.set(
            f"{_WORKABLE_OP_PENDING_KEY_PREFIX}:{org_id}",
            "1",
            ex=_WORKABLE_OP_PENDING_TTL_SECONDS,
        )
    except Exception:
        logger.exception("mark_workable_op_pending failed org_id=%s", org_id)


def is_workable_op_pending(org_id: int) -> bool:
    """True if a user-facing Workable write is pending for this org. Fail-open
    (returns False on Redis error) — a flaky signal must never wedge syncs; the
    mutex still serializes writes whenever Redis is up."""
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        return bool(client.exists(f"{_WORKABLE_OP_PENDING_KEY_PREFIX}:{org_id}"))
    except Exception:
        logger.exception("is_workable_op_pending failed org_id=%s", org_id)
        return False


@celery_app.task
def sync_starred_roles():
    """Periodic task: pull from Workable for orgs with starred roles.

    Filters each org's sync to the workable_job_id of its starred roles,
    so this stays fast (per-job calls) even for orgs with hundreds of
    roles. The star remains sticky adoption/sync-cadence metadata, so paused
    and off roles stay synchronized. It is not permission to spend: the
    candidate path separately requires an enabled, unpaused, lifecycle-ready
    role before launching any new paid parse/score work.
    """
    from sqlalchemy.orm import Session

    from ..components.integrations.workable.service import WorkableService
    from ..components.integrations.workable.sync_service import WorkableSyncService
    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal

    if settings.MVP_DISABLE_WORKABLE:
        return {"status": "skipped", "reason": "workable_disabled"}

    db: Session = SessionLocal()
    synced = 0
    skipped = 0
    failed = 0
    try:
        starred_rows = (
            db.query(Role.organization_id, Role.workable_job_id)
            .filter(
                Role.starred_for_auto_sync == True,  # noqa: E712
                Role.deleted_at.is_(None),
                Role.workable_job_id.isnot(None),
            )
            .all()
        )
        by_org: dict[int, list[str]] = {}
        for org_id, workable_job_id in starred_rows:
            if not workable_job_id:
                continue
            shortcode = str(workable_job_id).strip()
            if not shortcode:
                continue
            by_org.setdefault(int(org_id), []).append(shortcode)

        if not by_org:
            return {"status": "ok", "synced": 0, "skipped": 0, "failed": 0}

        org_ids = list(by_org.keys())
        orgs = (
            db.query(Organization)
            .filter(
                Organization.id.in_(org_ids),
                Organization.workable_connected == True,  # noqa: E712
                Organization.workable_access_token != None,  # noqa: E711
                Organization.workable_subdomain != None,  # noqa: E711
            )
            .all()
        )

        for org in orgs:
            shortcodes = by_org.get(int(org.id)) or []
            if not shortcodes:
                continue
            org_id_int = int(org.id)
            if is_workable_op_pending(org_id_int):
                # A user-facing Workable write (decision approval/override) is
                # waiting on the per-org mutex — defer this fire so it isn't
                # starved. The next Beat tick retries.
                skipped += 1
                continue
            lock_handle = _acquire_workable_org_mutex(
                org_id_int, source="starred", heartbeat=True
            )
            if lock_handle is None:
                # Another sync task is currently talking to Workable for
                # this org — skip this fire to avoid 429 races.
                skipped += 1
                continue
            try:
                service = WorkableSyncService(
                    WorkableService(
                        access_token=org.workable_access_token,
                        subdomain=org.workable_subdomain,
                    )
                )
                # mode="full" preserves candidate metadata for adopted roles.
                # on_application_created applies the independent running-agent
                # gate before it can launch paid parsing/scoring.
                service.sync_org(
                    db,
                    org,
                    full_resync=False,
                    mode="full",
                    selected_job_shortcodes=shortcodes,
                    should_yield=lambda oid=org_id_int: is_workable_op_pending(oid),
                    # Ride the mutex these scoped candidate syncs reliably hold to
                    # discover brand-new Workable jobs — the 15-min jobs_only sweep
                    # gets starved of the lock on busy orgs (see _discover_new_jobs).
                    discover_new_jobs=True,
                )
                synced += 1
            except Exception:
                failed += 1
                logger.exception(
                    "Starred-roles sync failed for org_id=%s shortcodes=%s",
                    org.id,
                    shortcodes,
                )
            finally:
                _release_workable_org_mutex(lock_handle)
        return {"status": "ok", "synced": synced, "skipped": skipped, "failed": failed}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Sync redesign (2026-05-20): split the monolithic sync into per-cadence tasks
#
# Old behavior: every 30 min, sync_workable_orgs did a full-fat sync of every
# job and every candidate for every org — including re-downloading CVs we
# already had. That re-fetched ~50k applications worth of data hourly, which
# is what kept rate-limiting Workable.
#
# New behavior:
#   - sync_workable_jobs               (15 min, mode=jobs_only) — refresh role
#                                       metadata so new postings appear fast.
#   - sync_starred_roles               (5 min,  mode=full)      — starred roles
#                                       (existing, untouched).
#   - sync_agent_mode_roles            (5 min,  mode=full)      — agentic-mode
#                                       roles so the agent loop has fresh data.
#   - sync_workable_daily_candidates   (nightly, mode=full)     — every other
#                                       role's candidates once per day.
# ---------------------------------------------------------------------------

@celery_app.task
def sync_workable_jobs():
    """Periodic task: refresh Workable role metadata only — no candidate fetch.

    Runs every 15 minutes. Picks up newly-published jobs, title/description
    edits, and state changes (e.g. published → closed). Skips candidates
    entirely, so it stays well under Workable's rate limit even for orgs
    with hundreds of jobs.

    Candidates flow through three separate cadences:
      * starred roles → sync_starred_roles (5 min)
      * agent-mode roles → sync_agent_mode_roles (5 min)
      * everything else → sync_workable_daily_candidates (nightly)
    """
    from sqlalchemy.orm import Session

    from ..components.integrations.workable.service import WorkableService
    from ..components.integrations.workable.sync_service import WorkableSyncService
    from ..models.organization import Organization
    from ..platform.database import SessionLocal

    if settings.MVP_DISABLE_WORKABLE:
        return {"status": "skipped", "reason": "workable_disabled"}

    db: Session = SessionLocal()
    synced = 0
    skipped = 0
    failed = 0
    try:
        orgs = (
            db.query(Organization)
            .filter(
                Organization.workable_connected == True,  # noqa: E712
                Organization.workable_access_token != None,  # noqa: E711
                Organization.workable_subdomain != None,  # noqa: E711
            )
            .all()
        )
        for org in orgs:
            org_id_int = int(org.id)
            if is_workable_op_pending(org_id_int):
                # Defer to a pending user-facing Workable write (see
                # sync_starred_roles).
                skipped += 1
                continue
            lock_handle = _acquire_workable_org_mutex(
                org_id_int, source="jobs", heartbeat=True
            )
            if lock_handle is None:
                # Another task type is currently hitting Workable for
                # this org. Skip — next Beat tick will retry.
                skipped += 1
                continue
            try:
                service = WorkableSyncService(
                    WorkableService(
                        access_token=org.workable_access_token,
                        subdomain=org.workable_subdomain,
                    )
                )
                service.sync_org(
                    db,
                    org,
                    mode="jobs_only",
                    should_yield=lambda oid=org_id_int: is_workable_op_pending(oid),
                )
                synced += 1
            except Exception:
                failed += 1
                logger.exception("Workable jobs-only sync failed for org_id=%s", org.id)
            finally:
                _release_workable_org_mutex(lock_handle)
        return {"status": "ok", "synced": synced, "skipped": skipped, "failed": failed}
    finally:
        db.close()


@celery_app.task
def sync_agent_mode_roles():
    """Periodic task: pull candidates for roles where ``agentic_mode_enabled``.

    Mirrors sync_starred_roles but filters to roles with the agent loop
    turned on (and not paused). Runs at the same 5-min cadence so the
    agent always sees fresh Workable state. A role that is BOTH starred
    and agentic gets picked up by whichever task wins the per-org
    mutex race — the other one skips and the work isn't duplicated.
    """
    from sqlalchemy.orm import Session

    from ..components.integrations.workable.service import WorkableService
    from ..components.integrations.workable.sync_service import WorkableSyncService
    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal

    if settings.MVP_DISABLE_WORKABLE:
        return {"status": "skipped", "reason": "workable_disabled"}

    db: Session = SessionLocal()
    synced = 0
    skipped = 0
    failed = 0
    try:
        rows = (
            db.query(Role.organization_id, Role.workable_job_id)
            .filter(
                Role.agentic_mode_enabled == True,  # noqa: E712
                Role.agent_paused_at.is_(None),
                Role.deleted_at.is_(None),
                Role.workable_job_id.isnot(None),
            )
            .all()
        )
        by_org: dict[int, list[str]] = {}
        for org_id, wid in rows:
            shortcode = str(wid or "").strip()
            if not shortcode:
                continue
            by_org.setdefault(int(org_id), []).append(shortcode)
        if not by_org:
            return {"status": "ok", "synced": 0, "skipped": 0, "failed": 0}

        orgs = (
            db.query(Organization)
            .filter(
                Organization.id.in_(list(by_org.keys())),
                Organization.workable_connected == True,  # noqa: E712
                Organization.workable_access_token != None,  # noqa: E711
                Organization.workable_subdomain != None,  # noqa: E711
            )
            .all()
        )
        for org in orgs:
            shortcodes = by_org.get(int(org.id)) or []
            if not shortcodes:
                continue
            org_id_int = int(org.id)
            if is_workable_op_pending(org_id_int):
                # Defer to a pending user-facing Workable write (see
                # sync_starred_roles).
                skipped += 1
                continue
            lock_handle = _acquire_workable_org_mutex(
                org_id_int, source="agent", heartbeat=True
            )
            if lock_handle is None:
                skipped += 1
                continue
            try:
                service = WorkableSyncService(
                    WorkableService(
                        access_token=org.workable_access_token,
                        subdomain=org.workable_subdomain,
                    )
                )
                service.sync_org(
                    db,
                    org,
                    full_resync=False,
                    mode="full",
                    selected_job_shortcodes=shortcodes,
                    should_yield=lambda oid=org_id_int: is_workable_op_pending(oid),
                    # Ride the mutex these scoped candidate syncs reliably hold to
                    # discover brand-new Workable jobs — the 15-min jobs_only sweep
                    # gets starved of the lock on busy orgs (see _discover_new_jobs).
                    discover_new_jobs=True,
                )
                synced += 1
            except Exception:
                failed += 1
                logger.exception(
                    "Agent-mode sync failed for org_id=%s shortcodes=%s",
                    org.id,
                    shortcodes,
                )
            finally:
                _release_workable_org_mutex(lock_handle)
        return {"status": "ok", "synced": synced, "skipped": skipped, "failed": failed}
    finally:
        db.close()


@celery_app.task
def sync_workable_daily_candidates():
    """Nightly catch-all: full sync of candidates for non-starred, non-agent roles.

    Starred and agent-mode roles get candidates every 5 min. This task
    covers everything else so an inactive role's candidates stay updated
    at a once-a-day cadence. Scheduled at 03:00 UTC by default — see
    celery_app.beat_schedule.
    """
    from sqlalchemy.orm import Session

    from ..components.integrations.workable.service import WorkableService
    from ..components.integrations.workable.sync_service import WorkableSyncService
    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal

    if settings.MVP_DISABLE_WORKABLE:
        return {"status": "skipped", "reason": "workable_disabled"}

    db: Session = SessionLocal()
    synced = 0
    skipped = 0
    failed = 0
    try:
        rows = (
            db.query(Role.organization_id, Role.workable_job_id)
            .filter(
                Role.source == "workable",
                Role.deleted_at.is_(None),
                Role.workable_job_id.isnot(None),
                Role.starred_for_auto_sync == False,  # noqa: E712
                # Skip agent-mode unless it's paused — paused agents still
                # need the nightly catch-up since the 5-min path skips them.
                ((Role.agentic_mode_enabled == False) | (Role.agent_paused_at.isnot(None))),  # noqa: E712
            )
            .all()
        )
        by_org: dict[int, list[str]] = {}
        for org_id, wid in rows:
            shortcode = str(wid or "").strip()
            if not shortcode:
                continue
            by_org.setdefault(int(org_id), []).append(shortcode)
        if not by_org:
            return {"status": "ok", "synced": 0, "skipped": 0, "failed": 0}

        orgs = (
            db.query(Organization)
            .filter(
                Organization.id.in_(list(by_org.keys())),
                Organization.workable_connected == True,  # noqa: E712
                Organization.workable_access_token != None,  # noqa: E711
                Organization.workable_subdomain != None,  # noqa: E711
            )
            .all()
        )
        for org in orgs:
            shortcodes = by_org.get(int(org.id)) or []
            if not shortcodes:
                continue
            org_id_int = int(org.id)
            if is_workable_op_pending(org_id_int):
                # Defer to a pending user-facing Workable write (see
                # sync_starred_roles).
                skipped += 1
                continue
            lock_handle = _acquire_workable_org_mutex(
                org_id_int, source="nightly", heartbeat=True
            )
            if lock_handle is None:
                skipped += 1
                continue
            try:
                service = WorkableSyncService(
                    WorkableService(
                        access_token=org.workable_access_token,
                        subdomain=org.workable_subdomain,
                    )
                )
                service.sync_org(
                    db,
                    org,
                    full_resync=False,
                    mode="full",
                    selected_job_shortcodes=shortcodes,
                    should_yield=lambda oid=org_id_int: is_workable_op_pending(oid),
                    # Ride the mutex these scoped candidate syncs reliably hold to
                    # discover brand-new Workable jobs — the 15-min jobs_only sweep
                    # gets starved of the lock on busy orgs (see _discover_new_jobs).
                    discover_new_jobs=True,
                )
                synced += 1
            except Exception:
                failed += 1
                logger.exception(
                    "Daily candidate sync failed for org_id=%s (%d shortcodes)",
                    org.id,
                    len(shortcodes),
                )
            finally:
                _release_workable_org_mutex(lock_handle)
        return {"status": "ok", "synced": synced, "skipped": skipped, "failed": failed}
    finally:
        db.close()


# Stuck-run cleanup. If a Celery worker dies mid-sync (OOM, SIGKILL,
# container restart) the finally block in ``sync_runner.execute_workable_sync_run``
# never runs, leaving the ``WorkableSyncRun`` row in ``status='running'``
# with ``finished_at=NULL`` forever. ``_latest_running_run_for_org`` then
# matches that row and every subsequent POST /workable/sync returns
# ``already_running`` — the user is silently locked out until someone
# runs a manual SQL UPDATE.
_STUCK_RUN_TIMEOUT_HOURS = 6
# A dead worker stops bumping the run's heartbeat (``updated_at``, written as the
# runner persists progress) long before the 6h absolute ceiling. Reaping on a
# stale heartbeat clears a zombie within ~30m instead of locking the org out for
# hours; a healthy run writes progress far more often than this.
_STUCK_RUN_HEARTBEAT_MINUTES = 30


@celery_app.task
def reap_stuck_workable_sync_runs():
    """Finalize WorkableSyncRun rows whose worker died before the run finished.

    Also clears stale org-level ``workable_sync_progress`` JSON for orgs
    that have no in-flight run but still hold old progress state — this
    happens when ``sync_workable_jobs`` / ``sync_starred_roles`` /
    ``sync_agent_mode_roles`` (which call ``sync_org`` without a
    ``run_id``) die mid-sync and never get the chance to clear it.

    A real run takes 30-90 minutes including candidate CV downloads, so 6h
    is a safe ceiling that won't kill a healthy in-flight sync. Beat fires
    this every 30 minutes.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import Session

    from ..models.organization import Organization
    from ..models.workable_sync_run import WorkableSyncRun
    from ..platform.database import SessionLocal

    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(hours=_STUCK_RUN_TIMEOUT_HOURS)
        heartbeat_cutoff = now - timedelta(minutes=_STUCK_RUN_HEARTBEAT_MINUTES)
        # A run is dead if it's blown the absolute 6h ceiling OR its heartbeat
        # (updated_at) has gone stale — the latter catches a worker that died
        # minutes in, instead of holding the org's sync lock for hours.
        running_runs = (
            db.query(WorkableSyncRun)
            .filter(
                WorkableSyncRun.status == "running",
                WorkableSyncRun.finished_at.is_(None),
            )
            .all()
        )
        stuck = []
        for run in running_runs:
            beat = run.updated_at or run.started_at
            if beat is not None and beat.tzinfo is None:
                beat = beat.replace(tzinfo=timezone.utc)
            started = run.started_at
            if started is not None and started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if (started is not None and started < threshold) or (
                beat is None or beat < heartbeat_cutoff
            ):
                stuck.append(run)
        org_ids_from_runs: set[int] = set()
        for run in stuck:
            run.status = "failed"
            run.finished_at = now
            run.phase = run.phase or "aborted"
            errors = list(run.errors or [])
            errors.append(
                "workable_sync_stale: A stale Workable sync was closed safely. Start a new sync."
            )
            run.errors = errors
            org_ids_from_runs.add(int(run.organization_id))

        if org_ids_from_runs:
            (
                db.query(Organization)
                .filter(Organization.id.in_(org_ids_from_runs))
                .update(
                    {
                        Organization.workable_sync_started_at: None,
                        Organization.workable_sync_progress: None,
                        Organization.workable_sync_cancel_requested_at: None,
                    },
                    synchronize_session=False,
                )
            )

        # Second sweep: orgs whose ``workable_sync_progress`` JSON is
        # stale but have no in-flight run row to reap. These come from
        # the run-less Beat tasks (sync_workable_jobs, sync_starred_roles,
        # sync_agent_mode_roles) — when their worker dies mid-flight,
        # ``_persist_progress`` leaves the org's progress JSON pointing
        # at the half-finished work forever, and nothing else clears it.
        stale_orgs = (
            db.query(Organization.id)
            .filter(
                Organization.workable_sync_started_at.isnot(None),
                Organization.workable_sync_started_at < threshold,
                ~Organization.id.in_(
                    db.query(WorkableSyncRun.organization_id).filter(
                        WorkableSyncRun.status == "running",
                        WorkableSyncRun.finished_at.is_(None),
                    )
                ),
            )
            .all()
        )
        org_ids_from_stale = {int(row[0]) for row in stale_orgs}
        if org_ids_from_stale:
            (
                db.query(Organization)
                .filter(Organization.id.in_(org_ids_from_stale))
                .update(
                    {
                        Organization.workable_sync_started_at: None,
                        Organization.workable_sync_progress: None,
                        Organization.workable_sync_cancel_requested_at: None,
                    },
                    synchronize_session=False,
                )
            )

        if not stuck and not org_ids_from_stale:
            return {"status": "ok", "reaped": 0, "cleared_orgs": 0}

        db.commit()
        if stuck:
            logger.warning(
                "reap_stuck_workable_sync_runs reaped %d run(s) across %d org(s): run_ids=%s",
                len(stuck),
                len(org_ids_from_runs),
                [r.id for r in stuck],
            )
        if org_ids_from_stale:
            logger.warning(
                "reap_stuck_workable_sync_runs cleared stale progress for %d org(s): org_ids=%s",
                len(org_ids_from_stale),
                sorted(org_ids_from_stale),
            )
        return {
            "status": "ok",
            "reaped": len(stuck),
            "cleared_orgs": len(org_ids_from_stale),
            "run_org_ids": sorted(org_ids_from_runs),
            "stale_org_ids": sorted(org_ids_from_stale),
        }
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=300)
def generate_assessment_task_for_role(self, role_id: int, organization_id: int):
    """Auto-provision an assessment task for a newly-created role from its JD.

    Generation is a multi-call Sonnet operation, so it runs off the
    request path here. The generated task is persisted as an inactive DRAFT
    and linked to the role; a durable Turn-on intent approves it automatically
    only after battle/repository validation. No-op if the role
    already has any linked task (including a generated review draft) or the JD
    is too thin.

    Every requested run is claimed against Role-backed durable state. Generator
    and persistence failures are retried with bounded exponential backoff; Beat
    later recovers an exhausted chain, lost broker kick, or stale worker claim.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import Session
    from ..platform.database import SessionLocal
    from ..platform.config import settings
    from ..models.role import Role
    from ..services.task_provisioning_service import (
        PROVISIONING_BLOCKED,
        PROVISIONING_FAILED,
        PROVISIONING_RETRY_WAIT,
        PROVISIONING_SUCCEEDED,
        TaskProvisioningBlockedError,
        TaskProvisioningRetryableError,
        TaskProvisioningSupersededError,
        claim_assessment_task_provisioning,
        finish_assessment_task_provisioning,
        generate_and_link_task_for_role,
    )

    db: Session = SessionLocal()
    claim_token: str | None = None
    try:
        # A broker delivery can pre-date a workspace pause. Serialize task
        # admission on the workspace row before claiming the role-backed paid
        # outbox, so a held workspace leaves the request pending for the
        # recovery sweep after Resume instead of starting Sonnet generation.
        from ..services.workspace_agent_control import (
            workspace_agent_control_snapshot,
        )

        workspace_paused, _workspace_control_version = (
            workspace_agent_control_snapshot(
                db,
                organization_id=int(organization_id),
                lock=True,
            )
        )
        if workspace_paused:
            db.rollback()
            return {"status": "deferred", "reason": "workspace_paused"}

        claim = claim_assessment_task_provisioning(
            db,
            role_id=int(role_id),
            organization_id=int(organization_id),
        )
        if claim.status == "missing":
            return {"status": "skipped", "reason": "role_not_found"}
        if claim.status == "already_linked":
            return {
                "status": "already_linked",
                "task_id": claim.linked_task_id,
            }
        if claim.status != "claimed" or not claim.claim_token:
            return {"status": "noop", "reason": claim.status}
        claim_token = claim.claim_token

        api_key = str(getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
        if not api_key:
            raise TaskProvisioningRetryableError(
                "ANTHROPIC_API_KEY is not configured for assessment-task generation"
            )

        role = (
            db.query(Role)
            .filter(Role.id == role_id, Role.organization_id == organization_id)
            .one_or_none()
        )
        if role is None:
            raise TaskProvisioningSupersededError("role was removed after claim")
        task = generate_and_link_task_for_role(
            db,
            role,
            api_key=api_key,
            organization_id=organization_id,
            claim_token=claim_token,
        )
        if task is None:
            finish_assessment_task_provisioning(
                db,
                role_id=role_id,
                organization_id=organization_id,
                claim_token=claim_token,
                status=PROVISIONING_SUCCEEDED,
            )
            return {"status": "already_linked"}

        state_finished = finish_assessment_task_provisioning(
            db,
            role_id=role_id,
            organization_id=organization_id,
            claim_token=claim_token,
            status=PROVISIONING_SUCCEEDED,
            task_id=int(task.id),
        )
        if not state_finished:
            return {"status": "superseded", "task_id": int(task.id)}
        # Battle-test the fresh draft so the review card carries a report
        # card (repo boots, baseline fails meaningfully) instead of raw JSON.
        try:
            battle_test_generated_task.delay(int(task.id), int(organization_id))
        except Exception:  # the generated/link state is already durable
            logger.warning(
                "battle-test kick failed for generated task %s", task.id, exc_info=True
            )
        return {
            "status": "generated",
            "task_id": int(task.id),
            "task_key": task.task_key,
            "needs_review": True,
        }
    except TaskProvisioningBlockedError as exc:
        db.rollback()
        if claim_token:
            finish_assessment_task_provisioning(
                db,
                role_id=role_id,
                organization_id=organization_id,
                claim_token=claim_token,
                status=PROVISIONING_BLOCKED,
                error=str(exc),
            )
        logger.warning("assessment-task provisioning blocked role=%s: %s", role_id, exc)
        return {"status": "blocked", "reason": str(exc)}
    except TaskProvisioningSupersededError as exc:
        db.rollback()
        logger.info("assessment-task provisioning superseded role=%s: %s", role_id, exc)
        return {"status": "superseded", "reason": str(exc)}
    except Exception as exc:
        db.rollback()
        retries = int(getattr(self.request, "retries", 0) or 0)
        max_retries = int(self.max_retries or 0)
        if retries < max_retries:
            countdown = min(300 * (2 ** min(retries, 3)), 1800)
            if claim_token:
                recorded = finish_assessment_task_provisioning(
                    db,
                    role_id=role_id,
                    organization_id=organization_id,
                    claim_token=claim_token,
                    status=PROVISIONING_RETRY_WAIT,
                    error="assessment_task_generation_failed",
                    next_attempt_at=datetime.now(timezone.utc)
                    + timedelta(seconds=countdown),
                )
                if not recorded:
                    return {"status": "superseded"}
            logger.warning(
                "assessment-task provisioning retry role=%s retry=%s/%s in=%ss: %s",
                role_id,
                retries + 1,
                max_retries,
                countdown,
                exc,
            )
            raise self.retry(exc=exc, countdown=countdown)

        # The Celery chain is bounded. Persist a cooled-down failed state so the
        # periodic sweep can start a later chain (for example after a missing
        # API key is configured) without any recruiter/manual recovery step.
        if claim_token:
            finish_assessment_task_provisioning(
                db,
                role_id=role_id,
                organization_id=organization_id,
                claim_token=claim_token,
                status=PROVISIONING_FAILED,
                error="assessment_task_generation_failed",
                next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        logger.exception(
            "assessment-task provisioning retries exhausted role=%s", role_id
        )
        return {
            "status": "failed",
            "reason": "assessment_task_generation_failed",
            "retry_after_seconds": 3600,
        }
    finally:
        db.close()


@celery_app.task
def sweep_assessment_task_provisioning(limit: int = 200):
    """Recover generation, battle-test, and one-click activation outboxes."""
    from datetime import datetime, timezone

    from sqlalchemy.orm import Session, selectinload

    from ..models.organization import Organization
    from ..models.role import Role
    from ..models.task import Task
    from ..platform.config import settings
    from ..platform.database import SessionLocal
    from ..services.task_provisioning_service import (
        PROVISIONING_RECOVERABLE_STATUSES,
        provisioning_state_is_due,
        task_provisioning_state,
    )
    from ..services.task_battle_test import battle_test_provisioning_action
    from ..services.role_activation_intent import (
        ACTIVATION_ACTIVE_STATUSES,
        activation_intent_state,
        activation_intent_task_ready,
        block_activation_intent_if_task_exhausted,
    )

    db: Session = SessionLocal()
    bounded_limit = max(1, min(int(limit), 1000))
    rows: list[Role] = []
    try:
        if getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False):
            status_expr = Role.assessment_task_provisioning["status"].as_string()
            rows = (
                db.query(Role)
                .join(Organization, Organization.id == Role.organization_id)
                .filter(
                    Role.deleted_at.is_(None),
                    Organization.agent_workspace_paused_at.is_(None),
                    Role.assessment_task_provisioning.isnot(None),
                    status_expr.in_(sorted(PROVISIONING_RECOVERABLE_STATUSES)),
                )
                .order_by(Role.created_at.asc(), Role.id.asc())
                .limit(bounded_limit)
                .all()
            )
        now = datetime.now(timezone.utc)
        role_keys = [
            (int(role.id), int(role.organization_id))
            for role in rows
            if provisioning_state_is_due(task_provisioning_state(role), now=now)
        ]
        # Generated Task.extra_data is itself the battle-test outbox. Scan a
        # generous bounded set so legacy generated drafts (created before the
        # explicit provisioning sub-state) are recovered too.
        battle_rows = (
            db.query(Task)
            .join(Organization, Organization.id == Task.organization_id)
            .filter(
                Task.organization_id.isnot(None),
                Organization.agent_workspace_paused_at.is_(None),
                Task.is_active.is_(False),
                Task.extra_data.isnot(None),
            )
            .order_by(Task.created_at.desc(), Task.id.desc())
            .limit(max(1000, min(bounded_limit * 10, 5000)))
            .all()
        )
        actionable_tasks = [
            (int(task.id), int(task.organization_id), action)
            for task in battle_rows
            if isinstance(task.extra_data, dict)
            and task.extra_data.get("generated")
            and task.extra_data.get("needs_review", True)
            and (action := battle_test_provisioning_action(task, now=now))
        ][:bounded_limit]
        battle_keys = [
            (task_id, org_id)
            for task_id, org_id, action in actionable_tasks
            if action == "battle_test"
        ]
        repair_keys = [
            (task_id, org_id)
            for task_id, org_id, action in actionable_tasks
            if action == "repair"
        ]
        activation_status = Role.assessment_task_provisioning[
            "activation_intent"
        ]["status"].as_string()
        activation_rows = (
            db.query(Role)
            .options(selectinload(Role.tasks))
            .join(Organization, Organization.id == Role.organization_id)
            .filter(
                Role.deleted_at.is_(None),
                Organization.agent_workspace_paused_at.is_(None),
                Role.agentic_mode_enabled.is_(False),
                Role.assessment_task_provisioning.isnot(None),
                activation_status.in_(sorted(ACTIVATION_ACTIVE_STATUSES)),
            )
            .order_by(Role.updated_at.asc(), Role.id.asc())
            .limit(bounded_limit)
            .all()
        )
        activation_blocked = sum(
            1
            for role in activation_rows
            if block_activation_intent_if_task_exhausted(role, now=now)
        )
        if activation_blocked:
            db.commit()
        activation_keys = [
            (
                int(role.id),
                str(activation_intent_state(role).get("request_id") or ""),
            )
            for role in activation_rows
            if activation_intent_task_ready(role)
            and activation_intent_state(role).get("request_id")
        ]
        focus_rows = (
            db.query(Role)
            .join(Organization, Organization.id == Role.organization_id)
            .filter(
                Role.deleted_at.is_(None),
                Role.agentic_mode_enabled.is_(True),
                Role.agent_paused_at.is_(None),
                Organization.agent_workspace_paused_at.is_(None),
                Role.job_spec_text.isnot(None),
                Role.job_spec_text != "",
                Role.interview_focus.is_(None),
            )
            .order_by(Role.updated_at.asc(), Role.id.asc())
            .limit(bounded_limit)
            .all()
        )
        focus_keys = []
        for role in focus_rows:
            provisioning = (
                role.assessment_task_provisioning
                if isinstance(role.assessment_task_provisioning, dict)
                else {}
            )
            focus_state = provisioning.get("interview_focus_provisioning") or {}
            next_attempt = focus_state.get("next_attempt_at")
            if next_attempt:
                try:
                    parsed = datetime.fromisoformat(
                        str(next_attempt).replace("Z", "+00:00")
                    )
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    if parsed > now:
                        continue
                except (TypeError, ValueError):
                    pass
            focus_keys.append(int(role.id))
        tech_rows = (
            db.query(Role)
            .join(Organization, Organization.id == Role.organization_id)
            .filter(
                Role.deleted_at.is_(None),
                Role.agentic_mode_enabled.is_(True),
                Role.agent_paused_at.is_(None),
                Organization.agent_workspace_paused_at.is_(None),
                Role.job_spec_text.isnot(None),
                Role.job_spec_text != "",
                Role.tech_questions_signature.is_(None),
            )
            .order_by(Role.updated_at.asc(), Role.id.asc())
            .limit(bounded_limit)
            .all()
        )
        tech_keys = []
        for role in tech_rows:
            provisioning = (
                role.assessment_task_provisioning
                if isinstance(role.assessment_task_provisioning, dict)
                else {}
            )
            tech_state = provisioning.get("tech_questions_provisioning") or {}
            next_attempt = tech_state.get("next_attempt_at")
            if next_attempt:
                try:
                    parsed = datetime.fromisoformat(
                        str(next_attempt).replace("Z", "+00:00")
                    )
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    if parsed > now:
                        continue
                except (TypeError, ValueError):
                    pass
            tech_keys.append(int(role.id))
    finally:
        db.close()

    dispatched = 0
    failed = 0
    for pending_role_id, pending_org_id in role_keys:
        try:
            generate_assessment_task_for_role.delay(
                pending_role_id, pending_org_id
            )
            dispatched += 1
        except Exception:
            failed += 1
            logger.exception(
                "assessment-task provisioning sweep kick failed role=%s",
                pending_role_id,
            )

    battle_dispatched = 0
    battle_failed = 0
    for pending_task_id, pending_task_org_id in battle_keys:
        try:
            battle_test_generated_task.delay(
                pending_task_id, pending_task_org_id
            )
            battle_dispatched += 1
        except Exception:
            battle_failed += 1
            logger.exception(
                "generated-task battle-test sweep kick failed task=%s",
                pending_task_id,
            )

    repair_dispatched = 0
    repair_failed = 0
    for pending_task_id, pending_task_org_id in repair_keys:
        try:
            repair_generated_task_after_battle_failure.delay(
                pending_task_id, pending_task_org_id
            )
            repair_dispatched += 1
        except Exception:
            repair_failed += 1
            logger.exception(
                "generated-task repair sweep kick failed task=%s",
                pending_task_id,
            )
    activation_dispatched = 0
    activation_failed = 0
    from .agent_tasks import agent_cohort_tick_role

    for pending_role_id, activation_request_id in activation_keys:
        try:
            agent_cohort_tick_role.delay(
                pending_role_id,
                activation=True,
                activation_intent_id=activation_request_id,
            )
            activation_dispatched += 1
        except Exception:
            activation_failed += 1
            logger.exception(
                "durable role activation sweep kick failed role=%s",
                pending_role_id,
            )
    focus_dispatched = 0
    focus_failed = 0
    from .automation_tasks import generate_role_interview_focus

    for pending_role_id in focus_keys:
        try:
            generate_role_interview_focus.delay(
                pending_role_id, requires_running_agent=True
            )
            focus_dispatched += 1
        except Exception:
            focus_failed += 1
            logger.exception(
                "interview-focus recovery kick failed role=%s", pending_role_id
            )
    tech_dispatched = 0
    tech_failed = 0
    from .automation_tasks import regenerate_role_tech_questions

    for pending_role_id in tech_keys:
        try:
            regenerate_role_tech_questions.delay(pending_role_id)
            tech_dispatched += 1
        except Exception:
            tech_failed += 1
            logger.exception(
                "tech-question recovery kick failed role=%s", pending_role_id
            )
    return {
        "status": "ok",
        "scanned": len(rows),
        "due": len(role_keys),
        "dispatched": dispatched,
        "failed": failed,
        "generation_enabled": bool(
            getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False)
        ),
        "battle_scanned": len(battle_rows),
        "battle_due": len(battle_keys),
        "battle_dispatched": battle_dispatched,
        "battle_failed": battle_failed,
        "repair_due": len(repair_keys),
        "repair_dispatched": repair_dispatched,
        "repair_failed": repair_failed,
        "activation_due": len(activation_keys),
        "activation_dispatched": activation_dispatched,
        "activation_failed": activation_failed,
        "activation_blocked": activation_blocked,
        "interview_focus_due": len(focus_keys),
        "interview_focus_dispatched": focus_dispatched,
        "interview_focus_failed": focus_failed,
        "tech_questions_due": len(tech_keys),
        "tech_questions_dispatched": tech_dispatched,
        "tech_questions_failed": tech_failed,
    }


def _kick_ready_activation_intents_for_task(
    db, *, task_id: int, organization_id: int
) -> None:
    """Low-latency post-battle kick; the provisioning sweep is the backstop."""
    from ..models.role import Role, role_tasks
    from ..services.role_activation_intent import (
        activation_intent_state,
        activation_intent_task_ready,
        block_activation_intent_if_task_exhausted,
    )
    from .agent_tasks import agent_cohort_tick_role

    roles = (
        db.query(Role)
        .join(role_tasks, role_tasks.c.role_id == Role.id)
        .filter(
            role_tasks.c.task_id == int(task_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(False),
        )
        .all()
    )
    blocked = False
    for role in roles:
        if block_activation_intent_if_task_exhausted(role):
            db.add(role)
            blocked = True
    if blocked:
        db.commit()
    for role in roles:
        intent = activation_intent_state(role)
        if not intent.get("request_id") or not activation_intent_task_ready(role):
            continue
        try:
            agent_cohort_tick_role.delay(
                int(role.id),
                activation=True,
                activation_intent_id=str(intent["request_id"]),
            )
        except Exception:
            logger.exception(
                "post-battle activation kick failed role=%s; sweep will retry",
                role.id,
            )


@celery_app.task
def recompute_task_calibrations():
    """Weekly per-(task, role_family) calibration sweep.

    ``sub_agents.task_calibration.recompute_all`` (predictive quality =
    correlation of assessment score vs realised outcome, with retire
    flagging) existed but was never on the beat schedule, so calibrations
    only moved when someone ran it by hand. Pure SQL/python — no model
    calls, no score changes; writes ``task_calibrations`` rows only.
    """
    from sqlalchemy.orm import Session
    from ..platform.database import SessionLocal
    from ..sub_agents.task_calibration import recompute_all

    db: Session = SessionLocal()
    try:
        summary = recompute_all(db)
        logger.info("Task calibration sweep: %s", summary)
        return {"status": "ok", **summary}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=120)
def battle_test_generated_task(self, task_id: int, organization_id: int):
    """Run the E2B battle-test on a generated draft and stamp the report card.

    Sandbox-only (no Anthropic calls). Task.extra_data is the durable intent and
    claim record, so a lost kick or worker crash is recovered by the same Beat
    sweep as JD generation. Duplicate deliveries collapse under a row lock.
    """
    import uuid
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import Session
    from ..platform.database import SessionLocal
    from ..models.task import Task
    from ..services.task_battle_test import (
        BATTLE_TEST_FAILED,
        BATTLE_TEST_MAX_REPAIR_ATTEMPTS,
        BATTLE_TEST_REPAIR_EXHAUSTED,
        BATTLE_TEST_REPAIR_FAILED,
        BATTLE_TEST_REPAIR_PENDING,
        BATTLE_TEST_REPAIR_RETRY_WAIT,
        BATTLE_TEST_REPAIRING,
        BATTLE_TEST_RETRY_WAIT,
        BATTLE_TEST_RUNNING,
        BATTLE_TEST_SUCCEEDED,
        battle_test_provisioning_is_due,
        battle_test_repair_feedback,
        run_battle_test,
    )

    db: Session = SessionLocal()
    claim_token: str | None = None
    try:
        task = (
            db.query(Task)
            .filter(Task.id == task_id, Task.organization_id == organization_id)
            .with_for_update()
            .one_or_none()
        )
        if task is None:
            return {"status": "skipped", "reason": "task_not_found"}
        extra = dict(task.extra_data) if isinstance(task.extra_data, dict) else {}
        if not extra.get("generated"):
            return {"status": "skipped", "reason": "not_generated"}
        if isinstance(extra.get("battle_test"), dict):
            state = (
                dict(extra.get("battle_test_provisioning"))
                if isinstance(extra.get("battle_test_provisioning"), dict)
                else {}
            )
            if state.get("status") in {
                BATTLE_TEST_REPAIR_PENDING,
                BATTLE_TEST_REPAIRING,
                BATTLE_TEST_REPAIR_RETRY_WAIT,
                BATTLE_TEST_REPAIR_FAILED,
                BATTLE_TEST_REPAIR_EXHAUSTED,
            }:
                return {
                    "status": "noop",
                    "reason": state.get("status"),
                    "task_id": int(task.id),
                }
            if state.get("status") != BATTLE_TEST_SUCCEEDED:
                now = datetime.now(timezone.utc).isoformat()
                extra["battle_test_provisioning"] = {
                    **state,
                    "status": BATTLE_TEST_SUCCEEDED,
                    "last_error": None,
                    "next_attempt_at": None,
                    "updated_at": now,
                    "completed_at": now,
                }
                task.extra_data = extra
                db.commit()
            return {
                "status": "already_done",
                "task_id": int(task.id),
                "verdict": extra["battle_test"].get("verdict"),
            }
        if not battle_test_provisioning_is_due(task):
            state = extra.get("battle_test_provisioning") or {}
            return {"status": "noop", "reason": state.get("status") or "not_due"}

        state = (
            dict(extra.get("battle_test_provisioning"))
            if isinstance(extra.get("battle_test_provisioning"), dict)
            else {}
        )
        claim_token = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        extra["battle_test_provisioning"] = {
            **state,
            "status": BATTLE_TEST_RUNNING,
            "claim_token": claim_token,
            "attempts": int(state.get("attempts") or 0) + 1,
            "last_error": None,
            "next_attempt_at": None,
            "started_at": now,
            "updated_at": now,
        }
        task.extra_data = extra
        db.commit()
        db.refresh(task)

        report = run_battle_test(task)
        # ``run_battle_test`` returns infrastructure exceptions as a report so
        # the card can render. Treat those as retryable delivery failures; a
        # deterministic structural fail (no error) is a valid completed report
        # that correctly remains behind bounded automatic repair and, only
        # after repair exhaustion, the explicit HITL boundary.
        if report.get("error"):
            raise RuntimeError(f"battle-test infrastructure error: {report['error']}")

        task = (
            db.query(Task)
            .filter(Task.id == task_id, Task.organization_id == organization_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if task is None:
            return {"status": "superseded", "reason": "task_removed"}
        extra = dict(task.extra_data) if isinstance(task.extra_data, dict) else {}
        state = (
            dict(extra.get("battle_test_provisioning"))
            if isinstance(extra.get("battle_test_provisioning"), dict)
            else {}
        )
        if str(state.get("claim_token") or "") != claim_token:
            db.rollback()
            return {"status": "superseded"}
        completed_at = datetime.now(timezone.utc).isoformat()
        extra["battle_test"] = report
        if report.get("verdict") != "pass":
            repair_attempts = int(state.get("repair_attempts") or 0)
            repair_available = repair_attempts < BATTLE_TEST_MAX_REPAIR_ATTEMPTS
            repair_status = (
                BATTLE_TEST_REPAIR_PENDING
                if repair_available
                else BATTLE_TEST_REPAIR_EXHAUSTED
            )
            feedback = battle_test_repair_feedback(report)
            extra["battle_test_provisioning"] = {
                **state,
                "status": repair_status,
                "last_error": feedback[:2000],
                "next_attempt_at": None,
                "updated_at": completed_at,
                "completed_at": completed_at if not repair_available else None,
            }
            task.extra_data = extra
            db.commit()
            if repair_available:
                from ..services.workspace_agent_control import (
                    workspace_agent_is_paused,
                )

                if workspace_agent_is_paused(
                    db,
                    organization_id=int(organization_id),
                ):
                    return {
                        "status": "repair_deferred",
                        "reason": "workspace_paused",
                        "task_id": int(task.id),
                        "verdict": report.get("verdict"),
                        "repair_attempts": repair_attempts,
                    }
                try:
                    repair_generated_task_after_battle_failure.delay(
                        int(task.id), int(organization_id)
                    )
                except Exception:
                    logger.exception(
                        "battle-test repair kick failed task=%s; sweep will recover",
                        task.id,
                    )
                return {
                    "status": "repair_queued",
                    "task_id": int(task.id),
                    "verdict": report.get("verdict"),
                    "repair_attempts": repair_attempts,
                }
            return {
                "status": "repair_exhausted",
                "task_id": int(task.id),
                "verdict": report.get("verdict"),
                "repair_attempts": repair_attempts,
            }

        extra["battle_test_provisioning"] = {
            **state,
            "status": BATTLE_TEST_SUCCEEDED,
            "last_error": None,
            "next_attempt_at": None,
            "updated_at": completed_at,
            "completed_at": completed_at,
        }
        task.extra_data = extra
        db.commit()
        _kick_ready_activation_intents_for_task(
            db, task_id=int(task.id), organization_id=int(organization_id)
        )
        return {
            "status": "done",
            "task_id": int(task.id),
            "verdict": report.get("verdict"),
        }
    except Exception as exc:  # provider/DB errors are durable + bounded
        db.rollback()
        retries = int(getattr(self.request, "retries", 0) or 0)
        max_retries = int(self.max_retries or 0)
        countdown = min(120 * (2 ** min(retries, 4)), 1800)
        if claim_token:
            retry_task = (
                db.query(Task)
                .filter(Task.id == task_id, Task.organization_id == organization_id)
                .with_for_update()
                .one_or_none()
            )
            if retry_task is not None:
                retry_extra = (
                    dict(retry_task.extra_data)
                    if isinstance(retry_task.extra_data, dict)
                    else {}
                )
                retry_state = (
                    dict(retry_extra.get("battle_test_provisioning"))
                    if isinstance(retry_extra.get("battle_test_provisioning"), dict)
                    else {}
                )
                if str(retry_state.get("claim_token") or "") == claim_token:
                    next_attempt = datetime.now(timezone.utc) + (
                        timedelta(seconds=countdown)
                        if retries < max_retries
                        else timedelta(hours=1)
                    )
                    retry_extra["battle_test_provisioning"] = {
                        **retry_state,
                        "status": (
                            BATTLE_TEST_RETRY_WAIT
                            if retries < max_retries
                            else BATTLE_TEST_FAILED
                        ),
                        "last_error": "assessment_task_battle_test_failed",
                        "next_attempt_at": next_attempt.isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    retry_task.extra_data = retry_extra
                    db.commit()
        if retries < max_retries:
            logger.warning(
                "battle-test retry task=%s retry=%s/%s in=%ss: %s",
                task_id,
                retries + 1,
                max_retries,
                countdown,
                exc,
            )
            raise self.retry(exc=exc, countdown=countdown)
        logger.exception("battle_test_generated_task retries exhausted task=%s", task_id)
        return {
            "status": "failed",
            "task_id": int(task_id),
            "reason": "assessment_task_battle_test_failed",
            "retry_after_seconds": 3600,
        }
    finally:
        db.close()
