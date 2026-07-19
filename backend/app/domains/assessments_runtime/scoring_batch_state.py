"""Pure state projection and durable discovery for scoring batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from ...models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from ...models.cv_score_job import (
    SCORE_JOB_DONE,
    SCORE_JOB_ERROR,
    SCORE_JOB_PENDING,
    SCORE_JOB_RUNNING,
    CvScoreJob,
)
from ...services.scoring_batch_successors import QUEUE_CONTRACT, successor_payload
from .progress_retention import RECENT_TERMINAL_PROGRESS_TTL


SCORING_ACTIVE_RUN_STATUSES = ("dispatching", "queued", "running", "cancelling")
SCORING_VISIBLE_RUN_STATUSES = (
    *SCORING_ACTIVE_RUN_STATUSES,
    "completed",
    "cancelled",
    "failed",
)
SCORING_FANOUT_STALE_AFTER = timedelta(hours=2)
SCORING_START_ADVISORY_NAMESPACE = 0x54414C49
SCORING_RECEIPT_CHUNK_SIZE = 500
SCORING_DURABLE_QUEUE_CONTRACT = QUEUE_CONTRACT


@dataclass(frozen=True)
class ScoringBatchExactTerminalBreakdown:
    """Application identities behind one exact scoring receipt projection."""

    scored_application_ids: frozenset[int]
    error_application_ids: frozenset[int]
    pre_screened_out_application_ids: frozenset[int]
    cancelled_application_ids: frozenset[int]
    active_application_ids: frozenset[int]

    @property
    def terminal_application_ids(self) -> frozenset[int]:
        return frozenset().union(
            self.scored_application_ids,
            self.error_application_ids,
            self.pre_screened_out_application_ids,
            self.cancelled_application_ids,
        )

    @property
    def scored(self) -> int:
        return len(self.scored_application_ids)

    @property
    def errors(self) -> int:
        return len(self.error_application_ids)

    @property
    def pre_screened_out(self) -> int:
        return len(self.pre_screened_out_application_ids)

    @property
    def cancelled(self) -> int:
        return len(self.cancelled_application_ids)


def lock_scoring_start_scope(db: Session, role_id: int) -> None:
    """Serialize paid starts for one role for the request transaction."""

    get_bind = getattr(db, "get_bind", None)
    if not callable(get_bind):
        return
    bind = get_bind()
    if bind.dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :role_id)"),
        {
            "namespace": SCORING_START_ADVISORY_NAMESPACE,
            "role_id": int(role_id),
        },
    )


def progress_count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def progress_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        resolved = value
    elif isinstance(value, str):
        try:
            resolved = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


def progress_run_id(progress: dict[str, Any]) -> int:
    return progress_count(progress.get("run_id"))


def progress_application_ids(
    progress: dict[str, Any],
) -> tuple[int, ...] | None:
    raw = progress.get("target_application_ids")
    if not isinstance(raw, (list, tuple)):
        return None
    return tuple(sorted({value for value in raw if type(value) is int and value > 0}))


def progress_id_receipts(
    progress: dict[str, Any],
    key: str,
) -> tuple[int, ...]:
    """Return one type-safe, de-duplicated durable receipt list."""

    raw = progress.get(key)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(sorted({value for value in raw if type(value) is int and value > 0}))


def scoring_uses_exact_receipts(progress: dict[str, Any]) -> bool:
    """Whether this run was created under the exact CvScoreJob contract."""

    return all(
        key in progress
        for key in (
            "dispatched_application_ids",
            "score_job_ids",
            "owned_score_job_ids",
        )
    )


def scoring_uses_durable_queue(progress: dict[str, Any]) -> bool:
    return bool(
        progress.get("queue_contract") == SCORING_DURABLE_QUEUE_CONTRACT
        or successor_payload(progress.get("queued_successor")) is not None
    )


def _chunks(values: tuple[int, ...]):
    for offset in range(0, len(values), SCORING_RECEIPT_CHUNK_SIZE):
        yield values[offset : offset + SCORING_RECEIPT_CHUNK_SIZE]


def scoring_batch_has_active_jobs(
    db: Session,
    *,
    run_id: int,
    progress: dict[str, Any],
) -> bool:
    """Return whether an owned or explicitly associated score job is active.

    Owned jobs are selected by ``batch_run_id`` so a crash between INSERT and
    counter persistence cannot hide them. Reused attempts remain unowned and
    are selected only by exact durable IDs. Receipt lists are chunked so large
    roles never exceed database bind limits.
    """

    if type(run_id) is not int or run_id <= 0:
        return False
    if not callable(getattr(db, "query", None)):
        return False
    target_ids = progress_application_ids(progress)
    associated_ids = progress_id_receipts(progress, "score_job_ids")
    if target_ids is None:
        for receipt_ids in _chunks(associated_ids):
            if (
                db.query(CvScoreJob.id)
                .filter(
                    CvScoreJob.id.in_(receipt_ids),
                    CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
                )
                .first()
                is not None
            ):
                return True
        return False
    if not target_ids:
        return False
    active_statuses = (SCORE_JOB_PENDING, SCORE_JOB_RUNNING)
    for application_ids in _chunks(target_ids):
        if (
            db.query(CvScoreJob.id)
            .filter(
                CvScoreJob.batch_run_id == int(run_id),
                CvScoreJob.application_id.in_(application_ids),
                CvScoreJob.status.in_(active_statuses),
            )
            .first()
            is not None
        ):
            return True

    target_id_set = set(target_ids)
    for receipt_ids in _chunks(associated_ids):
        rows = (
            db.query(CvScoreJob.application_id)
            .filter(
                CvScoreJob.id.in_(receipt_ids),
                CvScoreJob.status.in_(active_statuses),
                or_(
                    CvScoreJob.batch_run_id.is_(None),
                    CvScoreJob.batch_run_id != int(run_id),
                ),
            )
            .all()
        )
        if any(type(row[0]) is int and row[0] in target_id_set for row in rows):
            return True
    return False


def scoring_batch_exact_terminal_breakdown(
    db: Session,
    *,
    run_id: int,
    progress: dict[str, Any],
) -> ScoringBatchExactTerminalBreakdown:
    """Count exact terminal jobs, separating recruiter-cancelled attempts."""

    if type(run_id) is not int or run_id <= 0:
        return ScoringBatchExactTerminalBreakdown(
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
        )
    associated_ids = progress_id_receipts(progress, "score_job_ids")
    latest_by_application: dict[int, tuple[int, str, str | None, str | None]] = {}

    def _adopt(rows) -> None:
        for application_id, job_id, status, cache_hit, error_message in rows:
            application_id = int(application_id)
            job_id = int(job_id)
            current = latest_by_application.get(application_id)
            if current is None or job_id > current[0]:
                latest_by_application[application_id] = (
                    job_id,
                    str(status),
                    cache_hit,
                    error_message,
                )

    _adopt(
        db.query(
            CvScoreJob.application_id,
            CvScoreJob.id,
            CvScoreJob.status,
            CvScoreJob.cache_hit,
            CvScoreJob.error_message,
        )
        .filter(CvScoreJob.batch_run_id == int(run_id))
        .all()
    )
    for receipt_ids in _chunks(associated_ids):
        _adopt(
            db.query(
                CvScoreJob.application_id,
                CvScoreJob.id,
                CvScoreJob.status,
                CvScoreJob.cache_hit,
                CvScoreJob.error_message,
            )
            .filter(
                CvScoreJob.id.in_(receipt_ids),
                # The ownership query above already counted these rows.
                or_(
                    CvScoreJob.batch_run_id.is_(None),
                    CvScoreJob.batch_run_id != int(run_id),
                ),
            )
            .all()
        )
    scored_application_ids: set[int] = set()
    error_application_ids: set[int] = set()
    pre_screened_out_application_ids: set[int] = set()
    cancelled_application_ids: set[int] = set()
    active_application_ids: set[int] = set()
    for application_id, (
        _job_id,
        status,
        cache_hit,
        error_message,
    ) in latest_by_application.items():
        if status in (SCORE_JOB_PENDING, SCORE_JOB_RUNNING):
            active_application_ids.add(application_id)
        elif status == SCORE_JOB_ERROR:
            if str(error_message or "") == "cancelled_by_recruiter":
                cancelled_application_ids.add(application_id)
            else:
                error_application_ids.add(application_id)
        elif status == SCORE_JOB_DONE and cache_hit == "pre_screen_filtered":
            pre_screened_out_application_ids.add(application_id)
        elif status == SCORE_JOB_DONE:
            scored_application_ids.add(application_id)
    return ScoringBatchExactTerminalBreakdown(
        scored_application_ids=frozenset(scored_application_ids),
        error_application_ids=frozenset(error_application_ids),
        pre_screened_out_application_ids=frozenset(pre_screened_out_application_ids),
        cancelled_application_ids=frozenset(cancelled_application_ids),
        active_application_ids=frozenset(active_application_ids),
    )


def scoring_batch_exact_terminal_counts(
    db: Session,
    *,
    run_id: int,
    progress: dict[str, Any],
) -> tuple[int, int, int]:
    """Count exact terminals while preserving the historical error total."""

    breakdown = scoring_batch_exact_terminal_breakdown(
        db,
        run_id=run_id,
        progress=progress,
    )
    return (
        breakdown.scored,
        breakdown.errors + breakdown.cancelled,
        breakdown.pre_screened_out,
    )


def scoring_progress_from_run(run: BackgroundJobRun) -> dict[str, Any]:
    counters = dict(run.counters) if isinstance(run.counters, dict) else {}
    target_ids = progress_application_ids(counters) or ()
    total = max(
        progress_count(counters.get("selected_total")),
        progress_count(counters.get("total")),
        len(target_ids),
    )
    return {
        **counters,
        "total": total,
        "scored": progress_count(counters.get("scored")),
        "errors": progress_count(counters.get("errors")),
        "pre_screened_out": progress_count(counters.get("pre_screened_out")),
        "include_scored": bool(counters.get("include_scored")),
        "status": str(run.status or "running"),
        "started_at": run.started_at,
        "terminal_at": run.finished_at,
        "organization_id": int(run.organization_id),
        "run_id": int(run.id),
    }


def merge_scoring_progress(
    local: dict[str, Any] | None,
    run: BackgroundJobRun | None,
) -> dict[str, Any]:
    """Choose the newest identity while treating durable lifecycle as canonical."""

    local_progress = dict(local or {})
    if run is None:
        return local_progress
    durable = scoring_progress_from_run(run)
    local_run_id = progress_run_id(local_progress)
    durable_run_id = progress_run_id(durable)
    if local_run_id > durable_run_id:
        return local_progress
    if local_run_id < durable_run_id:
        return durable

    durable_status = str(durable.get("status") or "")
    if durable_status in {"completed", "cancelled", "failed"}:
        return {**local_progress, **durable}

    merged = {**durable, **local_progress}
    merged["status"] = durable_status
    merged["terminal_at"] = durable.get("terminal_at")
    if durable.get("selected_total") is not None:
        merged["total"] = durable["total"]
    return merged


def scoring_fanout_abandoned(
    progress: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    if str(progress.get("status") or "") not in SCORING_ACTIVE_RUN_STATUSES:
        return False
    if progress.get("fanout_complete") is True:
        return False
    observed_at = progress_datetime(now) or datetime.now(timezone.utc)
    lease_expires_at = progress_datetime(progress.get("fanout_lease_expires_at"))
    if lease_expires_at is not None and lease_expires_at > observed_at:
        return False

    activity = tuple(
        timestamp
        for timestamp in (
            progress_datetime(progress.get("started_at")),
            progress_datetime(progress.get("fanout_heartbeat_at")),
            progress_datetime(progress.get("fanout_last_published_at")),
        )
        if timestamp is not None
    )
    if not activity:
        return False
    return observed_at - max(activity) >= SCORING_FANOUT_STALE_AFTER


def latest_scoring_run(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
) -> BackgroundJobRun | None:
    return (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.scope_id == int(role_id),
            BackgroundJobRun.organization_id == int(organization_id),
            BackgroundJobRun.status.in_(SCORING_VISIBLE_RUN_STATUSES),
        )
        .order_by(BackgroundJobRun.id.desc())
        .first()
    )


def latest_scoring_backfill_run(
    db: Session,
    *,
    organization_id: int,
) -> BackgroundJobRun | None:
    """Return the durable parent receipt for the latest cross-role backfill."""

    return (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
            BackgroundJobRun.scope_kind == "org",
            BackgroundJobRun.scope_id == int(organization_id),
            BackgroundJobRun.organization_id == int(organization_id),
        )
        .order_by(BackgroundJobRun.id.desc())
        .first()
    )


def recent_scoring_runs(
    db: Session,
    *,
    organization_id: int,
    now: datetime | None = None,
    limit: int = 500,
) -> list[BackgroundJobRun]:
    """Return at most one latest visible run per role for an organization."""

    cutoff = (now or datetime.now(timezone.utc)) - RECENT_TERMINAL_PROGRESS_TTL
    latest_id_by_role = (
        db.query(func.max(BackgroundJobRun.id).label("run_id"))
        .filter(
            BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.organization_id == int(organization_id),
        )
        .group_by(BackgroundJobRun.scope_id)
        .subquery()
    )
    return (
        db.query(BackgroundJobRun)
        .join(
            latest_id_by_role,
            BackgroundJobRun.id == latest_id_by_role.c.run_id,
        )
        .filter(
            BackgroundJobRun.status.in_(SCORING_VISIBLE_RUN_STATUSES),
            or_(
                BackgroundJobRun.finished_at.is_(None),
                BackgroundJobRun.finished_at >= cutoff,
            ),
        )
        .order_by(BackgroundJobRun.id.desc())
        .limit(max(1, min(int(limit), 2_000)))
        .all()
    )
