import logging
import threading
import time
import uuid

from .celery_app import celery_app
from ..platform.config import settings

logger = logging.getLogger(__name__)


# Email tasks (send_assessment_email / send_results_email) live in
# app.components.notifications.tasks. Do NOT re-export them here: that module
# imports celery_app from this package, so a top-level back-import creates a
# circular import that breaks request-time email dispatch (the importer hits
# a partially-initialized notifications.tasks). Import them from the canonical
# module instead.


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

# Single per-org mutex shared by all four Workable sync tasks AND every
# interactive Workable write (decision approve / override / move / note).
# Two callers touching the same Workable token at the same time used to
# share-rate-limit each other into 429s (each ``sync_org`` calls
# ``list_open_jobs`` which fires 5 endpoint hits, and per-candidate
# prefetches fan out further). A single mutex means only one caller is
# talking to Workable for a given org at a time. If a sync task can't get
# the lock it skips that fire — the next Beat tick (5-15 min away) retries.
_WORKABLE_ORG_MUTEX_KEY_PREFIX = "celery:lock:workable_org_sync"
# Short TTL so a worker that dies mid-hold (deploy SIGKILL, OOM, container
# restart) can only leak the lock for ~TTL, not the old 30-min ceiling.
# This is safe *only* because a live holder keeps its lock alive past the
# TTL via the heartbeat below — long-but-healthy syncs are unaffected
# while a dead holder's lock self-heals within ~TTL.
_WORKABLE_ORG_MUTEX_TTL_SECONDS = 90
# Heartbeat cadence. Comfortably under the TTL (3x margin) so a couple of
# delayed refreshes (GIL contention, slow Redis) can't drop a live lock.
_WORKABLE_ORG_MUTEX_HEARTBEAT_SECONDS = 30
# A blocked interactive write sets this sibling key to ask a sync that is
# holding the mutex to yield. TTL > the op's max retry gap so it survives
# between an op's retries, but auto-clears within a minute if the waiter
# vanishes (worker died, recruiter navigated away).
_WORKABLE_ORG_MUTEX_WAITER_TTL_SECONDS = 60
# Cap on how long a yielding sync keeps deferring to interactive waiters
# before it forces its way back in, so sync always makes progress even
# under sustained approval load.
_WORKABLE_ORG_MUTEX_YIELD_MAX_WAIT_SECONDS = 300
# Poll cadence while a yielded sync waits for the interactive op to finish.
_WORKABLE_ORG_MUTEX_YIELD_POLL_SECONDS = 1.0


def _waiter_key(key: str) -> str:
    return f"{key}:waiters"


def _as_str(value) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _compare_and_delete(client, key: str, token: str) -> None:
    """Delete ``key`` only while it still holds ``token`` (atomic WATCH/MULTI).

    Guards against deleting a lock that another caller acquired after ours
    expired — e.g. our heartbeat stalled, the TTL lapsed, someone else
    grabbed it, and only then did we reach release.
    """

    def _txn(pipe):
        current = _as_str(pipe.get(key))
        pipe.multi()
        if current == token:
            pipe.delete(key)

    client.transaction(_txn, key)


def _compare_and_extend(client, key: str, token: str, ttl: int) -> None:
    """Refresh ``key``'s TTL only while it still holds ``token`` (atomic)."""

    def _txn(pipe):
        current = _as_str(pipe.get(key))
        pipe.multi()
        if current == token:
            pipe.expire(key, ttl)

    client.transaction(_txn, key)


class _OrgMutex:
    """Refreshable per-org Workable lock.

    The value is a unique token so we only ever extend/delete a lock we
    still own. A daemon heartbeat extends the TTL while the holder is
    alive; if the holder dies the heartbeat stops with it and the short
    TTL lets the lock self-heal within ~TTL instead of leaking for 30 min.
    """

    def __init__(self, client, key: str, source: str):
        self.client = client
        self.key = key
        self.source = source
        self.token = ""
        self._stop = threading.Event()
        self._hb: threading.Thread | None = None

    def _start_heartbeat(self) -> None:
        self._stop = threading.Event()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"workable-mutex-hb:{self.key}",
            daemon=True,
        )
        self._hb = thread
        thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(_WORKABLE_ORG_MUTEX_HEARTBEAT_SECONDS):
            try:
                _compare_and_extend(
                    self.client, self.key, self.token, _WORKABLE_ORG_MUTEX_TTL_SECONDS
                )
            except Exception:
                logger.exception("Workable mutex heartbeat failed key=%s", self.key)

    def _stop_heartbeat(self) -> None:
        self._stop.set()
        thread = self._hb
        self._hb = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def acquire(self) -> bool:
        """Try to take the lock once (non-blocking). Starts the heartbeat
        on success; ``False`` means it's held by someone else."""
        token = f"{self.source}:{uuid.uuid4().hex}"
        if self.client.set(self.key, token, nx=True, ex=_WORKABLE_ORG_MUTEX_TTL_SECONDS):
            self.token = token
            self._start_heartbeat()
            return True
        return False

    def release(self) -> None:
        self._stop_heartbeat()
        if self.token:
            try:
                _compare_and_delete(self.client, self.key, self.token)
            except Exception:
                logger.exception("Failed to release workable-org mutex key=%s", self.key)
        self.token = ""

    def waiter_present(self) -> bool:
        try:
            return bool(self.client.exists(_waiter_key(self.key)))
        except Exception:
            return False


def _acquire_workable_org_mutex(org_id: int, *, source: str, signal_waiter: bool = False):
    """Acquire the per-org Workable mutex shared across all sync tasks.

    ``source`` is a short label (``"jobs"`` / ``"starred"`` / ``"agent"``
    / ``"nightly"`` / ``"workable_op:*"``) embedded in the lock token so we
    can see in Redis which caller is holding the lock when debugging.

    ``signal_waiter`` is set by interactive writes: when they're blocked
    they flag a sibling key so a long-running sync holding the lock yields
    (see ``_yield_workable_org_mutex_if_waiter``).

    Returns the ``_OrgMutex`` handle on success, ``None`` if held by
    another caller, ``False`` on Redis failure (caller runs unguarded).
    """
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.REDIS_URL)
        key = f"{_WORKABLE_ORG_MUTEX_KEY_PREFIX}:{org_id}"
        mutex = _OrgMutex(client, key, source)
        if mutex.acquire():
            # We hold it now — clear any waiter flag a prior blocked op left.
            try:
                client.delete(_waiter_key(key))
            except Exception:
                pass
            return mutex
        if signal_waiter:
            # Ask the current holder (a sync) to yield at its next checkpoint.
            try:
                client.set(
                    _waiter_key(key), source, ex=_WORKABLE_ORG_MUTEX_WAITER_TTL_SECONDS
                )
            except Exception:
                pass
        return None
    except Exception:
        logger.exception(
            "Failed to acquire workable-org mutex org_id=%s source=%s; running unguarded",
            org_id,
            source,
        )
        return False


def _release_workable_org_mutex(handle) -> None:
    if not isinstance(handle, _OrgMutex):
        return
    handle.release()


def _yield_workable_org_mutex_if_waiter(handle, org_id: int) -> None:
    """Cooperative preemption: if an interactive write is waiting, release
    the mutex, let the op drain, then re-acquire and continue.

    Called by the long-running sync tasks at their job/candidate
    checkpoints. A read-heavy sync would otherwise hold the global write
    mutex for its whole run (observed 1h10m) and starve decision approvals.
    Bounded by ``_WORKABLE_ORG_MUTEX_YIELD_MAX_WAIT_SECONDS`` so sync still
    makes progress under sustained approval load.
    """
    if not isinstance(handle, _OrgMutex):
        return
    if not handle.waiter_present():
        return
    logger.info(
        "Yielding Workable mutex to interactive waiter org_id=%s holder=%s",
        org_id,
        handle.source,
    )
    handle.release()
    deadline = time.monotonic() + _WORKABLE_ORG_MUTEX_YIELD_MAX_WAIT_SECONDS
    while True:
        # While a waiter is still pending and we're within budget, keep the
        # lock free so the interactive op wins it. Past the deadline (or
        # once no waiter remains), grab it back as soon as it's free.
        deferring = handle.waiter_present() and time.monotonic() < deadline
        if not deferring and handle.acquire():
            return
        time.sleep(_WORKABLE_ORG_MUTEX_YIELD_POLL_SECONDS)


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
            lock_handle = _acquire_workable_org_mutex(int(org.id), source="starred")
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
                # mode="full" so the candidate path enters the branch
                # that downloads the CV and calls on_application_created;
                # that's where starred_for_auto_sync gates auto-scoring.
                service.sync_org(
                    db,
                    org,
                    full_resync=False,
                    mode="full",
                    selected_job_shortcodes=shortcodes,
                    yield_if_contended=lambda h=lock_handle, oid=int(org.id): (
                        _yield_workable_org_mutex_if_waiter(h, oid)
                    ),
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
            lock_handle = _acquire_workable_org_mutex(int(org.id), source="jobs")
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
                    yield_if_contended=lambda h=lock_handle, oid=int(org.id): (
                        _yield_workable_org_mutex_if_waiter(h, oid)
                    ),
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
            lock_handle = _acquire_workable_org_mutex(int(org.id), source="agent")
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
                    yield_if_contended=lambda h=lock_handle, oid=int(org.id): (
                        _yield_workable_org_mutex_if_waiter(h, oid)
                    ),
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
            lock_handle = _acquire_workable_org_mutex(int(org.id), source="nightly")
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
                    yield_if_contended=lambda h=lock_handle, oid=int(org.id): (
                        _yield_workable_org_mutex_if_waiter(h, oid)
                    ),
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

        now = datetime.now(timezone.utc)
        org_ids_from_runs: set[int] = set()
        for run in stuck:
            run.status = "failed"
            run.finished_at = now
            run.phase = run.phase or "aborted"
            errors = list(run.errors or [])
            errors.append(
                f"Stuck-run reaper: marked failed after {_STUCK_RUN_TIMEOUT_HOURS}h timeout"
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
