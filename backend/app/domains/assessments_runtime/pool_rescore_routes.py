"""Talent-pool rediscovery Phase B — start + poll the opt-in re-score.

- POST /api/v1/candidates/pool-rescore        → create a bounded re-score job
  (hard count cap), dispatch it, return {job_id, count, estimated_cost_usd}.
- GET  /api/v1/candidates/pool-rescore/{id}    → poll status + per-candidate
  scores against the requirement.

The expensive Sonnet re-score is gated three ways: a hard server-side count cap
(the per-role monthly USD cap does NOT apply to a role-less ad-hoc score, so this
+ the UI confirm are the budget rail), an explicit recruiter confirm in the UI
(which shows the estimate this returns), and per-call metering. Results are stored
on the job, never on the canonical role score.
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.pool_rescore_job import PoolRescoreJob
from ...models.user import User
from ...platform.database import get_db

router = APIRouter(prefix="/candidates", tags=["Talent-pool rediscovery"])

# Hard ceiling on one re-score action — the budget rail. ~$0.09 per full holistic
# score (Jun 2026 cost-per-outcome); the UI shows count * this before confirming.
MAX_POOL_RESCORE = 50
COST_PER_RESCORE_USD = 0.09


@router.post("/pool-rescore")
def start_pool_rescore(
    payload: dict = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start a bounded re-score of ``application_ids`` against ``requirement_text``."""
    payload = payload or {}
    requirement_text = str(payload.get("requirement_text") or "").strip()
    raw_ids = payload.get("application_ids") or []
    if not requirement_text:
        raise HTTPException(status_code=400, detail="requirement_text is required")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="application_ids is required")
    try:
        application_ids = sorted({int(x) for x in raw_ids})
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="application_ids must be integers")
    if len(application_ids) > MAX_POOL_RESCORE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Too many candidates ({len(application_ids)}); the re-score cap is "
                f"{MAX_POOL_RESCORE}. Narrow the shortlist."
            ),
        )

    job = PoolRescoreJob(
        organization_id=current_user.organization_id,
        created_by_user_id=getattr(current_user, "id", None),
        requirement_text=requirement_text,
        requirement_hash=hashlib.sha256(requirement_text.encode("utf-8")).hexdigest(),
        application_ids=application_ids,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Dispatch the Sonnet re-score to the worker (cache-backed; metered).
    from ...tasks.pool_rescore_tasks import rescore_pool_against_requirement

    dispatched = True
    task_id = None
    try:
        async_result = rescore_pool_against_requirement.delay(job.id)
        task_id = str(async_result.id) if getattr(async_result, "id", None) else None
    except Exception:  # broker acceptance can be ambiguous; pending row is durable
        import logging

        logging.getLogger("taali.pool_rescore.routes").exception(
            "pool re-score publish failed/ambiguous job=%s; recovery will retry",
            job.id,
        )
        dispatched = False

    return {
        "job_id": job.id,
        "count": len(application_ids),
        "estimated_cost_usd": round(len(application_ids) * COST_PER_RESCORE_USD, 2),
        "status": job.status,
        "dispatch_pending": not dispatched,
        "task_id": task_id,
    }


@router.get("/pool-rescore/{job_id}")
def get_pool_rescore(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll a re-score job (org-scoped)."""
    job = (
        db.query(PoolRescoreJob)
        .filter(
            PoolRescoreJob.id == job_id,
            PoolRescoreJob.organization_id == current_user.organization_id,
        )
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Re-score job not found")
    return {
        "job_id": job.id,
        "status": job.status,
        "requirement_text": job.requirement_text,
        "counts": job.counts or {},
        "results": job.results or [],
        "estimated_cost_usd": round(
            len(job.application_ids or []) * COST_PER_RESCORE_USD, 2
        ),
    }
