"""Durable, automatic recovery for incomplete assessment rubric grading.

The assessment row is the outbox: ``scoring_partial``/``scoring_failed`` keep
the result non-authoritative, while ``score_breakdown.rubric_grading.retry``
holds a lightweight worker lease and retry audit.  A direct post-commit kick
keeps latency low; the Beat sweep recovers a lost broker message or worker.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_

from .celery_app import celery_app

logger = logging.getLogger("taali.assessments.rubric_retry")

_LEASE_MINUTES = 60
_MAX_BACKOFF_MINUTES = 360


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _rubric_payload(assessment: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    breakdown = (
        dict(assessment.score_breakdown)
        if isinstance(getattr(assessment, "score_breakdown", None), dict)
        else {}
    )
    rubric = (
        dict(breakdown.get("rubric_grading"))
        if isinstance(breakdown.get("rubric_grading"), dict)
        else {}
    )
    return breakdown, rubric


def _set_retry(assessment: Any, **updates: Any) -> None:
    breakdown, rubric = _rubric_payload(assessment)
    retry = (
        dict(rubric.get("retry"))
        if isinstance(rubric.get("retry"), dict)
        else {}
    )
    retry.update(updates)
    rubric["retry"] = retry
    breakdown["rubric_grading"] = rubric
    assessment.score_breakdown = breakdown


def _retry_due(assessment: Any, *, now: datetime) -> bool:
    _, rubric = _rubric_payload(assessment)
    retry = rubric.get("retry") if isinstance(rubric.get("retry"), dict) else {}
    status = str(retry.get("status") or "pending")
    if status == "running":
        claimed_at = _parse_dt(retry.get("claimed_at"))
        return claimed_at is None or claimed_at <= now - timedelta(minutes=_LEASE_MINUTES)
    next_attempt_at = _parse_dt(retry.get("next_attempt_at"))
    return next_attempt_at is None or next_attempt_at <= now


@celery_app.task(
    name="app.tasks.rubric_retry_tasks.retry_incomplete_rubric_scoring",
    queue="scoring",
    acks_late=True,
)
def retry_incomplete_rubric_scoring(assessment_id: int) -> dict[str, Any]:
    """Claim and re-run scoring for one incomplete completed assessment."""
    from ..components.assessments.repository import append_assessment_timeline_event
    from ..components.assessments.service import (
        resume_code_for_assessment,
        submit_assessment,
    )
    from ..domains.assessments_runtime.role_support import refresh_application_score_cache
    from ..models.assessment import Assessment, AssessmentStatus
    from ..models.task import Task
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        assessment = (
            db.query(Assessment)
            .filter(Assessment.id == int(assessment_id))
            .with_for_update()
            .one_or_none()
        )
        if assessment is None:
            return {"status": "skipped", "reason": "not_found", "assessment_id": int(assessment_id)}
        if assessment.status not in {
            AssessmentStatus.COMPLETED,
            AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
        }:
            return {"status": "skipped", "reason": "not_completed", "assessment_id": int(assessment_id)}
        if not (
            bool(getattr(assessment, "scoring_partial", False))
            or bool(getattr(assessment, "scoring_failed", False))
        ):
            return {"status": "skipped", "reason": "already_complete", "assessment_id": int(assessment_id)}

        now = _utcnow()
        _, rubric = _rubric_payload(assessment)
        retry = rubric.get("retry") if isinstance(rubric.get("retry"), dict) else {}
        if str(retry.get("status") or "") == "running" and not _retry_due(assessment, now=now):
            return {"status": "skipped", "reason": "leased", "assessment_id": int(assessment_id)}

        attempt_count = max(0, int(retry.get("attempt_count") or 0)) + 1
        _set_retry(
            assessment,
            status="running",
            attempt_count=attempt_count,
            claimed_at=now.isoformat(),
            last_error=None,
        )
        append_assessment_timeline_event(
            assessment,
            "rubric_grading_retry_started",
            {"attempt_count": attempt_count},
        )
        if getattr(assessment, "application", None) is not None:
            # Clears any legacy stale score cache before the provider call.
            from ..services.related_role_application_runtime import (
                assessment_uses_related_role_pipeline,
            )

            if not assessment_uses_related_role_pipeline(db, assessment):
                refresh_application_score_cache(assessment.application, db=db)
        db.commit()

        task_row = db.query(Task).filter(Task.id == assessment.task_id).one_or_none()
        if task_row is None:
            raise RuntimeError("assessment task not found")
        final_code = resume_code_for_assessment(
            assessment,
            str(getattr(task_row, "starter_code", "") or ""),
        )
        result = submit_assessment(
            assessment,
            final_code,
            int(assessment.tab_switch_count or 0),
            db,
            retry_scoring=True,
            # The original incomplete submission emitted no completion side
            # effects. The first fully-graded retry may emit them exactly once.
            suppress_completion_side_effects=False,
            wake_agent_on_commit=True,
        )
        db.refresh(assessment)
        if result.get("grading_status") == "pending":
            delay = min(_MAX_BACKOFF_MINUTES, max(1, 2 ** min(attempt_count, 8)))
            _set_retry(
                assessment,
                status="error",
                claimed_at=None,
                next_attempt_at=(_utcnow() + timedelta(minutes=delay)).isoformat(),
                last_error="rubric_grading_incomplete",
            )
            append_assessment_timeline_event(
                assessment,
                "rubric_grading_retry_incomplete",
                {"attempt_count": attempt_count, "retry_in_minutes": delay},
            )
            db.commit()
            return {
                "status": "pending",
                "assessment_id": int(assessment.id),
                "attempt_count": attempt_count,
                "retry_in_minutes": delay,
            }

        _set_retry(
            assessment,
            status="complete",
            claimed_at=None,
            next_attempt_at=None,
            completed_at=_utcnow().isoformat(),
            last_error=None,
        )
        append_assessment_timeline_event(
            assessment,
            "rubric_grading_retry_completed",
            {"attempt_count": attempt_count},
        )
        db.commit()
        return {
            "status": "complete",
            "assessment_id": int(assessment.id),
            "attempt_count": attempt_count,
        }
    except Exception as exc:
        db.rollback()
        logger.exception("rubric retry failed assessment_id=%s", assessment_id)
        assessment = db.query(Assessment).filter(Assessment.id == int(assessment_id)).one_or_none()
        if assessment is not None:
            if not (
                bool(getattr(assessment, "scoring_partial", False))
                or bool(getattr(assessment, "scoring_failed", False))
            ):
                # The authoritative scoring transaction committed before a
                # later bookkeeping/dispatch error. Do not invalidate it and
                # rerun paid grading (which could duplicate completion effects).
                _set_retry(
                    assessment,
                    status="complete",
                    claimed_at=None,
                    next_attempt_at=None,
                    completed_at=_utcnow().isoformat(),
                    last_error=None,
                    completion_warning=str(exc)[:1000],
                )
                append_assessment_timeline_event(
                    assessment,
                    "rubric_grading_retry_completed_with_warning",
                    {"error": str(exc)[:500]},
                )
                db.commit()
                return {
                    "status": "complete",
                    "assessment_id": int(assessment_id),
                    "warning": str(exc),
                }
            _, rubric = _rubric_payload(assessment)
            retry = rubric.get("retry") if isinstance(rubric.get("retry"), dict) else {}
            attempt_count = max(1, int(retry.get("attempt_count") or 1))
            delay = min(_MAX_BACKOFF_MINUTES, max(1, 2 ** min(attempt_count, 8)))
            assessment.scoring_failed = True
            _set_retry(
                assessment,
                status="error",
                claimed_at=None,
                next_attempt_at=(_utcnow() + timedelta(minutes=delay)).isoformat(),
                last_error=str(exc)[:1000],
            )
            append_assessment_timeline_event(
                assessment,
                "rubric_grading_retry_failed",
                {"attempt_count": attempt_count, "error": str(exc)[:500]},
            )
            db.commit()
        return {
            "status": "error",
            "assessment_id": int(assessment_id),
            "error": str(exc),
        }
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.rubric_retry_tasks.sweep_incomplete_rubric_scoring",
    queue="scoring",
)
def sweep_incomplete_rubric_scoring(limit: int = 100) -> dict[str, Any]:
    """Dispatch due/stale incomplete grading rows; safe to run every minute."""
    from ..models.assessment import Assessment, AssessmentStatus
    from ..platform.database import SessionLocal

    db = SessionLocal()
    dispatched = 0
    skipped = 0
    errors = 0
    try:
        rows = (
            db.query(Assessment)
            .filter(
                Assessment.is_voided.is_(False),
                Assessment.status.in_(
                    [
                        AssessmentStatus.COMPLETED,
                        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
                    ]
                ),
                or_(
                    Assessment.scoring_partial.is_(True),
                    Assessment.scoring_failed.is_(True),
                ),
            )
            .order_by(Assessment.completed_at.asc(), Assessment.id.asc())
            .limit(max(1, int(limit)))
            .all()
        )
        now = _utcnow()
        for assessment in rows:
            if not _retry_due(assessment, now=now):
                skipped += 1
                continue
            try:
                retry_incomplete_rubric_scoring.delay(int(assessment.id))
                dispatched += 1
            except Exception:
                errors += 1
                logger.exception(
                    "rubric retry dispatch failed assessment_id=%s",
                    assessment.id,
                )
        return {
            "status": "ok" if not errors else "partial",
            "eligible": len(rows),
            "dispatched": dispatched,
            "skipped": skipped,
            "errors": errors,
        }
    finally:
        db.close()


__all__ = [
    "retry_incomplete_rubric_scoring",
    "sweep_incomplete_rubric_scoring",
]
