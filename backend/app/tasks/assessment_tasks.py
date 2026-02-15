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
            workflow_mode = str(config.get("workflow_mode") or "manual")
            sync_model = str(config.get("sync_model") or "scheduled_pull_only")
            try:
                sync_interval_minutes = int(config.get("sync_interval_minutes") or 30)
            except Exception:
                sync_interval_minutes = 30
            if workflow_mode != "workable_hybrid" or sync_model != "scheduled_pull_only":
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
