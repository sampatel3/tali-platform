import logging
from .celery_app import celery_app
from ..platform.config import settings

logger = logging.getLogger(__name__)


# send_assessment_email and send_results_email moved to
# app.components.notifications.tasks (the canonical email-task module).
# Re-export for backwards compatibility with importers that still reference
# them at this path; safe to remove once those imports are migrated.
from ..components.notifications.tasks import (  # noqa: F401
    send_assessment_email,
    send_results_email,
)


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

    log_extra = {"request_id": request_id or self.request.id}
    if not (settings.RESEND_API_KEY or "").strip():
        logger.info(
            "RESEND_API_KEY not set — skipping candidate feedback email to %s",
            candidate_email,
            extra=log_extra,
        )
        return {"success": False, "skipped": True}
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


# Note: ``sync_workable_orgs`` (every-30-min full sync of every job AND
# every candidate AND every CV download) was removed on 2026-05-20. It
# was the source of the constant rate-limiting and the starvation bug
# (``workable_last_sync_at`` debounce starved by ``sync_starred_roles``
# 's writes — see PR #194). Sync is now split per-cadence: jobs every
# 15 min (jobs_only), starred-role candidates every 5 min, agent-mode
# candidates every 5 min, everything else once nightly.

_STARRED_SYNC_LOCK_KEY_PREFIX = "celery:lock:sync_starred_roles"
_STARRED_SYNC_LOCK_TTL_SECONDS = 600  # 10m ceiling — starred sync is filtered, fast


def _acquire_starred_lock(org_id: int):
    """Per-org lock for starred-role sync.

    Independent from the broader sync_workable_orgs lock so a slow
    org-wide sync can't block the 15-min starred cadence.
    """
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        key = f"{_STARRED_SYNC_LOCK_KEY_PREFIX}:{org_id}"
        acquired = client.set(key, "1", nx=True, ex=_STARRED_SYNC_LOCK_TTL_SECONDS)
        if not acquired:
            return None
        return (client, key)
    except Exception:
        logger.exception(
            "Failed to acquire starred-roles sync lock org_id=%s; running unguarded",
            org_id,
        )
        return False


def _release_starred_lock(handle) -> None:
    if not handle:
        return
    try:
        client, key = handle
        client.delete(key)
    except Exception:
        logger.exception("Failed to release starred-roles sync lock")


@celery_app.task
def sync_starred_roles():
    """Periodic task: pull from Workable for orgs with starred roles.

    Filters each org's sync to the workable_job_id of its starred roles,
    so this stays fast (per-job calls) even for orgs with hundreds of
    roles. Auto-scoring of newly created applications happens inside the
    sync path — see sync_service._sync_candidate_for_role, gated on
    role.starred_for_auto_sync.
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
            lock_handle = _acquire_starred_lock(int(org.id))
            if lock_handle is None:
                # Another starred-sync is already running for this org.
                skipped += 1
                continue
            try:
                service = WorkableSyncService(
                    WorkableService(
                        access_token=org.workable_access_token,
                        subdomain=org.workable_subdomain,
                    )
                )
                # mode="full" so the candidate path enters the branch
                # that downloads the CV and calls on_application_created;
                # that's where starred_for_auto_sync gates auto-scoring.
                service.sync_org(
                    db,
                    org,
                    full_resync=False,
                    mode="full",
                    selected_job_shortcodes=shortcodes,
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
                _release_starred_lock(lock_handle)
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

_JOBS_SYNC_LOCK_KEY = "celery:lock:sync_workable_jobs"
_JOBS_SYNC_LOCK_TTL_SECONDS = 600  # 10m ceiling — jobs-only is fast


def _acquire_jobs_sync_lock():
    """Best-effort Redis lock for sync_workable_jobs."""
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        if client.set(_JOBS_SYNC_LOCK_KEY, "1", nx=True, ex=_JOBS_SYNC_LOCK_TTL_SECONDS):
            return client
        return None
    except Exception:
        logger.exception("Failed to acquire jobs-sync lock; running unguarded")
        return False


def _release_jobs_sync_lock(client) -> None:
    if not client:
        return
    try:
        client.delete(_JOBS_SYNC_LOCK_KEY)
    except Exception:
        logger.exception("Failed to release jobs-sync lock")


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

    lock_client = _acquire_jobs_sync_lock()
    if lock_client is None:
        logger.info("sync_workable_jobs skipping: another instance is already running")
        return {"status": "skipped", "reason": "already_running"}

    db: Session = SessionLocal()
    synced = 0
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
            try:
                service = WorkableSyncService(
                    WorkableService(
                        access_token=org.workable_access_token,
                        subdomain=org.workable_subdomain,
                    )
                )
                service.sync_org(db, org, mode="jobs_only")
                synced += 1
            except Exception:
                failed += 1
                logger.exception("Workable jobs-only sync failed for org_id=%s", org.id)
        return {"status": "ok", "synced": synced, "failed": failed}
    finally:
        db.close()
        _release_jobs_sync_lock(lock_client)


_AGENT_MODE_SYNC_LOCK_KEY_PREFIX = "celery:lock:sync_agent_mode_roles"
_AGENT_MODE_SYNC_LOCK_TTL_SECONDS = 600  # 10m ceiling — fast filtered sync


def _acquire_agent_mode_lock(org_id: int):
    """Per-org lock for agent-mode sync."""
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        key = f"{_AGENT_MODE_SYNC_LOCK_KEY_PREFIX}:{org_id}"
        if client.set(key, "1", nx=True, ex=_AGENT_MODE_SYNC_LOCK_TTL_SECONDS):
            return (client, key)
        return None
    except Exception:
        logger.exception(
            "Failed to acquire agent-mode sync lock org_id=%s; running unguarded",
            org_id,
        )
        return False


def _release_agent_mode_lock(handle) -> None:
    if not handle:
        return
    try:
        client, key = handle
        client.delete(key)
    except Exception:
        logger.exception("Failed to release agent-mode sync lock")


@celery_app.task
def sync_agent_mode_roles():
    """Periodic task: pull candidates for roles where ``agentic_mode_enabled``.

    Mirrors sync_starred_roles but filters to roles with the agent loop
    turned on (and not paused). Runs at the same 5-min cadence so the
    agent always sees fresh Workable state. A role that is BOTH starred
    and agentic will be picked up by both tasks — Redis locks prevent
    overlap within a single org, and ``sync_org`` is idempotent across
    repeated upserts.
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
            lock_handle = _acquire_agent_mode_lock(int(org.id))
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
                _release_agent_mode_lock(lock_handle)
        return {"status": "ok", "synced": synced, "skipped": skipped, "failed": failed}
    finally:
        db.close()


_DAILY_CANDIDATE_SYNC_LOCK_KEY_PREFIX = "celery:lock:sync_workable_daily_candidates"
_DAILY_CANDIDATE_SYNC_LOCK_TTL_SECONDS = 7200  # 2h ceiling — nightly catch-all


def _acquire_daily_candidate_lock(org_id: int):
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        key = f"{_DAILY_CANDIDATE_SYNC_LOCK_KEY_PREFIX}:{org_id}"
        if client.set(key, "1", nx=True, ex=_DAILY_CANDIDATE_SYNC_LOCK_TTL_SECONDS):
            return (client, key)
        return None
    except Exception:
        logger.exception(
            "Failed to acquire daily-candidate sync lock org_id=%s; running unguarded",
            org_id,
        )
        return False


def _release_daily_candidate_lock(handle) -> None:
    if not handle:
        return
    try:
        client, key = handle
        client.delete(key)
    except Exception:
        logger.exception("Failed to release daily-candidate sync lock")


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
            lock_handle = _acquire_daily_candidate_lock(int(org.id))
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
                _release_daily_candidate_lock(lock_handle)
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


@celery_app.task
def reap_stuck_workable_sync_runs():
    """Finalize WorkableSyncRun rows whose worker died before the run finished.

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
        threshold = datetime.now(timezone.utc) - timedelta(hours=_STUCK_RUN_TIMEOUT_HOURS)
        stuck = (
            db.query(WorkableSyncRun)
            .filter(
                WorkableSyncRun.status == "running",
                WorkableSyncRun.finished_at.is_(None),
                WorkableSyncRun.started_at < threshold,
            )
            .all()
        )
        if not stuck:
            return {"status": "ok", "reaped": 0}

        now = datetime.now(timezone.utc)
        org_ids: set[int] = set()
        for run in stuck:
            run.status = "failed"
            run.finished_at = now
            run.phase = run.phase or "aborted"
            errors = list(run.errors or [])
            errors.append(
                f"Stuck-run reaper: marked failed after {_STUCK_RUN_TIMEOUT_HOURS}h timeout"
            )
            run.errors = errors
            org_ids.add(int(run.organization_id))

        # Clear matching org sync flags so the next sync trigger isn't
        # confused by stale in-flight state pointing at the dead run.
        if org_ids:
            (
                db.query(Organization)
                .filter(Organization.id.in_(org_ids))
                .update(
                    {
                        Organization.workable_sync_started_at: None,
                        Organization.workable_sync_progress: None,
                        Organization.workable_sync_cancel_requested_at: None,
                    },
                    synchronize_session=False,
                )
            )
        db.commit()
        logger.warning(
            "reap_stuck_workable_sync_runs reaped %d run(s) across %d org(s): run_ids=%s",
            len(stuck),
            len(org_ids),
            [r.id for r in stuck],
        )
        return {"status": "ok", "reaped": len(stuck), "org_ids": sorted(org_ids)}
    finally:
        db.close()
