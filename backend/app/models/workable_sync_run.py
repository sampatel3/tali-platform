from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class WorkableSyncRun(Base):
    __tablename__ = "workable_sync_runs"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    requested_by_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    mode = Column(String, nullable=False, default="metadata")
    status = Column(String, nullable=False, default="running")
    phase = Column(String, nullable=True)
    jobs_total = Column(Integer, nullable=False, default=0)
    jobs_processed = Column(Integer, nullable=False, default=0)
    candidates_seen = Column(Integer, nullable=False, default=0)
    candidates_upserted = Column(Integer, nullable=False, default=0)
    applications_upserted = Column(Integer, nullable=False, default=0)
    errors = Column(JSON, nullable=True)
    db_snapshot = Column(JSON, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="workable_sync_runs")
