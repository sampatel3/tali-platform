from sqlalchemy import Boolean, JSON, Column, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base

role_tasks = Table(
    "role_tasks",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("organization_id", "workable_job_id", name="uq_roles_org_workable_job"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    source = Column(String, default="manual", nullable=False)
    workable_job_id = Column(String, nullable=True, index=True)
    workable_job_data = Column(JSON, nullable=True)
    job_spec_file_url = Column(String, nullable=True)
    job_spec_filename = Column(String, nullable=True)
    job_spec_text = Column(Text, nullable=True)
    job_spec_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    # Recruiter intent lives in ``role_criteria`` rows now (see alembic
    # 066 + 068). The legacy ``additional_requirements`` text column was
    # dropped in 068; readers consume :func:`render_role_intent_block`
    # / :func:`render_role_intent_lines`.

    # Workspace criterion ids the recruiter has explicitly removed from
    # this role. Sync workspace skips these; "Show hidden" surfaces them
    # so the recruiter can add them back.
    suppressed_org_criterion_ids = Column(JSON, nullable=True)
    interview_focus = Column(JSON, nullable=True)
    interview_focus_generated_at = Column(DateTime(timezone=True), nullable=True)
    screening_pack_template = Column(JSON, nullable=True)
    tech_interview_pack_template = Column(JSON, nullable=True)
    # Role-level cache of AI-generated tech screening questions. Generated
    # once per role and reused across every candidate on that role — the
    # previous per-candidate path was firing ~300 LLM calls/day with
    # minimal benefit (most questions overlap across candidates on the
    # same role). Invalidated on job_spec_text or criteria changes; the
    # ``signature`` column lets the regenerator detect drift without
    # walking the entire role state.
    tech_questions_cached = Column(JSON, nullable=True)
    tech_questions_cached_at = Column(DateTime(timezone=True), nullable=True)
    tech_questions_signature = Column(String(length=64), nullable=True)
    # ``manual`` — recruiter sets the threshold by hand.
    # ``auto``   — agent computes a recommendation from the role's score
    # distribution + any advance/hire labels each time the threshold is
    # consulted (see ``services.auto_threshold_service``).
    auto_reject_threshold_mode = Column(
        String(length=8), nullable=False, default="manual", server_default="manual"
    )
    workable_actor_member_id = Column(String, nullable=True)
    starred_for_auto_sync = Column(
        Boolean,
        nullable=False,
        server_default="false",
        default=False,
        index=True,
    )
    agentic_mode_enabled = Column(
        Boolean,
        nullable=False,
        server_default="false",
        default=False,
        index=True,
    )
    agent_action_allowlist = Column(JSON, nullable=True)
    agent_token_budget_per_cycle = Column(Integer, nullable=True)
    agent_decision_budget_per_cycle = Column(Integer, nullable=True)
    # Universal monthly USD cap covering ALL Anthropic spend on this role
    # (scoring, pre-screen, assessment, agent). Required when activating
    # agentic mode; optional otherwise. Stored as cents.
    monthly_usd_budget_cents = Column(Integer, nullable=True)
    # 0..100 minimum total score for the role's auto-shortlist. Below this
    # threshold the candidate is flagged for recruiter review. Seeded from
    # ``organization.default_score_threshold`` at role-create time; recruiter
    # overrides on the role page win.
    score_threshold = Column(Integer, nullable=True)
    agent_paused_at = Column(DateTime(timezone=True), nullable=True)
    agent_paused_reason = Column(Text, nullable=True)
    agent_last_run_at = Column(DateTime(timezone=True), nullable=True)
    agent_calibration = Column(JSON, nullable=True)
    # Per-role Anthropic model override. Null = use settings.resolved_claude_model
    # DEPRECATED (May 2026 single-version cleanup): per §8.1 of
    # recruitment_system_architecture.md the canonical model-selection
    # surface is ``config/agent_models.yaml`` (loaded by
    # ``app.agent_runtime.model_config``). This per-role override
    # remains as a runtime escape hatch for the orchestrator agent's
    # own model only; the five sub-agents resolve their model via
    # ``get_model_for_agent("<name>")``. Sunset target: when all
    # remaining callers route through model_config and the
    # orchestrator's model is also moved into agent_models.yaml.
    agent_model = Column(String, nullable=True)
    # Event-debounce window. When set and in the future, an event-triggered
    # agent cycle is already scheduled for this role and additional events
    # within the window must NOT enqueue another. The agent task clears it
    # on entry so events arriving during the cycle start a new window.
    # See app/agent_runtime/event_debounce.py.
    agent_next_run_at = Column(DateTime(timezone=True), nullable=True)
    # Cached "do high scorers cluster" signals (skills/companies/titles/schools
    # over-represented in the top decile vs the full applicant pool). Computed
    # lazily by ``cohort_signals_service.compute_cohort_signals`` and refreshed
    # when stale (>1 hour). See the agent's get_cohort_signals tool.
    agent_cohort_signals = Column(JSON, nullable=True)
    agent_cohort_signals_at = Column(DateTime(timezone=True), nullable=True)
    # Per-role HITL toggles. Both default False so every candidate-
    # affecting action lands in the Decision Hub for human approval
    # unless the recruiter explicitly opts into automation.
    #
    # ``auto_reject``: when True, reject decisions execute immediately —
    # the pre-screen Celery auto-reject path disqualifies in Workable
    # without queueing, and the agent's queue_reject_decision /
    # queue_skip_assessment_reject_decision tools call the same path
    # ``approve_decision.run`` uses on recruiter approval, instead of
    # creating a pending AgentDecision card.
    #
    # ``auto_promote``: when True, the agent sends assessments and
    # advances candidates to interview without approval. When False the
    # agent's queue_advance_decision tool produces an AgentDecision card
    # and ``send_assessment`` opens an ``agent_needs_input`` approval row.
    auto_reject = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    auto_promote = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    tasks = relationship("Task", secondary=role_tasks)
    applications = relationship("CandidateApplication", back_populates="role", cascade="all, delete-orphan")
    assessments = relationship("Assessment", back_populates="role")
    criteria = relationship(
        "RoleCriterion",
        back_populates="role",
        cascade="all, delete-orphan",
        order_by="RoleCriterion.ordering",
    )
