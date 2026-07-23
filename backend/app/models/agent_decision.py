from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    select,
    text,
)
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
# The Hub keeps ``processing`` rows visible but read-only, so accepted actions
# remain acknowledged while their writeback runs. If that writeback ultimately
# fails, the dispatch task flips the row back to ``pending`` so the recruiter
# can review and explicitly retry it rather than losing the decision.
AGENT_DECISION_STATUSES = (
    "pending",
    "processing",
    "approved",
    "overridden",
    "reverted_for_feedback",
    "discarded",
    "expired",
)
AGENT_DECISION_ACTIVE_STATUSES = (
    "pending",
    "processing",
    "reverted_for_feedback",
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
        # A decision belongs to one logical role/candidate subject. The same
        # candidate may have an independent card in another role, while owner
        # and direct physical applications can never create duplicate cards in
        # the same role. Migration 188 builds the PostgreSQL equivalent.
        Index(
            "uq_agent_decisions_active_org_role_candidate",
            "organization_id",
            "role_id",
            "candidate_id",
            unique=True,
            sqlite_where=text(
                "status IN ('pending', 'processing', 'reverted_for_feedback')"
            ),
            postgresql_where=text(
                "status IN ('pending', 'processing', 'reverted_for_feedback')"
            ),
        ),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), index=True, nullable=False)
    application_id = Column(Integer, ForeignKey("candidate_applications.id"), index=True, nullable=False)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), index=True, nullable=False)
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
    # Durable resolution intent/result metadata.  In particular, the selected
    # ATS destination is written before deferred dispatch so historical queries
    # do not depend on an ephemeral Celery payload.
    resolution_metadata = Column(JSON, nullable=False, default=dict, server_default="{}")

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


@event.listens_for(AgentDecision, "before_insert")
@event.listens_for(AgentDecision, "before_update")
def _resolve_candidate_identity(_mapper, connection, target) -> None:
    """Mirror migration 188's DB invariant for ORM-created test schemas."""

    application_id = getattr(target, "application_id", None)
    if application_id is None:
        return
    from .candidate_application import CandidateApplication

    application_identity = connection.execute(
        select(
            CandidateApplication.candidate_id,
            CandidateApplication.organization_id,
        ).where(CandidateApplication.id == int(application_id))
    ).one_or_none()
    if application_identity is None:
        raise ValueError("AgentDecision.application_id does not exist")
    resolved_candidate_id, application_organization_id = application_identity
    if target.candidate_id is None:
        target.candidate_id = int(resolved_candidate_id)
    elif int(target.candidate_id) != int(resolved_candidate_id):
        raise ValueError(
            "candidate_id does not own AgentDecision.application_id"
        )
    if int(target.organization_id) != int(application_organization_id):
        raise ValueError(
            "organization_id does not own AgentDecision.application_id"
        )

    from .role import Role

    role_organization_id = connection.scalar(
        select(Role.organization_id).where(Role.id == int(target.role_id))
    )
    if role_organization_id is None:
        raise ValueError("AgentDecision.role_id does not exist")
    if int(target.organization_id) != int(role_organization_id):
        raise ValueError("organization_id does not own AgentDecision.role_id")
