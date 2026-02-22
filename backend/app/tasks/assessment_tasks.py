import logging
from .celery_app import celery_app
from ..platform.config import settings

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_assessment_email(self, candidate_email: str, candidate_name: str, token: str, org_name: str, position: str, assessment_id: int | None = None, request_id: str | None = None):
    """Send assessment invitation email to candidate."""
    from ..domains.integrations_notifications.adapters import build_email_adapter

    try:
        email_svc = build_email_adapter()
        result = email_svc.send_assessment_invite(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            token=token,
            assessment_id=assessment_id,
            org_name=org_name,
            position=position,
            frontend_url=settings.FRONTEND_URL,
        )
        if not result["success"]:
            raise Exception(result.get("error", "Email send failed"))
        logger.info(f"Assessment email sent to {candidate_email}", extra={"request_id": request_id or self.request.id})
        return result
    except Exception as exc:
        logger.error(f"Failed to send assessment email to {candidate_email}: {exc}", extra={"request_id": request_id or self.request.id})
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_results_email(self, user_email: str, candidate_name: str, score: float, assessment_id: int, request_id: str | None = None):
    """Notify hiring manager that assessment is complete."""
    from ..domains.integrations_notifications.adapters import build_email_adapter

    try:
        email_svc = build_email_adapter()
        result = email_svc.send_results_notification(
            user_email=user_email,
            candidate_name=candidate_name,
            score=score,
            assessment_id=assessment_id,
            frontend_url=settings.FRONTEND_URL,
        )
        if not result["success"]:
            raise Exception(result.get("error", "Email send failed"))
        logger.info(f"Results email sent to {user_email} for assessment {assessment_id}", extra={"request_id": request_id or self.request.id})
        return result
    except Exception as exc:
        logger.error(f"Failed to send results email: {exc}", extra={"request_id": request_id or self.request.id})
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_candidate_feedback_ready_email(
    self,
    candidate_email: str,
    candidate_name: str,
    org_name: str,
    role_title: str,
    feedback_link: str,
    request_id: str | None = None,
):
    """Notify candidate that their feedback report is ready."""
    from ..domains.integrations_notifications.adapters import build_email_adapter

    try:
        email_svc = build_email_adapter()
        result = email_svc.send_candidate_feedback_ready(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            org_name=org_name,
            role_title=role_title,
            feedback_link=feedback_link,
        )
        if not result["success"]:
            raise Exception(result.get("error", "Email send failed"))
        logger.info(
            "Candidate feedback email sent to %s",
            candidate_email,
            extra={"request_id": request_id or self.request.id},
        )
        return result
    except Exception as exc:
        logger.error(
            "Failed to send candidate feedback email to %s: %s",
            candidate_email,
            exc,
            extra={"request_id": request_id or self.request.id},
        )
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=120)
def post_results_to_workable(self, access_token: str, subdomain: str, candidate_id: str, assessment_data: dict, request_id: str | None = None):
    """Post assessment results to Workable candidate profile."""
    from ..domains.integrations_notifications.adapters import build_workable_adapter

    try:
        workable_svc = build_workable_adapter(access_token=access_token, subdomain=subdomain)
        result = workable_svc.post_assessment_result(candidate_id=candidate_id, assessment_data=assessment_data)
        if not result["success"]:
            raise Exception(result.get("error", "Workable post failed"))
        logger.info(f"Results posted to Workable for candidate {candidate_id}", extra={"request_id": request_id or self.request.id})
        return result
    except Exception as exc:
        logger.error(f"Failed to post to Workable: {exc}", extra={"request_id": request_id or self.request.id})
        raise self.retry(exc=exc)


@celery_app.task
def cleanup_expired_assessments():
    """Periodic task: expire old pending assessments and close abandoned sandboxes."""
    from datetime import datetime, timedelta, timezone
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

        # Close abandoned in-progress sandboxes (over 2 hours)
        stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        stale = db.query(Assessment).filter(
            Assessment.status == AssessmentStatus.IN_PROGRESS,
            Assessment.started_at < stale_cutoff,
        ).all()

        for assessment in stale:
            assessment.status = AssessmentStatus.EXPIRED
            # E2B sandboxes auto-expire, but we mark assessments locally
            count += 1

        db.commit()
        logger.info(f"Cleaned up {count} expired/stale assessments")
    except Exception as e:
        logger.error(f"Cleanup task failed: {e}")
        db.rollback()
    finally:
        db.close()


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
def sync_workable_orgs():
    """Periodic task: sync Workable jobs/candidates for hybrid-workflow orgs."""
    from datetime import datetime, timedelta, timezone
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
            config = org.workable_config if isinstance(org.workable_config, dict) else {}
            sync_model = str(config.get("sync_model") or "scheduled_pull_only")
            try:
                sync_interval_minutes = int(config.get("sync_interval_minutes") or 30)
            except Exception:
                sync_interval_minutes = 30
            if sync_model != "scheduled_pull_only":
                skipped += 1
                continue
            last_sync = getattr(org, "workable_last_sync_at", None)
            if last_sync is not None:
                effective_last_sync = last_sync if last_sync.tzinfo else last_sync.replace(tzinfo=timezone.utc)
                if effective_last_sync >= datetime.now(timezone.utc) - timedelta(minutes=max(sync_interval_minutes, 5)):
                    skipped += 1
                    continue
            try:
                service = WorkableSyncService(
                    WorkableService(
                        access_token=org.workable_access_token,
                        subdomain=org.workable_subdomain,
                    )
                )
                service.sync_org(db, org, full_resync=False)
                synced += 1
            except Exception:
                failed += 1
                logger.exception("Workable sync task failed for org_id=%s", org.id)
        return {"status": "ok", "synced": synced, "skipped": skipped, "failed": failed}
    finally:
        db.close()
