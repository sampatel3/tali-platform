from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


AGENT_RUN_TRIGGERS = ("event", "cron", "manual")
AGENT_RUN_STATUSES = ("running", "succeeded", "failed", "budget_paused", "aborted")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(BigInteger, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), index=True, nullable=False)
    trigger = Column(String, nullable=False)
    trigger_event_id = Column(Integer, ForeignKey("candidate_application_events.id"), nullable=True)
    status = Column(String, nullable=False, default="running")
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cache_read_tokens = Column(Integer, nullable=False, default=0)
    cache_creation_tokens = Column(Integer, nullable=False, default=0)
    total_cost_micro_usd = Column(BigInteger, nullable=False, default=0)
    decisions_emitted = Column(Integer, nullable=False, default=0)
    tools_called = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    model_version = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    agent_state_snapshot = Column(JSON, nullable=True)
    # B7 step 1: instrumentation. Count of tool-use rounds the cycle
    # actually executed (vs the static MAX_TOOL_ROUNDS cap). Used post-
    # deploy to histogram round counts and tune the cap downward if
    # p95 is well below the limit — trims worst-case spend without
    # losing functionality. NULL on pre-B7 rows.
    rounds_executed = Column(Integer, nullable=True)

    role = relationship("Role")
    decisions = relationship("AgentDecision", back_populates="agent_run")
