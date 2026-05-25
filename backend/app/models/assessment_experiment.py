"""A/B experiment models for assessment-task trials.

An ``AssessmentExperiment`` scopes one role: when it is ``active`` and a
candidate is sent an assessment for that role, the assignment engine picks one
of its ``arms`` deterministically (stable per candidate+role) so resends never
re-roll. Two layers are supported on the same shape:

- *distinct-task*: arms point at different ``task_id`` (``knob_overrides`` NULL).
- *design-knob*: arms share a ``task_id`` and differ only in ``knob_overrides``
  (duration / score_weights / calibration), applied at invite time without
  forking the Task row.

The per-candidate assignment record is co-located on the ``assessments`` row
(see new columns on :class:`app.models.assessment.Assessment`), which is safe
because the assignment is written in the same transaction as the assessment and
bounded by the one-active-per-(candidate, role) unique index.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


# Experiment lifecycle.
EXPERIMENT_STATUS_DRAFT = "draft"
EXPERIMENT_STATUS_ACTIVE = "active"
EXPERIMENT_STATUS_PAUSED = "paused"
EXPERIMENT_STATUS_COMPLETED = "completed"
EXPERIMENT_STATUSES = {
    EXPERIMENT_STATUS_DRAFT,
    EXPERIMENT_STATUS_ACTIVE,
    EXPERIMENT_STATUS_PAUSED,
    EXPERIMENT_STATUS_COMPLETED,
}

# What an experiment varies.
EXPERIMENT_TYPE_TASK = "task"
EXPERIMENT_TYPE_KNOB = "knob"
EXPERIMENT_TYPES = {EXPERIMENT_TYPE_TASK, EXPERIMENT_TYPE_KNOB}

# How a given assessment's task arm was chosen (recorded on the assessment row).
ASSIGNMENT_METHOD_RANDOM = "random"  # drawn from an active experiment
ASSIGNMENT_METHOD_FORCED = "forced"  # explicit recruiter task_id; excluded from the random cohort
ASSIGNMENT_METHOD_SINGLE_TASK_DEFAULT = "single_task_default"  # role has exactly one task, no experiment
ASSIGNMENT_METHOD_NO_EXPERIMENT = "no_experiment"  # picked outside any experiment
ASSIGNMENT_METHODS = {
    ASSIGNMENT_METHOD_RANDOM,
    ASSIGNMENT_METHOD_FORCED,
    ASSIGNMENT_METHOD_SINGLE_TASK_DEFAULT,
    ASSIGNMENT_METHOD_NO_EXPERIMENT,
}


class AssessmentExperiment(Base):
    __tablename__ = "assessment_experiments"
    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="uq_experiment_org_key"),
        Index("ix_experiment_role_status", "role_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id = Column(
        Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key = Column(String, nullable=False)  # stable per-org slug, e.g. "deeplight_ai_eng_task_ab"
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String, nullable=False, default=EXPERIMENT_STATUS_DRAFT)
    experiment_type = Column(String, nullable=False, default=EXPERIMENT_TYPE_TASK)
    starts_at = Column(DateTime(timezone=True), nullable=True)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    salt = Column(String, nullable=False)  # per-experiment hash salt; keeps draws independent
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    arms = relationship(
        "AssessmentExperimentArm",
        back_populates="experiment",
        cascade="all, delete-orphan",
        order_by="AssessmentExperimentArm.id",
    )


class AssessmentExperimentArm(Base):
    __tablename__ = "assessment_experiment_arms"
    __table_args__ = (
        UniqueConstraint("experiment_id", "arm_key", name="uq_arm_experiment_key"),
        Index("ix_arm_experiment_active", "experiment_id", "is_active"),
    )

    id = Column(Integer, primary_key=True, index=True)
    experiment_id = Column(
        Integer,
        ForeignKey("assessment_experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    arm_key = Column(String, nullable=False)  # "A"/"B" or "design_heavy"/"impl_heavy"
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    weight = Column(Integer, nullable=False, default=1)  # relative split weight
    knob_overrides = Column(JSON, nullable=True)  # {duration_minutes, score_weights, calibration_enabled, ...}
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    experiment = relationship("AssessmentExperiment", back_populates="arms")
    task = relationship("Task")
