from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.sql.expression import false as sql_false
from sqlalchemy.sql.expression import true as sql_true

from ..platform.database import Base


SCORE_JOB_PENDING = "pending"
SCORE_JOB_RUNNING = "running"
SCORE_JOB_DONE = "done"
SCORE_JOB_ERROR = "error"
SCORE_JOB_STALE = "stale"


SCORE_JOB_STATUSES = {
    SCORE_JOB_PENDING,
    SCORE_JOB_RUNNING,
    SCORE_JOB_DONE,
    SCORE_JOB_ERROR,
    SCORE_JOB_STALE,
}


class CvScoreJob(Base):
    """Per-application scoring job state.

    At most one active (pending/running) job per application. Done/error/stale
    rows are kept for audit but a fresh enqueue creates a new pending row
    rather than mutating history. The application listing reads the latest
    row per application to surface ``score_status`` to the UI.
    """

    __tablename__ = "cv_score_jobs"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(
        Integer,
        ForeignKey("candidate_applications.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), index=True, nullable=True)
    status = Column(String, nullable=False, default=SCORE_JOB_PENDING)
    cache_key = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    model = Column(String, nullable=True)
    cache_hit = Column(String, nullable=True)  # "hit" | "miss" | None
    error_message = Column(Text, nullable=True)
    celery_task_id = Column(String, nullable=True)
    # Durable execution authority.  Autonomous/ingest work must re-check the
    # live Role immediately before provider spend; an explicit recruiter or
    # administrator score remains allowed while the role agent is paused/off.
    # Existing rows are conservatively treated as autonomous by migration.
    requires_active_agent = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sql_true(),
    )
    # Persist the recruiter-approved pre-screen bypass.  Keeping this only in
    # the Celery message caused stuck-job recovery to silently lose it.
    force_full_score = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sql_false(),
    )
    # Stale scores can be made visibly untrustworthy without authorizing a
    # paid rescore.  Only an explicit recruiter confirmation flips this gate;
    # ordinary/legacy work defaults to approved to preserve existing flows.
    dispatch_approved = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sql_true(),
    )
    queued_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    application = relationship("CandidateApplication", back_populates="score_jobs")
