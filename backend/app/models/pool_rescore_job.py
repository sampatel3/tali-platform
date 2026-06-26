"""Talent-pool rediscovery — Phase B: opt-in re-score against a NEW requirement.

One row per recruiter-triggered re-score of a shortlist (the shortlist comes from
``screen_pool_against_requirement``) against a free-text requirement. Each selected
application is scored against the requirement via the holistic engine and the
result is stored HERE — never on ``candidate_applications.cv_match_details``, which
stays the canonical role-tied score. The holistic shared-result cache makes
re-running the same (CV, requirement) ~free, so a re-run is cheap.
"""
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from ..platform.database import Base

POOL_RESCORE_PENDING = "pending"
POOL_RESCORE_RUNNING = "running"
POOL_RESCORE_DONE = "done"
POOL_RESCORE_ERROR = "error"

POOL_RESCORE_STATUSES = {
    POOL_RESCORE_PENDING,
    POOL_RESCORE_RUNNING,
    POOL_RESCORE_DONE,
    POOL_RESCORE_ERROR,
}


class PoolRescoreJob(Base):
    """A bounded, opt-in re-score of selected applications against an ad-hoc
    requirement. Cost-guarded by a hard count cap at the API + an explicit UI
    confirm; every call is metered. Results are kept separate from the canonical
    role score so a rediscovery experiment never overwrites real pipeline data."""

    __tablename__ = "pool_rescore_jobs"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requirement_text = Column(Text, nullable=False)
    # sha256 of the requirement text — lets the UI find a prior re-score of the
    # same requirement instead of paying for it again.
    requirement_hash = Column(String, index=True, nullable=False)
    status = Column(String, nullable=False, default=POOL_RESCORE_PENDING)
    # Requested application ids (already count-capped at the API).
    application_ids = Column(JSON, nullable=False, default=list)
    # {requested, scored, cached, failed}
    counts = Column(JSON, nullable=True)
    # [{application_id, role_fit_score, summary, scoring_status, cache_hit}]
    results = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
