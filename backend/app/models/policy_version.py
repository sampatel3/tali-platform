"""``policy_versions`` — fitted models produced by the nightly policy fitter.

Distinct from ``decision_policies``:
- ``decision_policies`` holds the *rule-driven* verdict policy
  (threshold sheet + rule list); existing engine reads from it.
- ``policy_versions`` holds *fitted* models produced by the nightly fitter.
  The automatic per-decision shadow/promotion lifecycle is currently dormant:
  candidate rows remain fail-closed and can support governed safety checks,
  but are not activated by the production scheduler. Shadow/gate primitives
  remain available for an explicit future rollout.

Statuses:
  candidate  -> just fit, not yet evaluated
  shadow     -> being evaluated in shadow mode alongside the live policy
  live       -> the active fitted policy for (org, role)
  archived   -> superseded by a newer live version (kept for rollback)
  rejected   -> failed the promotion gate
  superseded -> replaced by a newer pending nightly candidate before promotion

The nightly fitter bounds its own pending output to one ``candidate`` per
(org, role). Explicit/manual ``shadow`` rows are not touched by that cleanup.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


POLICY_VERSION_STATUSES = (
    "candidate",
    "shadow",
    "live",
    "archived",
    "rejected",
    "superseded",
)
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
