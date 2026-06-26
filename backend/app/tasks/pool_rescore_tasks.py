"""Talent-pool rediscovery Phase B — the opt-in Sonnet re-score.

Scores each selected application against a NEW free-text requirement via the
holistic engine: cache-backed (an unchanged CV+requirement is ~free on re-run)
and metered per call. Results land on the ``PoolRescoreJob`` row — NEVER on
``candidate_applications.cv_match_details``, which stays the canonical role score.
One bad application degrades to a failed row, not a dead job.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .celery_app import celery_app

logger = logging.getLogger("taali.tasks.pool_rescore")


@celery_app.task(name="rescore_pool_against_requirement")
def rescore_pool_against_requirement(job_id: int) -> dict:
    from ..cv_matching.holistic import run_holistic_match
    from ..models.candidate_application import CandidateApplication
    from ..models.pool_rescore_job import (
        POOL_RESCORE_DONE,
        POOL_RESCORE_ERROR,
        POOL_RESCORE_PENDING,
        POOL_RESCORE_RUNNING,
        PoolRescoreJob,
    )
    from ..platform.database import SessionLocal
    from ..services.claude_client_resolver import get_metered_client

    with SessionLocal() as db:
        job = db.get(PoolRescoreJob, int(job_id))
        if job is None:
            logger.warning("pool re-score job %s not found", job_id)
            return {"ok": False, "error": "job_not_found"}
        # Idempotent: a re-dispatch of an already-started job is a no-op.
        if job.status != POOL_RESCORE_PENDING:
            return {"ok": True, "status": job.status, "skipped": True}

        job.status = POOL_RESCORE_RUNNING
        db.commit()

        try:
            from ..services.workable_context_service import format_workable_context
        except Exception:  # noqa: BLE001 — notes are best-effort
            format_workable_context = None  # type: ignore[assignment]

        requirement = job.requirement_text or ""
        org_id = int(job.organization_id)
        app_ids = [int(x) for x in (job.application_ids or [])]

        try:
            client = get_metered_client(organization_id=org_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pool re-score client init failed job=%s: %s", job_id, exc)
            job.status = POOL_RESCORE_ERROR
            job.error_message = f"client init failed: {exc}"[:500]
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"ok": False, "error": "client_init_failed"}

        apps = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id.in_(app_ids),
                CandidateApplication.organization_id == org_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
            if app_ids
            else []
        )

        results: list[dict] = []
        scored = cached = failed = 0
        for app in apps:
            cv = app.cv_text or (
                app.candidate.cv_text if app.candidate is not None else None
            )
            workable_context = None
            if format_workable_context is not None:
                try:
                    workable_context = format_workable_context(app.candidate, app) or None
                except Exception:  # noqa: BLE001
                    workable_context = None
            try:
                out = run_holistic_match(
                    cv or "",
                    requirement,
                    client=client,
                    # role-less ad-hoc score; metered against the application so
                    # spend stays attributable + water-tight.
                    metering_context={
                        "organization_id": org_id,
                        "role_id": None,
                        "entity_id": f"application:{app.id}",
                    },
                    workable_context=workable_context,
                )
                status = getattr(out.scoring_status, "value", str(out.scoring_status))
                is_cached = bool(getattr(out, "cache_hit", False))
                ok = str(status).lower() == "ok"
                results.append(
                    {
                        "application_id": int(app.id),
                        "role_fit_score": out.role_fit_score if ok else None,
                        "summary": (out.summary or "")[:1000] if ok else None,
                        "scoring_status": status,
                        "cache_hit": is_cached,
                    }
                )
                if ok:
                    scored += 1
                    if is_cached:
                        cached += 1
                else:
                    failed += 1
            except Exception as exc:  # noqa: BLE001 — degrade this app, not the job
                logger.warning("pool re-score app=%s failed: %s", app.id, exc)
                failed += 1
                results.append(
                    {
                        "application_id": int(app.id),
                        "role_fit_score": None,
                        "summary": None,
                        "scoring_status": "failed",
                        "cache_hit": False,
                    }
                )

        # Rank by the NEW-requirement score, best first (failed/None last).
        results.sort(
            key=lambda r: (
                r["role_fit_score"] if r["role_fit_score"] is not None else float("-inf")
            ),
            reverse=True,
        )
        job.results = results
        job.counts = {
            "requested": len(app_ids),
            "scored": scored,
            "cached": cached,
            "failed": failed,
        }
        job.status = POOL_RESCORE_DONE
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {"ok": True, "scored": scored, "cached": cached, "failed": failed}
