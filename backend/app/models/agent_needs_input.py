"""``agent_needs_input`` — open recruiter questions raised by the agent.

When the orchestrator's survey finds a gap it can't fill on its own
(empty must-have intent slot, no monthly budget cap, ambiguous
threshold, two equally-plausible candidates) it writes one row here.
The recruiter answers inline on the role page (Phase 6 Hub UI) and the
next agent cycle picks the answer up via ``read_pending_recruiter_inputs``.

Idempotency: the orchestrator should not spam — the
``ask_recruiter`` action upserts on (role_id, kind), so re-asking the
same question re-uses the existing open row instead of creating a
fresh one.

Lifecycle:

    created_at
        │
        ├─ resolved_at + response   (recruiter answered)
        │
        ├─ dismissed_at              (recruiter said "skip" or
        │                             agent gave up after N cycles)
        │
        └─ open                      (still pending)
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


# Canonical ``kind`` values. The agent sets these when it asks; the Hub
# UI keys card layouts off them. Add new kinds here so frontend +
# backend stay in sync.
NEEDS_INPUT_KINDS = (
    # NeedsInput is *only* for role-level clarifying questions the agent
    # asks to unblock its own work (intent slots, budget, threshold).
    # Per-candidate HITL gates ("approve this send", "approve this
    # resend") used to live here but now flow through ``agent_decisions``
    # with decision_type=send_assessment / resend_assessment_invite —
    # they're per-candidate verdicts and belong in the decisions queue.
    "intent_slot_missing",                  # role has empty must_have / preferred / etc.
    "intent_clarification",                 # agent judges current intent thin / ambiguous on a specific dimension
    "monthly_budget_missing",               # role.monthly_usd_budget_cents is null
    "threshold_ambiguous",                  # role.score_threshold not set + cohort spread is high
    "task_assignment_missing",              # role has no assessment task linked
    "candidate_tie_break",                  # two near-identical candidates, recruiter picks
    "other",
)


class AgentNeedsInput(Base):
    __tablename__ = "agent_needs_input"

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)

    # See NEEDS_INPUT_KINDS. Free-form string (no enum) so adding a kind
    # doesn't require a migration; the application layer validates.
    kind = Column(String(64), nullable=False)
    # Optional per-subject discriminator. For ``send_assessment_approval``
    # and other per-candidate kinds this is the application_id so the
    # ``ask_recruiter`` idempotency key keeps one row per candidate,
    # not one row per (role, kind). NULL keeps the legacy role-wide
    # semantics for kinds like ``monthly_budget_missing``.
    subject_id = Column(BigInteger, nullable=True)
    prompt = Column(Text, nullable=False)
    # Mutually exclusive: ``options`` is a list of {value, label} for
    # multiple-choice; ``schema`` describes a free-text/numeric input.
    options = Column(JSON, nullable=True)
    # ``schema`` is a SQL keyword in some dialects but works fine as a
    # column name; quoting handled by SQLAlchemy.
    response_schema = Column("schema", JSON, nullable=True)

    agent_run_id = Column(
        BigInteger, ForeignKey("agent_runs.id"), nullable=True
    )
    rationale = Column(Text, nullable=True)

    resolved_by_user_id = Column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    response = Column(JSON, nullable=True)
    dismissed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    role = relationship("Role")
    organization = relationship("Organization")
    agent_run = relationship("AgentRun")
    resolved_by = relationship("User", foreign_keys=[resolved_by_user_id])

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None and self.dismissed_at is None
