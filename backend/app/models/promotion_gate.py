"""ORM rows for Phase 5 promotion-gate tables."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class BiasAuditResult(Base):
    __tablename__ = "bias_audit_results"

    id = Column(BigInteger, primary_key=True)
    policy_version_id = Column(
        BigInteger, ForeignKey("policy_versions.id"), nullable=False, index=True
    )
    audited_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    passed = Column(Boolean, nullable=False, server_default=false())
    metrics_json = Column(JSON, nullable=False)
    violations_json = Column(JSON, nullable=True)
    override_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    override_reason = Column(Text, nullable=True)

    policy_version = relationship("PolicyVersion")


class ShadowRun(Base):
    __tablename__ = "shadow_runs"

    id = Column(BigInteger, primary_key=True)
    candidate_policy_version_id = Column(
        BigInteger, ForeignKey("policy_versions.id"), nullable=False, index=True
    )
    live_policy_version_id = Column(
        BigInteger, ForeignKey("policy_versions.id"), nullable=True
    )
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    decisions_compared = Column(Integer, nullable=False, server_default="0")
    disagreements = Column(Integer, nullable=False, server_default="0")
    metrics_json = Column(JSON, nullable=True)
    status = Column(String(16), nullable=False, server_default="comparing")


class GoldEvalExample(Base):
    __tablename__ = "gold_eval_examples"

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True, index=True)
    features_json = Column(JSON, nullable=False)
    expected_outcome = Column(Float, nullable=False)
    notes = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
