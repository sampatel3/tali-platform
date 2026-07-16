"""Source-of-truth log of every Anthropic API call the platform makes.

Written by ``MeteredAnthropicClient`` *before* the SDK response is
handed back to the caller. Unconditional — no early-return, no
exception, no ``metering={"skip": True}`` can suppress the write.

Pairs with ``UsageEvent`` as a two-table design:
- ``claude_call_log`` = what was called (model, tokens, cost). Cannot
  be bypassed by application code.
- ``UsageEvent`` = why it was called (feature, role_id, entity_id,
  agent_run_id). Written by application code where context exists.
  ``UsageEvent.id`` is referenced from ``claude_call_log.usage_event_id``
  when attribution succeeds; a NULL FK is the canonical
  "metering attribution gap" signal.

The reconciliation against Anthropic billing now compares
``claude_call_log`` totals against the admin API totals — drift between
those two is a real metering bug. Any divergence between
``claude_call_log`` and ``UsageEvent`` (call_log row with NULL
usage_event_id) is an attribution bug: the call happened, was billed,
recorded as raw cost — just not enriched with feature/role context.
"""
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class ClaudeCallLog(Base):
    __tablename__ = "claude_call_log"

    id = Column(BigInteger, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    model = Column(String, nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    cache_read_tokens = Column(Integer, default=0, nullable=False)
    cache_creation_tokens = Column(Integer, default=0, nullable=False)
    # Anthropic prompt-cache writes have two TTLs that bill at different
    # rates (5m: 1.25× input, 1h: 2.00× input). ``cache_creation_tokens``
    # is the total; this column carries the 1-hour portion so pricing
    # can split it correctly. NULL on rows written before #387 — treated
    # as "unknown split" and priced at the conservative 1.25× default.
    cache_creation_1h_tokens = Column(Integer, default=0, nullable=True)
    cost_usd_micro = Column(BigInteger, default=0, nullable=False)
    feature_hint = Column(String, nullable=True)
    # 'ok' | 'sdk_error' | 'sdk_ambiguous_error' | 'no_usage_on_response'
    status = Column(String, default="ok", nullable=False)
    error_reason = Column(Text, nullable=True)
    anthropic_request_id = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    usage_event_id = Column(Integer, ForeignKey("usage_events.id"), nullable=True)

    # B1: error categorization + retry visibility. Failure rows carry an
    # ``error_class`` so dashboards distinguish 429 vs 5xx vs context-length;
    # ``sdk_ambiguous_error`` additionally signals a retained provider-attempt
    # hold. Retries thread together via
    # ``parent_call_log_id`` (when in-process) or ``trace_id`` (when the
    # retry crosses process boundaries).
    error_class = Column(String, nullable=True)  # rate_limit | overloaded | context_length | bad_request | server_error | timeout | network | validation | other
    http_status = Column(Integer, nullable=True)
    retry_attempt = Column(Integer, nullable=False, default=0, server_default="0")
    parent_call_log_id = Column(BigInteger, ForeignKey("claude_call_log.id"), nullable=True)
    trace_id = Column(String, nullable=True)

    organization = relationship("Organization")
    usage_event = relationship("UsageEvent")

    __table_args__ = (
        Index("ix_claude_call_log_org_created", "organization_id", "created_at"),
        Index("ix_claude_call_log_model_created", "model", "created_at"),
        Index("ix_claude_call_log_usage_event_id", "usage_event_id"),
        Index("ix_claude_call_log_error_class_created", "error_class", "created_at"),
        Index("ix_claude_call_log_trace_id", "trace_id"),
    )
