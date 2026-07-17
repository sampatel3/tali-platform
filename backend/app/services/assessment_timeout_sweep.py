"""Bounded selection and execution for abandoned assessment timeouts."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..components.assessments.repository import time_remaining_seconds
from ..models.assessment import Assessment, AssessmentStatus

logger = logging.getLogger(__name__)


def run_timed_out_assessment_sweep(
    *,
    limit: int = 25,
    session_factory: Callable[[], Session] | None = None,
    finalizer: Callable[[Assessment, Session], dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Find due rows without letting paused/active rows monopolize LIMIT."""

    if session_factory is None:
        from ..platform.database import SessionLocal

        session_factory = SessionLocal
    if finalizer is None:
        from ..components.assessments.service import (
            finalize_timed_out_assessment,
        )

        finalizer = finalize_timed_out_assessment

    db = session_factory()
    finalized = 0
    scoring_failed = 0
    skipped = 0
    try:
        bounded_limit = max(0, min(int(limit), 100))
        if bounded_limit == 0:
            return {"finalized": 0, "scoring_failed": 0, "skipped": 0}

        due_ids: list[int] = []
        cursor_started_at = None
        cursor_id: int | None = None
        scan_batch_size = max(100, min(1000, bounded_limit * 4))
        earliest_possible_timeout = datetime.now(timezone.utc) - timedelta(
            minutes=15
        )
        while len(due_ids) < bounded_limit:
            query = db.query(Assessment).filter(
                Assessment.status == AssessmentStatus.IN_PROGRESS,
                Assessment.is_voided.is_(False),
                Assessment.is_demo.is_(False),
                Assessment.is_timer_paused.is_(False),
                Assessment.paused_at.is_(None),
                Assessment.started_at.isnot(None),
                Assessment.started_at <= earliest_possible_timeout,
            )
            if cursor_started_at is not None and cursor_id is not None:
                query = query.filter(
                    or_(
                        Assessment.started_at > cursor_started_at,
                        and_(
                            Assessment.started_at == cursor_started_at,
                            Assessment.id > cursor_id,
                        ),
                    )
                )
            page = (
                query.order_by(Assessment.started_at.asc(), Assessment.id.asc())
                .limit(scan_batch_size)
                .all()
            )
            if not page:
                break
            for assessment in page:
                if time_remaining_seconds(assessment) <= 0:
                    due_ids.append(int(assessment.id))
                    if len(due_ids) >= bounded_limit:
                        break
                else:
                    skipped += 1
            cursor_started_at = page[-1].started_at
            cursor_id = int(page[-1].id)
            if len(page) < scan_batch_size:
                break

        # Selection is detached from expensive finalization. Every candidate
        # is reloaded and rechecked so a concurrent pause, submit, or archive
        # always wins before capture/scoring begins.
        db.rollback()
        for assessment_id in due_ids:
            assessment = (
                db.query(Assessment)
                .filter(
                    Assessment.id == assessment_id,
                    Assessment.status == AssessmentStatus.IN_PROGRESS,
                    Assessment.is_voided.is_(False),
                )
                .populate_existing()
                .one_or_none()
            )
            if assessment is None or time_remaining_seconds(assessment) > 0:
                skipped += 1
                db.rollback()
                continue
            try:
                result = finalizer(assessment, db)
            except Exception:
                logger.exception(
                    "timed-out assessment finalization crashed assessment_id=%s",
                    assessment.id,
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
            finalized,
            scoring_failed,
            skipped,
        )
        return {
            "finalized": finalized,
            "scoring_failed": scoring_failed,
            "skipped": skipped,
        }
    finally:
        db.close()


__all__ = ["run_timed_out_assessment_sweep"]
