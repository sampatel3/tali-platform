"""Durable receipt helpers for the legacy-compatible scoring fan-out task."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import secrets
from time import monotonic
from typing import Any, Iterable, Mapping


logger = logging.getLogger(__name__)
_CANCEL_DB_POLL_SECONDS = 1.0
_CANCEL_DB_POLL_ITEMS = 10
# One Workable fetch can include two bounded HTTP calls, object storage, PDF
# extraction, and up to two 120-second metered parsing attempts. Renew before
# each item and keep the lease above that complete worst-case boundary.
_FANOUT_LEASE_SECONDS = 10 * 60
_RECEIPT_QUERY_CHUNK_SIZE = 500


class ScoringBatchLeaseLost(RuntimeError):
    """Raised when an expired fan-out delivery tries to mutate a new owner."""


def _count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _target_ids(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if type(item) is int and item > 0})


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        resolved = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
    except ValueError:
        return None
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


@dataclass
class ScoringBatchProgress:
    role_id: int
    run_id: int | None
    include_scored: bool
    applied_after: str | None
    owner_delivery_id: str | None = None
    total: int = 0
    selected: int = 0
    excluded_by_filter: int = 0
    fetched: int = 0
    fetch_failures: int = 0
    missing_cv: int = 0
    enqueue_skipped: int = 0
    enqueued: int = 0
    pre_screened_out: int = 0
    target_application_ids: list[int] = field(default_factory=list)
    score_job_application_ids: set[int] = field(default_factory=set)
    score_job_ids: set[int] = field(default_factory=set)
    owned_score_job_ids: set[int] = field(default_factory=set)
    _last_cancel_poll: float | None = field(default=None, init=False, repr=False)
    _cancel_checks_since_poll: int = field(default=0, init=False, repr=False)
    _cancel_cached: bool = field(default=False, init=False, repr=False)

    @property
    def not_enqueued(self) -> int:
        return max(0, self.total - len(self.score_job_application_ids))

    def adopt_total(self, run) -> None:
        if run is not None:
            counters = dict(run.counters or {})
            self.total = max(self.total, _count(counters.get("selected_total")))
            for field_name in (
                "selected",
                "excluded_by_filter",
                "fetched",
                "fetch_failures",
                "missing_cv",
                "enqueue_skipped",
                "pre_screened_out",
            ):
                setattr(
                    self,
                    field_name,
                    max(
                        int(getattr(self, field_name)),
                        _count(counters.get(field_name)),
                    ),
                )
            self.add_targets(_target_ids(counters.get("target_application_ids")))
            self.score_job_application_ids.update(
                _target_ids(counters.get("dispatched_application_ids"))
            )
            self.score_job_ids.update(_target_ids(counters.get("score_job_ids")))
            self.owned_score_job_ids.update(
                _target_ids(counters.get("owned_score_job_ids"))
            )
            self.enqueued = len(self.score_job_application_ids)

    def recover_owned_receipts(self, db, run) -> list[object]:
        """Rebuild receipts from atomic job ownership after a hard crash."""

        if run is None:
            return []
        from ..models.cv_score_job import CvScoreJob

        target_ids = _target_ids(self.target_application_ids)
        self.target_application_ids = target_ids
        rows: list[object] = []
        for offset in range(0, len(target_ids), _RECEIPT_QUERY_CHUNK_SIZE):
            rows.extend(
                db.query(CvScoreJob)
                .filter(
                    CvScoreJob.batch_run_id == int(run.id),
                    CvScoreJob.application_id.in_(
                        target_ids[offset : offset + _RECEIPT_QUERY_CHUNK_SIZE]
                    ),
                )
                .order_by(CvScoreJob.application_id, CvScoreJob.id)
                .all()
            )
        for job in rows:
            self.score_job_application_ids.add(int(job.application_id))
            self.score_job_ids.add(int(job.id))
            self.owned_score_job_ids.add(int(job.id))
        self.enqueued = len(self.score_job_application_ids)
        return rows

    def add_targets(self, values: Iterable[object]) -> None:
        targets = set(self.target_application_ids)
        targets.update(value for value in values if type(value) is int and value > 0)
        self.target_application_ids = sorted(targets)
        self.total = max(self.total, len(self.target_application_ids))

    def record_enqueued(self, application_id: int, job: object | None = None) -> None:
        application_id = int(application_id)
        if application_id not in self.score_job_application_ids:
            self.score_job_application_ids.add(application_id)
        if job is not None:
            job_id = getattr(job, "id", None)
            if type(job_id) is int and job_id > 0:
                self.score_job_ids.add(job_id)
                if getattr(job, "batch_run_id", None) == self.run_id:
                    self.owned_score_job_ids.add(job_id)
        self.enqueued = len(self.score_job_application_ids)

    def evidence(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "selected_total": self.total,
            "selected": self.selected,
            "target_application_ids": self.target_application_ids,
            "excluded_by_filter": self.excluded_by_filter,
            "fetched": self.fetched,
            "fetch_failures": self.fetch_failures,
            "missing_cv": self.missing_cv,
            "enqueue_skipped": self.enqueue_skipped,
            "enqueued": self.enqueued,
            "score_job_receipts": len(self.score_job_application_ids),
            "dispatched_application_ids": sorted(self.score_job_application_ids),
            "score_job_ids": sorted(self.score_job_ids),
            "owned_score_job_ids": sorted(self.owned_score_job_ids),
            "not_enqueued": self.not_enqueued,
            "pre_screened_out": self.pre_screened_out,
            "include_scored": bool(self.include_scored),
            "applied_after": self.applied_after,
        }

    def save(self, db, run, state: str, **kwargs) -> None:
        persist_scoring_batch_run(
            db,
            run,
            progress=self.evidence(),
            state=state,
            owner_delivery_id=self.owner_delivery_id,
            **kwargs,
        )

    def cancelled_result(self, db, run, phase: str) -> dict[str, Any]:
        self.save(
            db,
            run,
            f"cancelled_{phase}",
            status="cancelling",
            cancelled=True,
        )
        if self.run_id is None:
            result = {
                "status": "cancelled",
                "role_id": self.role_id,
                "count": self.enqueued,
                "fetched": self.fetched,
                "fetch_failures": self.fetch_failures,
            }
            if phase != "fetch":
                result["pre_screened_out"] = self.pre_screened_out
            return result
        return self.result(status="cancelled")

    def result(self, *, status: str = "enqueued") -> dict[str, Any]:
        result = {
            "status": status,
            "role_id": self.role_id,
            "count": self.enqueued,
            "fetched": self.fetched,
            "fetch_failures": self.fetch_failures,
            "pre_screened_out": self.pre_screened_out,
        }
        if self.run_id is None:
            return result
        result.update(
            total=self.total,
            selected=self.selected,
            missing_cv=self.missing_cv,
            enqueue_skipped=self.enqueue_skipped,
            not_enqueued=self.not_enqueued,
            run_id=self.run_id,
        )
        return result

    def finalize(self, db, run) -> dict[str, Any]:
        if run is None:
            return self.result()
        if run.status == "cancelling" or run.cancel_requested_at is not None:
            return self.cancelled_result(db, run, "after_enqueue")
        if self.total == 0:
            self.save(db, run, "completed", status="completed", finished=True)
            result_status = "completed"
        elif self.enqueued == 0:
            self.save(
                db,
                run,
                "failed_before_dispatch",
                status="failed",
                finished=True,
                error="scoring_batch_nothing_enqueued",
            )
            result_status = "failed"
        else:
            self.save(db, run, "enqueued")
            result_status = "enqueued"
        return self.result(status=result_status)

    def fail(self, db, run) -> None:
        self.recover_owned_receipts(db, run)
        has_active = False
        if run is not None:
            from ..models.cv_score_job import (
                CvScoreJob,
                SCORE_JOB_PENDING,
                SCORE_JOB_RUNNING,
            )

            active_statuses = (SCORE_JOB_PENDING, SCORE_JOB_RUNNING)
            has_active = (
                db.query(CvScoreJob.id)
                .filter(
                    CvScoreJob.batch_run_id == int(run.id),
                    CvScoreJob.status.in_(active_statuses),
                )
                .first()
                is not None
            )
            external_ids = sorted(self.score_job_ids - self.owned_score_job_ids)
            for offset in range(0, len(external_ids), 500):
                if (
                    db.query(CvScoreJob.id)
                    .filter(
                        CvScoreJob.id.in_(external_ids[offset : offset + 500]),
                        CvScoreJob.status.in_(active_statuses),
                    )
                    .first()
                    is not None
                ):
                    has_active = True
                    break
        self.save(
            db,
            run,
            "failed_before_dispatch" if self.enqueued == 0 else "fanout_failed",
            status="running" if has_active else "failed",
            finished=not has_active,
            error="scoring_batch_fanout_failed",
            fanout_failed=True,
        )

    def commit_fetches(self, db, run) -> None:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            if run is not None:
                raise
            logger.error(
                "Failed to commit batch CV fetch results error_type=%s",
                type(exc).__name__,
            )

    def cancel_requested(self, db, run) -> bool:
        if run is None:
            return False
        if self._cancel_cached:
            return True
        now = monotonic()
        self._cancel_checks_since_poll += 1
        if (
            self._last_cancel_poll is not None
            and now - self._last_cancel_poll < _CANCEL_DB_POLL_SECONDS
            and self._cancel_checks_since_poll < _CANCEL_DB_POLL_ITEMS
        ):
            return False
        self._last_cancel_poll = now
        self._cancel_checks_since_poll = 0
        db.refresh(run, attribute_names=["status", "cancel_requested_at"])
        self._cancel_cached = (
            run.status == "cancelling" or run.cancel_requested_at is not None
        )
        return self._cancel_cached


def claim_scoring_batch_run(
    db,
    *,
    run_id: int | None,
    role_id: int,
    organization_id: int | None,
    delivery_id: str | None = None,
    require_exact_target_snapshot: bool = True,
) -> tuple[object | None, dict[str, Any] | None]:
    """Bind one optional receipt and atomically fence duplicate deliveries."""

    if run_id is None:
        return None, None
    if type(run_id) is not int or run_id <= 0:
        return None, {"status": "invalid_run", "role_id": role_id, "run_id": run_id}

    from ..models.background_job_run import (
        JOB_KIND_SCORING_BATCH,
        SCOPE_KIND_ROLE,
        BackgroundJobRun,
    )

    query = db.query(BackgroundJobRun).filter(
        BackgroundJobRun.id == run_id,
        BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
        BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
        BackgroundJobRun.scope_id == int(role_id),
    )
    if organization_id is not None:
        query = query.filter(BackgroundJobRun.organization_id == int(organization_id))
    run = query.with_for_update().one_or_none()
    if run is None:
        return None, {"status": "invalid_run", "role_id": role_id, "run_id": run_id}
    if not isinstance(run.counters, dict):
        run.counters = {
            "fanout_state": "invalid_counters",
            "fanout_complete": True,
        }
        run.status = "failed"
        run.error = "scoring_batch_invalid_counters"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return None, {"status": "invalid_run", "role_id": role_id, "run_id": run_id}
    counters = dict(run.counters)
    raw_targets = counters.get("target_application_ids")
    targets = _target_ids(raw_targets)
    has_exact_target_snapshot = (
        isinstance(raw_targets, list) and bool(raw_targets) and raw_targets == targets
    )
    selected_total = max(
        _count(counters.get("total")),
        _count(counters.get("selected_total")),
        len(targets),
    )
    counters.update(
        total=selected_total,
        selected_total=selected_total,
        target_application_ids=targets,
    )
    if run.finished_at is not None or run.status not in {
        "dispatching",
        "queued",
        "running",
        "cancelling",
    }:
        counters["fanout_complete"] = True
        run.counters = counters
        if run.finished_at is None:
            run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return None, {
            "status": "already_terminal",
            "role_id": role_id,
            "run_id": run_id,
            "run_status": run.status,
        }
    if require_exact_target_snapshot and not has_exact_target_snapshot:
        counters.update(
            fanout_state="invalid_target_snapshot",
            fanout_complete=True,
            target_application_ids=[],
        )
        run.counters = counters
        run.status = "failed"
        run.error = "scoring_batch_invalid_target_snapshot"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return None, {
            "status": "invalid_run",
            "role_id": role_id,
            "run_id": run_id,
            "error": "scoring_batch_invalid_target_snapshot",
        }
    if run.status == "cancelling" or run.cancel_requested_at is not None:
        dispatched = _target_ids(counters.get("dispatched_application_ids"))
        enqueued = len(dispatched)
        claim_token = secrets.token_hex(16)
        counters.update(
            fanout_state="cancelled_before_fetch",
            fanout_complete=True,
            fanout_owner_delivery_id=claim_token,
            fanout_owner_task_id=str(delivery_id or "unknown"),
            not_enqueued=max(0, selected_total - enqueued),
        )
        run.counters = counters
        run.status = "cancelling"
        now = datetime.now(timezone.utc)
        run.cancel_requested_at = run.cancel_requested_at or now
        db.commit()
        return run, {
            "status": "cancelled",
            "role_id": role_id,
            "run_id": run_id,
            "count": enqueued,
            "not_enqueued": max(0, selected_total - enqueued),
        }
    state = str(counters.get("fanout_state") or "")
    if bool(counters.get("fanout_complete")):
        return None, {
            "status": "already_enqueued"
            if state == "enqueued"
            else "duplicate_delivery",
            "role_id": role_id,
            "run_id": run_id,
            "count": _count(counters.get("enqueued")),
        }
    now = datetime.now(timezone.utc)
    lease_expires_at = _datetime(counters.get("fanout_lease_expires_at"))
    if lease_expires_at is not None and lease_expires_at > now:
        return None, {
            "status": "delivery_busy",
            "role_id": role_id,
            "run_id": run_id,
            "retry_after_seconds": max(
                1, int((lease_expires_at - now).total_seconds()) + 1
            ),
        }
    claim_token = secrets.token_hex(16)
    counters.update(
        fanout_state="resuming" if state else "claimed",
        fanout_complete=False,
        fanout_owner_delivery_id=claim_token,
        fanout_owner_task_id=str(delivery_id or "unknown"),
        fanout_heartbeat_at=now.isoformat(),
        fanout_lease_expires_at=(
            now + timedelta(seconds=_FANOUT_LEASE_SECONDS)
        ).isoformat(),
    )
    run.counters = counters
    run.status = "running"
    run.error = None
    db.commit()
    return run, None


def persist_scoring_batch_run(
    db,
    run,
    *,
    progress: Mapping[str, Any],
    state: str,
    status: str | None = None,
    finished: bool = False,
    error: str | None = None,
    cancelled: bool = False,
    fanout_failed: bool = False,
    owner_delivery_id: str | None = None,
) -> None:
    """Merge worker evidence without discarding producer-owned counters."""

    if run is None:
        return
    from ..models.background_job_run import BackgroundJobRun

    fresh_run = (
        db.query(BackgroundJobRun)
        .filter(BackgroundJobRun.id == int(run.id))
        .with_for_update()
        .populate_existing()
        .one()
    )
    counters = dict(fresh_run.counters or {})
    if owner_delivery_id is not None and str(
        counters.get("fanout_owner_delivery_id") or ""
    ) != str(owner_delivery_id):
        raise ScoringBatchLeaseLost(
            f"scoring batch fanout lease lost run_id={fresh_run.id}"
        )
    fanout_complete = state not in {"claimed", "resuming", "fetching", "enqueuing"}
    now = datetime.now(timezone.utc)
    counters.update(
        dict(progress),
        fanout_state=state,
        fanout_complete=fanout_complete,
        fanout_heartbeat_at=now.isoformat(),
        fanout_lease_expires_at=(
            None
            if fanout_complete
            else (now + timedelta(seconds=_FANOUT_LEASE_SECONDS)).isoformat()
        ),
    )
    if fanout_failed:
        counters["fanout_failed"] = True
    fresh_run.counters = counters
    if status is not None:
        fresh_run.status = status
    if error is not None:
        fresh_run.error = error
    if cancelled and fresh_run.cancel_requested_at is None:
        fresh_run.cancel_requested_at = now
    if finished:
        fresh_run.finished_at = now
    db.add(fresh_run)
    db.commit()
    run.counters = dict(fresh_run.counters or {})
    run.status = fresh_run.status
    run.error = fresh_run.error
    run.finished_at = fresh_run.finished_at
    run.cancel_requested_at = fresh_run.cancel_requested_at


__all__ = [
    "ScoringBatchProgress",
    "ScoringBatchLeaseLost",
    "claim_scoring_batch_run",
    "persist_scoring_batch_run",
]
