from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    text,
)
from sqlalchemy.sql import func

from ..platform.database import Base


JOB_KIND_SCORING_BATCH = "scoring_batch"
JOB_KIND_CV_FETCH = "cv_fetch"
JOB_KIND_GRAPH_SYNC = "graph_sync"
JOB_KIND_PRE_SCREEN_BATCH = "pre_screen_batch"
JOB_KIND_PROCESS_ROLE = "process_role"
# A recruiter approve / bulk-approve of Hub decisions. One row per request
# (a 100-decision bulk approve is ONE job), draining the Workable writebacks
# sequentially in the background.
JOB_KIND_DECISION_BATCH = "decision_batch"
# A single Workable write-back op (override, hand-back stage move, manual
# outcome sync, note) run through the generic serialized runner.
JOB_KIND_WORKABLE_OP = "workable_op"

JOB_KINDS = (
    JOB_KIND_SCORING_BATCH,
    JOB_KIND_CV_FETCH,
    JOB_KIND_GRAPH_SYNC,
    JOB_KIND_PRE_SCREEN_BATCH,
    JOB_KIND_PROCESS_ROLE,
    JOB_KIND_DECISION_BATCH,
    JOB_KIND_WORKABLE_OP,
)

SCOPE_KIND_ROLE = "role"
SCOPE_KIND_ORG = "org"

SCORING_RECOVERY_INDEX = "ix_background_job_runs_scoring_recovery_active"
SCORING_RECOVERY_PREDICATE = (
    "kind = 'scoring_batch' AND finished_at IS NULL "
    "AND status IN ('dispatching', 'queued', 'running', 'cancelling')"
)


class BackgroundJobRun(Base):
    __tablename__ = "background_job_runs"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=False)
    scope_kind = Column(String, nullable=False)
    scope_id = Column(Integer, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    # Optional stable producer receipt. Confirmed chat commands use this to
    # collapse a replay before a second ATS task can be published.
    dispatch_key = Column(String(200), nullable=True, unique=True, index=True)
    status = Column(String, nullable=False)
    counters = Column(JSON, nullable=False, default=dict)
    error = Column(Text, nullable=True)
    started_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_background_job_runs_org_started", "organization_id", "started_at"),
        Index(
            "ix_background_job_runs_kind_scope_started",
            "kind",
            "scope_id",
            "started_at",
        ),
        Index(
            SCORING_RECOVERY_INDEX,
            "scope_kind",
            "id",
            postgresql_where=text(SCORING_RECOVERY_PREDICATE),
            sqlite_where=text(SCORING_RECOVERY_PREDICATE),
        ),
    )
