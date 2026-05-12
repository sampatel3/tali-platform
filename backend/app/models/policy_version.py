"""``policy_versions`` — fitted models produced by the nightly policy fitter.

Distinct from ``decision_policies``:
- ``decision_policies`` holds the *rule-driven* verdict policy
  (threshold sheet + rule list); existing engine reads from it.
- ``policy_versions`` holds *fitted* models: one row per nightly fit,
  shadow-evaluated and promoted by the promotion gate (Phase 5). The
  Phase 3 code writes candidate rows here; the Phase 5 promotion gate
  flips the status.

Statuses:
  candidate  -> just fit, not yet evaluated
  shadow     -> being evaluated in shadow mode alongside the live policy
  live       -> the active fitted policy for (org, role)
  archived   -> superseded by a newer live version (kept for rollback)
  rejected   -> failed the promotion gate

Multiple ``candidate`` and ``shadow`` rows may exist concurrently per
(org, role); only one ``live`` row at a time.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


POLICY_VERSION_STATUSES = ("candidate", "shadow", "live", "archived", "rejected")
POLICY_MODEL_KINDS = ("logistic_pooled", "gbm_pooled")  # extensible


class PolicyVersion(Base):
    __tablename__ = "policy_versions"

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True, index=True)

    model_kind = Column(String(32), nullable=False)
    model_json = Column(JSON, nullable=False)
    metrics_json = Column(JSON, nullable=True)

    training_window_start = Column(DateTime(timezone=True), nullable=True)
    training_window_end = Column(DateTime(timezone=True), nullable=True)
    trained_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    status = Column(String(16), nullable=False, server_default="candidate", index=True)
    promoted_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    organization = relationship("Organization")
    role = relationship("Role")
