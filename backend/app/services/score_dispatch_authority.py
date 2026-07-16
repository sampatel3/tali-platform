"""Durable approval and revocation transitions for CV-score work."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.cv_score_job import (
    SCORE_JOB_ERROR,
    SCORE_JOB_PENDING,
    SCORE_JOB_RUNNING,
    SCORE_JOB_STALE,
    CvScoreJob,
)


class ScoreDispatchRevoked(RuntimeError):
    """Stop a score attempt whose durable authority was revoked."""

    def __init__(self, *, phase: str, detail: str) -> None:
        super().__init__(detail)
        self.phase = str(phase)
        self.detail = str(detail)


def score_dispatch_is_approved(
    db: Session, *, job_id: int | None, application_id: int
) -> bool:
    """Read current authority without trusting an identity-map-cached job."""
    if job_id is None:
        return True
    with db.no_autoflush:
        approved = (
            db.query(CvScoreJob.dispatch_approved)
            .filter(
                CvScoreJob.id == int(job_id),
                CvScoreJob.application_id == int(application_id),
            )
            .scalar()
        )
    return approved is True


def require_score_phase_authority(
    db: Session,
    *,
    application: CandidateApplication,
    job: CvScoreJob,
    phase: str,
) -> None:
    """Fence every provider phase on live dispatch and role generation."""
    if not score_dispatch_is_approved(
        db,
        job_id=getattr(job, "id", None),
        application_id=int(application.id),
    ):
        raise ScoreDispatchRevoked(
            phase=phase,
            detail="rescreen approval is required",
        )
    expected_key = str(getattr(job, "cache_key", "") or "")
    prefix = "role-intent:"
    if not expected_key.startswith(prefix):
        return
    from ..models.role import Role
    from .role_intent_fingerprint import role_intent_fingerprint

    with db.no_autoflush:
        live_role = (
            db.query(Role)
            .filter(
                Role.id == int(job.role_id),
                Role.organization_id == int(application.organization_id),
                Role.deleted_at.is_(None),
            )
            .populate_existing()
            .one_or_none()
        )
        live_fingerprint = (
            role_intent_fingerprint(live_role, db=db)
            if live_role is not None
            else None
        )
    if live_fingerprint != expected_key.removeprefix(prefix):
        raise ScoreDispatchRevoked(phase=phase, detail="role intent changed")


def revoke_role_active_dispatch(
    db: Session,
    *,
    role_id: int,
    application_ids: list[int] | None = None,
) -> int:
    """Revoke old active attempts and add one unapproved latest marker each.

    This closes the queued-job race after a job-spec edit: an older task keeps
    its historical row id, so revoking that row is what prevents it from
    treating the newly edited spec as authority for a paid call.
    """
    if application_ids is not None and not application_ids:
        return 0
    query = db.query(CvScoreJob).filter(
        CvScoreJob.role_id == int(role_id),
        CvScoreJob.status.in_((SCORE_JOB_PENDING, SCORE_JOB_RUNNING)),
    )
    if application_ids is not None:
        query = query.filter(
            CvScoreJob.application_id.in_(
                [int(application_id) for application_id in application_ids]
            )
        )
    active = query.with_for_update().all()
    if not active:
        return 0
    now = datetime.now(timezone.utc)
    for job in active:
        job.dispatch_approved = False
        job.status = "error"
        job.error_message = "superseded_by_job_spec_update"
        job.finished_at = now

    eligible_ids = {
        int(application_id)
        for (application_id,) in (
            db.query(CandidateApplication.id)
            .filter(
                CandidateApplication.id.in_(
                    {int(job.application_id) for job in active}
                ),
                CandidateApplication.role_id == int(role_id),
                CandidateApplication.application_outcome == "open",
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
        )
    }
    added = 0
    for application_id in sorted(eligible_ids):
        latest = (
            db.query(CvScoreJob)
            .filter(CvScoreJob.application_id == application_id)
            .order_by(CvScoreJob.id.desc())
            .first()
        )
        if (
            latest is not None
            and latest.status == SCORE_JOB_STALE
            and not bool(latest.dispatch_approved)
        ):
            continue
        db.add(
            CvScoreJob(
                application_id=application_id,
                role_id=int(role_id),
                status=SCORE_JOB_STALE,
                requires_active_agent=True,
                dispatch_approved=False,
                queued_at=now,
            )
        )
        added += 1
    db.flush()
    return added


def discard_superseded_score_result(
    db: Session,
    *,
    application_id: int,
    role_id: int,
    job: CvScoreJob,
    live_fingerprint: str | None,
    force_full_score: bool,
) -> dict[str, int | str]:
    """Atomically discard old-intent writes and retain one recovery marker."""
    db.rollback()
    terminal = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.id == int(job.id))
        .with_for_update()
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    if terminal is not None and terminal.status in {
        SCORE_JOB_PENDING,
        SCORE_JOB_RUNNING,
    }:
        terminal.status = SCORE_JOB_ERROR
        terminal.error_message = "superseded_role_intent"
        terminal.finished_at = now
    latest = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == int(application_id))
        .order_by(CvScoreJob.id.desc())
        .first()
    )
    if latest is None or int(latest.id) == int(job.id) or latest.status != SCORE_JOB_STALE:
        db.add(
            CvScoreJob(
                application_id=int(application_id),
                role_id=int(role_id),
                status=SCORE_JOB_STALE,
                cache_key=(
                    f"role-intent:{live_fingerprint}"
                    if live_fingerprint
                    else None
                ),
                error_message="rescore_after_role_reconfiguration",
                requires_active_agent=bool(job.requires_active_agent),
                force_full_score=bool(job.force_full_score or force_full_score),
                queued_at=now,
            )
        )
    db.commit()
    return {
        "status": "superseded_role_intent",
        "application_id": int(application_id),
        "role_id": int(role_id),
    }


def approve_role_stale_dispatch(
    db: Session,
    *,
    role_id: int,
    application_ids: list[int] | None = None,
) -> int:
    """Promote each latest stale attempt to recruiter-authorized dispatch.

    Rows are updated in place so approval cannot create duplicate stale work.
    The caller already owns the role authorization boundary; row locks make
    concurrent confirmations collapse into the same durable transition.
    """
    if application_ids is not None and not application_ids:
        return 0
    latest_ids = (
        db.query(
            CvScoreJob.application_id.label("application_id"),
            func.max(CvScoreJob.id).label("job_id"),
        )
        .filter(CvScoreJob.role_id == int(role_id))
        .group_by(CvScoreJob.application_id)
        .subquery()
    )
    query = (
        db.query(CvScoreJob)
        .join(latest_ids, CvScoreJob.id == latest_ids.c.job_id)
        .filter(
            CvScoreJob.role_id == int(role_id),
            CvScoreJob.status == SCORE_JOB_STALE,
        )
        .with_for_update()
    )
    if application_ids is not None:
        query = query.filter(
            CvScoreJob.application_id.in_(
                [int(application_id) for application_id in application_ids]
            )
        )

    promoted = 0
    for job in query.all():
        if bool(job.dispatch_approved) and not bool(job.requires_active_agent):
            continue
        job.dispatch_approved = True
        job.requires_active_agent = False
        db.add(job)
        promoted += 1
    if promoted:
        db.flush()
    return promoted
