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
    additional_requirements = Column(Text, nullable=True)
    interview_focus = Column(JSON, nullable=True)
    interview_focus_generated_at = Column(DateTime(timezone=True), nullable=True)
    screening_pack_template = Column(JSON, nullable=True)
    tech_interview_pack_template = Column(JSON, nullable=True)
    auto_reject_enabled = Column(Boolean, nullable=True)
    auto_reject_threshold_100 = Column(Integer, nullable=True)
    workable_actor_member_id = Column(String, nullable=True)
    workable_disqualify_reason_id = Column(String, nullable=True)
    auto_reject_note_template = Column(Text, nullable=True)
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
    # (Haiku by default). Set to e.g. ``claude-sonnet-4-5`` for roles where
    # borderline-judgment cycles are worth Sonnet's cost — recruiter-tunable
    # cost/quality knob without touching env vars.
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
