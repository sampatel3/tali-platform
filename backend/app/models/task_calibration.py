"""``task_calibrations`` — Amendment A2: per-template predictive quality.

One row per (task_id, role_family). Recomputed by
``task_selection.calibration.recompute_all`` nightly.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class TaskCalibration(Base):
    __tablename__ = "task_calibrations"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "role_family", name="uq_task_calibrations_task_role_family",
        ),
    )

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    role_family = Column(String(64), nullable=False, index=True)
    predictive_quality = Column(Float, nullable=False, server_default="0")
    sample_size = Column(Integer, nullable=False, server_default="0")
    avg_outcome_quality = Column(Float, nullable=True)
    last_recomputed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    retired_at = Column(DateTime(timezone=True), nullable=True)
    retired_reason = Column(Text, nullable=True)

    task = relationship("Task")
    organization = relationship("Organization")
