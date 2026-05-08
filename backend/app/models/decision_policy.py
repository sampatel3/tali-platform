"""``decision_policies`` — versioned, deterministic verdict configuration.

The DecisionPolicy is the only learnable surface in the agent stack.
Scoring engines stay stable (recruiter-trust contract); the orchestrator
plans; sub-agents wrap; this row says "given these inputs, what's the
verdict?" — pure-Python evaluation, no LLM in the verdict path.

One row per (organization, role) where ``role_id IS NULL`` is the org
default. Per-role overrides apply as a shallow merge on top. Recruiter
intent enters as a third, ephemeral overlay at evaluation time and is
never persisted onto a row.

The ``revision_id`` column pins each policy to its ``rubric_revisions``
audit row so:

    decision (now) -> agent_decisions.evidence['policy_revision_id']
                  -> rubric_revisions row (cause + feedback_ids)
                  -> the exact policy_json that produced this verdict

Old decisions are never re-scored when a new policy lands — they stay
attributed to the revision they were judged under.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class DecisionPolicy(Base):
    __tablename__ = "decision_policies"

    id = Column(BigInteger, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    # ``role_id`` null = org default; non-null = per-role override that the
    # engine shallow-merges on top of the org default at evaluation time.
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True)
    revision_id = Column(
        BigInteger, ForeignKey("rubric_revisions.id"), nullable=False
    )

    # The full policy specification. Validated against
    # ``decision_policy.schema.PolicyJson`` on write; the engine always
    # parses through that schema so unknown fields are rejected early.
    policy_json = Column(JSON, nullable=False)

    # ``activated_at`` is the load-active gate: the engine selects rows
    # where (deactivated_at IS NULL AND activated_at IS NOT NULL).
    # Retune-proposed revisions land with both NULL; an admin activating
    # them flips ``activated_at`` and ``deactivated_at`` on the prior
    # row in a single transaction (see retuner / Hub activate route).
    activated_at = Column(DateTime(timezone=True), nullable=True)
    deactivated_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    role = relationship("Role")
    revision = relationship("RubricRevision")
