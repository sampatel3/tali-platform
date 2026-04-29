from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.sql import func

from ..platform.database import Base


JOB_KIND_SCORING_BATCH = "scoring_batch"
JOB_KIND_CV_FETCH = "cv_fetch"
JOB_KIND_GRAPH_SYNC = "graph_sync"

JOB_KINDS = (JOB_KIND_SCORING_BATCH, JOB_KIND_CV_FETCH, JOB_KIND_GRAPH_SYNC)

SCOPE_KIND_ROLE = "role"
SCOPE_KIND_ORG = "org"


class BackgroundJobRun(Base):
    __tablename__ = "background_job_runs"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=False)
    scope_kind = Column(String, nullable=False)
    scope_id = Column(Integer, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    status = Column(String, nullable=False)
    counters = Column(JSON, nullable=False, default=dict)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_background_job_runs_org_started", "organization_id", "started_at"),
        Index("ix_background_job_runs_kind_scope_started", "kind", "scope_id", "started_at"),
    )
