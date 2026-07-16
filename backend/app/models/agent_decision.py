from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


AGENT_DECISION_TYPES = (
    "advance_to_interview",
    "reject",
    "skip_assessment_reject",
    # Per-candidate HITL gates that used to live as ``agent_needs_input``
    # rows (kinds ``send_assessment_approval`` / ``resend_assessment_invite_approval``).
    # They're per-candidate verdicts on a candidate-facing action, not
    # role-level clarifying questions, so they belong in the decisions
    # queue alongside advance/reject. Approving routes through
    # ``approve_decision.run`` which calls the underlying action.
    "send_assessment",
    "resend_assessment_invite",
    # Multi-agent upgrade (§6.3): the policy may emit
    # ``escalate_low_confidence`` instead of a confident verdict when
    # sub-agent uncertainties exceed thresholds or scores disagree
    # sharply. The Hub treats this as a distinct queue lane — no
    # auto-action, recruiter must adjudicate.
    "escalate_low_confidence",
)
# ``reverted_for_feedback`` is set by the "Send back & teach" action — the
# decision goes back into the queue with the reviewer's correction note
# attached, while a ``decision_feedback`` row carries the training signal.
# ``processing`` is the in-flight state between a recruiter approving a
# decision and the background dispatch task confirming the Workable writeback.
# The Hub queue only ever shows ``pending``, so a ``processing`` row vanishes
# from the queue (optimistic removal); if the Workable writeback ultimately
# fails the dispatch task flips it back to ``pending`` so it returns to the
# queue rather than being lost.
AGENT_DECISION_STATUSES = (
    "pending",
    "processing",
    "approved",
    "overridden",
    "reverted_for_feedback",
    "discarded",
    "expired",
)
# ``human_disposition`` records *what kind* of human action resolved the
# decision, regardless of the lifecycle state. ``approved``/``overridden``
# mirror ``status``; ``taught`` is set when the resolver path was the teach
# loop (regardless of whether the decision is back to pending or applied).
AGENT_DECISION_HUMAN_DISPOSITIONS = (
    "approved",
    "overridden",
    "taught",
)


class AgentDecision(Base):
    __tablename__ = "agent_decisions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_agent_decisions_idempotency_key"),
        Index("ix_agent_decisions_application_status", "application_id", "status"),
        Index("ix_agent_decisions_role_status", "role_id", "status"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), index=True, nullable=False)
    application_id = Column(Integer, ForeignKey("candidate_applications.id"), index=True, nullable=False)
    agent_run_id = Column(BigInteger, ForeignKey("agent_runs.id"), nullable=True, index=True)

    decision_type = Column(String, nullable=False)
    recommendation = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)

    reasoning = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=True)
    confidence = Column(Numeric(4, 3), nullable=True)

    model_version = Column(String, nullable=False)
    prompt_version = Column(String, nullable=False)

    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolution_note = Column(Text, nullable=True)
    override_action = Column(String, nullable=True)

    # A recruiter-requested fresh agent cycle is durable even if the broker is
    # unavailable after this decision is discarded. The wrapper task leases
    # this receipt and marks it complete only after the focused cycle returns.
    reevaluation_status = Column(String(24), nullable=True, index=True)
    reevaluation_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    reevaluation_next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    reevaluation_lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    reevaluation_error = Column(String(500), nullable=True)

    # Hub-era fields (migration 063):
    #   feedback_id: links to the latest decision_feedback row when the human
    #     disposition was ``taught``.
    #   human_disposition: orthogonal to status — answers "what kind of human
    #     action resolved this," used by the Hub to compute teach- vs override-
    #     rate without joining decision_feedback every time.
    #   snoozed_until: pending rows are hidden from the queue until this time.
    # ``feedback_id`` and ``decision_feedback.decision_id`` form a mutual FK
    # cycle (a decision points at its current feedback row, the feedback
    # points back at the decision). Mark this side ``use_alter`` so
    # SQLAlchemy can sort table creation/deletion deterministically.
    feedback_id = Column(
        BigInteger,
        ForeignKey("decision_feedback.id", use_alter=True, name="fk_agent_decisions_feedback_id"),
        nullable=True,
    )
    human_disposition = Column(String, nullable=True)
    snoozed_until = Column(DateTime(timezone=True), nullable=True)

    # v10 capability-flag audit snapshot. Dict of {capability_name: bool}
    # for every capability registered at decision time. Empty dict ≡
    # "v1/v2 era, no v10 capabilities active". Persisted by
    # ``queue_decision.run`` reading from ``app.capabilities.flags``.
    active_capabilities = Column(JSON, nullable=False, default=dict, server_default="{}")

    # Discipline §8.5: per-decision token roll-up populated by
    # ``token_spend_aggregator.aggregate`` at queue time. Shape:
    #   {input_tokens, output_tokens, cache_read_tokens,
    #    cache_creation_tokens, total_micro_usd, by_agent: {...}}
    # Empty dict ≡ "no usage events found for this agent_run_id at
    # queue time" (defensive default, never raises).
    token_spend = Column(JSON, nullable=False, default=dict, server_default="{}")

    # A1: input fingerprint snapshot. Populated by
    # ``queue_decision._capture_input_fingerprint`` from the live
    # CandidateApplication + Role at queue time. Drives the read-time
    # staleness service (A2) for pending decisions; preserved verbatim
    # forever on resolved decisions as the immutable audit record.
    # Shape: see ``alembic/versions/092_add_input_fingerprint_*.py``.
    input_fingerprint = Column(JSON, nullable=False, default=dict, server_default="{}")
    # Indexed scalar shortcut for the drift detector — comparing 64-char
    # hex hashes is cheap; pulling JSON values is not.
    criteria_fingerprint = Column(String(64), nullable=True, index=True)
    cv_fingerprint = Column(String(64), nullable=True)

    # C4: cross-cycle dedup key. Hash of
    # ``(application_id, decision_type, criteria_fingerprint,
    #   cv_fingerprint, pre_screen_bucket, cv_match_bucket)``.
    # ``queue_decision.run`` blocks emit when a resolved decision with
    # the same key was created within the last 7 days (approved) or
    # 10 minutes (discarded). Intentional re-emit after inputs change
    # changes the hash and is allowed through. Non-unique so the
    # dedup window logic lives in code, not the schema.
    decision_dedup_key = Column(String(64), nullable=True, index=True)

    idempotency_key = Column(String, nullable=False)

    agent_run = relationship("AgentRun", back_populates="decisions")
    role = relationship("Role")
    application = relationship("CandidateApplication")
