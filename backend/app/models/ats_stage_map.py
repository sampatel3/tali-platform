from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class AtsStageMap(Base):
    """Per-org mapping from a remote ATS status to a Taali pipeline stage.

    Bullhorn (and Workable) statuses are per-org free text, so the remote
    status → Taali stage translation cannot be hardcoded. Each row maps one
    ``remote_status`` for one ``ats`` in one org to a ``taali_stage`` (one of
    ``pipeline_service.PIPELINE_STAGES``) plus an ``is_reject`` flag. An
    unmapped status surfaces as needs-mapping and is NEVER guessed.
    """

    __tablename__ = "ats_stage_map"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "ats",
            "remote_status",
            name="uq_ats_stage_map_org_ats_remote_status",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    ats = Column(String, nullable=False)
    remote_status = Column(String, nullable=False)
    # One of pipeline_service.PIPELINE_STAGES; kept a plain String at the model
    # layer (validated at the route/schema layer, like candidate_application
    # .pipeline_stage).
    taali_stage = Column(String, nullable=False)
    is_reject = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="ats_stage_maps")
