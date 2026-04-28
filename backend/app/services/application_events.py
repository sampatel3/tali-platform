"""Event-handler primitives for the platform's auto-trigger flows.

Single place that says "given this event, here are the auto-tasks to
enqueue." Today there are two events:

- ``on_role_jd_attached(role)`` — fires when a JD is attached or
  re-uploaded to a role. Enqueues interview-focus generation.
- ``on_application_created(app, *, score)`` — fires when an application
  is ingested from any source (manual upload, Workable sync). Enqueues
  the per-candidate auto work (interview pack, auto-reject) and, when
  ``score=True``, the recruiter-implicit scoring job (e.g. recruiter
  uploaded a CV by hand — that's an implicit "score this" intent).

Policy reminder: CV match scoring is HUMAN-triggered. Workable bulk sync
calls these handlers with ``score=False`` so the sync ingests cleanly
without auto-enqueueing scoring for everyone it pulls. Recruiter-driven
flows (POST /applications, POST /applications/{id}/upload-cv) pass
``score=True``.

These helpers must be cheap and synchronous — they only schedule Celery
tasks via ``.delay()``. The actual Claude work happens on the worker.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.candidate_application import CandidateApplication
    from ..models.role import Role

logger = logging.getLogger("taali.events")


def on_role_jd_attached(role: "Role") -> None:
    """Schedule auto-tasks for a role that just had a JD attached.

    No-ops when there's no JD text yet; the page can still return without
    waiting for the worker.
    """
    if role is None or not (getattr(role, "job_spec_text", "") or "").strip():
        return
    role_id = int(getattr(role, "id", 0) or 0)
    if role_id <= 0:
        return
    try:
        from ..tasks.automation_tasks import generate_role_interview_focus

        generate_role_interview_focus.delay(role_id)
        logger.info("on_role_jd_attached enqueued role_id=%s", role_id)
    except Exception:  # pragma: no cover — defensive: never let scheduling break the request
        logger.exception("on_role_jd_attached scheduling failed role_id=%s", role_id)


def on_application_created(
    app: "CandidateApplication",
    *,
    score: bool = False,
    score_force: bool = False,
) -> None:
    """Schedule auto-tasks for a freshly-ingested application.

    Always schedules:
    - interview-pack generation (cached on the row, read by pipeline list)
    - auto-reject pre-screen evaluation

    When ``score=True``, also enqueues a CV match scoring job. Pass this
    only from human-triggered ingestion flows where the recruiter's
    intent is implicitly "score this": manual CV upload endpoints and the
    explicit Score / Rescore / Score-selected buttons.

    The Workable bulk sync passes ``score=False`` so importing N
    candidates doesn't auto-enqueue N scoring jobs — recruiters trigger
    scoring when they're ready, on their own schedule.
    """
    if app is None:
        return
    application_id = int(getattr(app, "id", 0) or 0)
    if application_id <= 0:
        return
    try:
        from ..tasks.automation_tasks import (
            generate_application_interview_pack,
            run_application_auto_reject,
        )

        generate_application_interview_pack.delay(application_id)
        run_application_auto_reject.delay(application_id)
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "on_application_created auto-task scheduling failed application_id=%s",
            application_id,
        )

    if score:
        try:
            # Reuse the orchestrator's enqueue path so caching, job-row
            # bookkeeping, and queue routing stay consistent with the
            # explicit Score / Rescore endpoints.
            from sqlalchemy.orm import Session

            from ..platform.database import SessionLocal
            from ..services.cv_score_orchestrator import enqueue_score

            db: Session = SessionLocal()
            try:
                from ..models.candidate_application import CandidateApplication

                live = (
                    db.query(CandidateApplication)
                    .filter(CandidateApplication.id == application_id)
                    .first()
                )
                if live is not None:
                    enqueue_score(db, live, force=score_force)
                    db.commit()
            finally:
                db.close()
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "on_application_created scoring enqueue failed application_id=%s",
                application_id,
            )

    logger.info(
        "on_application_created enqueued application_id=%s score=%s",
        application_id,
        score,
    )
