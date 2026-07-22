"""Provider-neutral routing decisions and physical execution attempts.

These tables intentionally contain routing metadata only. Prompt, message,
candidate, and provider response content belong in neither table.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import false, func

from ..platform.database import Base

AI_ROUTING_INVOCATION_STATUSES = (
    "planned",
    "running",
    "succeeded",
    "failed",
    "cancelled",
)
AI_ROUTING_ATTEMPT_STATUSES = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "ambiguous",
    "cancelled",
)


class AIRoutingInvocation(Base):
    """One logical, versioned routing decision for a named task."""

    __tablename__ = "ai_routing_invocations"

    invocation_id = Column(String(36), primary_key=True)
    route_id = Column(String(36), nullable=False)
    root_invocation_id = Column(String(36), nullable=False)
    parent_invocation_id = Column(String(36), nullable=True)

    operation = Column(String(80), nullable=False)
    workflow = Column(String(120), nullable=False)
    task = Column(String(160), nullable=False)
    profile_version = Column(String(120), nullable=False)
    policy_version = Column(String(120), nullable=False)
    registry_version = Column(String(120), nullable=False)
    request_snapshot = Column(JSON, nullable=False, default=dict)
    decision_snapshot = Column(JSON, nullable=False, default=dict)

    # Attribution is deliberately denormalized. Routing telemetry must be able
    # to flush inside the caller's transaction without taking domain-table FK
    # locks or preventing later privacy deletion of a domain record.
    organization_id = Column(Integer, nullable=True)
    user_id = Column(Integer, nullable=True)
    role_id = Column(Integer, nullable=True)
    agent_run_id = Column(BigInteger, nullable=True)
    entity_id = Column(String(160), nullable=True)

    selected_deployment_id = Column(String(160), nullable=True)
    status = Column(
        String(24), nullable=False, default="planned", server_default="planned"
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    attempts = relationship(
        "AIRoutingAttempt",
        back_populates="invocation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AIRoutingAttempt.ordinal",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('planned', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_ai_routing_invocation_status",
        ),
        CheckConstraint(
            "((status IN ('planned', 'running') AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'cancelled') "
            "AND finished_at IS NOT NULL))",
            name="ck_ai_routing_invocation_finished",
        ),
        CheckConstraint(
            "((status = 'planned' AND started_at IS NULL) OR "
            "(status IN ('running', 'succeeded') AND started_at IS NOT NULL) OR "
            "status IN ('failed', 'cancelled'))",
            name="ck_ai_routing_invocation_started",
        ),
        Index("ix_ai_routing_invocation_route", "route_id"),
        Index("ix_ai_routing_invocation_root", "root_invocation_id"),
        Index("ix_ai_routing_invocation_parent", "parent_invocation_id"),
        Index(
            "ix_ai_routing_invocation_task_created",
            "workflow",
            "task",
            "created_at",
        ),
        Index(
            "ix_ai_routing_invocation_org_created",
            "organization_id",
            "created_at",
        ),
        Index("ix_ai_routing_invocation_status_created", "status", "created_at"),
    )


class AIRoutingAttempt(Base):
    """One physical provider attempt made for a logical invocation."""

    __tablename__ = "ai_routing_attempts"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    invocation_id = Column(
        String(36),
        ForeignKey("ai_routing_invocations.invocation_id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal = Column(Integer, nullable=False)
    iteration_ordinal = Column(Integer, nullable=False)
    attempt_in_iteration = Column(Integer, nullable=False)
    provider = Column(String(80), nullable=False)
    runtime = Column(String(80), nullable=False)
    deployment_id = Column(String(160), nullable=False)
    model = Column(String(160), nullable=False)
    region = Column(String(32), nullable=False, default="global", server_default="global")
    pricing_id = Column(String(160), nullable=True)
    credit_reservation_ref = Column(String(255), nullable=False)
    estimated_input_tokens = Column(BigInteger, nullable=False)
    estimated_output_tokens = Column(BigInteger, nullable=False)
    estimated_input_cost_basis = Column(String(32), nullable=False)
    admitted_cost_usd_micro = Column(BigInteger, nullable=False)
    fallback_from_deployment_id = Column(String(160), nullable=True)
    fallback_reason = Column(String(160), nullable=True)

    status = Column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    error_class = Column(String(120), nullable=True)
    error_reason = Column(String(160), nullable=True)
    provider_request_id = Column(String(255), nullable=True)
    latency_ms = Column(BigInteger, nullable=True)
    input_tokens = Column(BigInteger, nullable=True)
    output_tokens = Column(BigInteger, nullable=True)
    cache_read_tokens = Column(BigInteger, nullable=True)
    cache_creation_tokens = Column(BigInteger, nullable=True)
    cost_usd_micro = Column(BigInteger, nullable=True)
    usage_unknown = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    usage_event_id = Column(Integer, nullable=True)
    claude_call_log_id = Column(BigInteger, nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    invocation = relationship("AIRoutingInvocation", back_populates="attempts")

    __table_args__ = (
        UniqueConstraint(
            "invocation_id",
            "ordinal",
            name="uq_ai_routing_attempt_invocation_ordinal",
        ),
        UniqueConstraint(
            "credit_reservation_ref",
            name="uq_ai_routing_attempt_credit_reservation",
        ),
        CheckConstraint("ordinal >= 1", name="ck_ai_routing_attempt_ordinal"),
        CheckConstraint(
            "iteration_ordinal >= 1",
            name="ck_ai_routing_attempt_iteration_ordinal",
        ),
        CheckConstraint(
            "attempt_in_iteration >= 1",
            name="ck_ai_routing_attempt_in_iteration",
        ),
        CheckConstraint(
            "estimated_input_tokens >= 0",
            name="ck_ai_routing_attempt_estimated_input_tokens",
        ),
        CheckConstraint(
            "estimated_output_tokens > 0",
            name="ck_ai_routing_attempt_estimated_output_tokens",
        ),
        CheckConstraint(
            "estimated_input_cost_basis IN "
            "('standard', 'cache_write_5m', 'cache_write_1h')",
            name="ck_ai_routing_attempt_estimated_input_cost_basis",
        ),
        CheckConstraint(
            "admitted_cost_usd_micro >= 0",
            name="ck_ai_routing_attempt_admitted_cost",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', "
            "'ambiguous', 'cancelled')",
            name="ck_ai_routing_attempt_status",
        ),
        CheckConstraint(
            "((status IN ('pending', 'running') AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'ambiguous', 'cancelled') "
            "AND finished_at IS NOT NULL))",
            name="ck_ai_routing_attempt_finished",
        ),
        CheckConstraint(
            "((status = 'pending' AND started_at IS NULL) OR "
            "(status != 'pending' AND started_at IS NOT NULL))",
            name="ck_ai_routing_attempt_started",
        ),
        CheckConstraint(
            "((fallback_from_deployment_id IS NULL AND fallback_reason IS NULL) OR "
            "(fallback_from_deployment_id IS NOT NULL AND fallback_reason IS NOT NULL))",
            name="ck_ai_routing_attempt_fallback_complete",
        ),
        CheckConstraint(
            "((status IN ('failed', 'ambiguous') AND error_class IS NOT NULL) OR "
            "(status = 'succeeded' AND error_class IS NULL AND error_reason IS NULL) OR "
            "status IN ('pending', 'running', 'cancelled'))",
            name="ck_ai_routing_attempt_error_complete",
        ),
        CheckConstraint(
            "((status IN ('pending', 'running') AND latency_ms IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'ambiguous', 'cancelled') "
            "AND latency_ms IS NOT NULL AND latency_ms >= 0))",
            name="ck_ai_routing_attempt_latency",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_ai_routing_attempt_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_ai_routing_attempt_output_tokens",
        ),
        CheckConstraint(
            "cache_read_tokens IS NULL OR cache_read_tokens >= 0",
            name="ck_ai_routing_attempt_cache_read_tokens",
        ),
        CheckConstraint(
            "cache_creation_tokens IS NULL OR cache_creation_tokens >= 0",
            name="ck_ai_routing_attempt_cache_creation_tokens",
        ),
        CheckConstraint(
            "cost_usd_micro IS NULL OR cost_usd_micro >= 0",
            name="ck_ai_routing_attempt_cost",
        ),
        CheckConstraint(
            "((status IN ('pending', 'running') AND (NOT usage_unknown) "
            "AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND cache_read_tokens IS NULL AND cache_creation_tokens IS NULL "
            "AND cost_usd_micro IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'ambiguous', 'cancelled') "
            "AND usage_unknown AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND cache_read_tokens IS NULL AND cache_creation_tokens IS NULL "
            "AND cost_usd_micro IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'ambiguous', 'cancelled') "
            "AND (NOT usage_unknown) AND input_tokens IS NOT NULL "
            "AND output_tokens IS NOT NULL AND cache_read_tokens IS NOT NULL "
            "AND cache_creation_tokens IS NOT NULL AND cost_usd_micro IS NOT NULL))",
            name="ck_ai_routing_attempt_usage_complete",
        ),
        Index(
            "ix_ai_routing_attempt_deployment_created", "deployment_id", "created_at"
        ),
        Index("ix_ai_routing_attempt_status_created", "status", "created_at"),
        Index("ix_ai_routing_attempt_usage_event", "usage_event_id"),
        Index("ix_ai_routing_attempt_claude_call", "claude_call_log_id"),
    )
