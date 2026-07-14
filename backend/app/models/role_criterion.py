from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base
from .org_criterion import (
    BUCKET_CONSTRAINT,
    BUCKET_MUST,
    BUCKET_PREFERRED,
    CRITERION_BUCKETS,
)


CRITERION_SOURCE_RECRUITER = "recruiter"
CRITERION_SOURCE_DERIVED = "derived_from_spec"
# Criteria authored in a RoleBrief and copied into its materialized Role.
# Keeping this distinct from recruiter/workspace chips lets a re-publish
# reconcile only the brief-owned rows without deleting later role-specific
# recruiter edits.
CRITERION_SOURCE_REQUISITION = "requisition"
# Retained for back-compat with the old schema literal (schemas/role.py).
# New code should set ``bucket = "constraint"`` and use
# ``CRITERION_SOURCE_RECRUITER`` instead.
CRITERION_SOURCE_RECRUITER_CONSTRAINT = "recruiter_constraint"


class RoleCriterion(Base):
    __tablename__ = "role_criteria"

    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), index=True, nullable=False)
    source = Column(String, default=CRITERION_SOURCE_RECRUITER, nullable=False)
    ordering = Column(Integer, default=0, nullable=False)
    weight = Column(Float, default=1.0, nullable=False)
    # Legacy boolean. Kept in sync with ``bucket`` (true iff bucket == 'must')
    # so existing readers (cv_score_orchestrator cache key) keep working
    # until the next breaking change.
    must_have = Column(Boolean, default=False, nullable=False)
    bucket = Column(String, default=BUCKET_PREFERRED, nullable=False)
    # Provenance — set when this row was copied from a workspace criterion.
    # ``ON DELETE SET NULL``: if the workspace criterion is deleted the role
    # keeps its copy as a role-only chip.
    org_criterion_id = Column(
        Integer,
        ForeignKey("org_criteria.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    # Set whenever the recruiter edits a workspace-derived row's text or
    # bucket. ``sync workspace`` skips overwriting these so recruiter edits
    # are preserved.
    customized_at = Column(DateTime(timezone=True), nullable=True)
    text = Column(Text, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    role = relationship("Role", back_populates="criteria")
    org_criterion = relationship("OrganizationCriterion", foreign_keys=[org_criterion_id])
