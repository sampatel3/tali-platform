from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base

# How the brief was captured.
BRIEF_SOURCE_CONVERSATIONAL = "conversational"  # no-login agent chat with the hiring manager
BRIEF_SOURCE_TRANSCRIPT = "transcript"  # kickoff-call transcript (Fireflies/upload)
BRIEF_SOURCE_UPLOAD = "upload"  # existing JD / notes
BRIEF_SOURCES = (
    BRIEF_SOURCE_CONVERSATIONAL,
    BRIEF_SOURCE_TRANSCRIPT,
    BRIEF_SOURCE_UPLOAD,
)

# Lifecycle: draft (intake in progress) -> submitted (hiring manager finished)
# -> applied (materialized onto the role: name/description/criteria).
BRIEF_STATUS_DRAFT = "draft"
BRIEF_STATUS_SUBMITTED = "submitted"
BRIEF_STATUS_APPLIED = "applied"
BRIEF_STATUSES = (BRIEF_STATUS_DRAFT, BRIEF_STATUS_SUBMITTED, BRIEF_STATUS_APPLIED)


class RoleBrief(Base):
    """The AI-native requisition: a structured hiring brief attached to a (draft)
    role, captured via a no-login conversational intake with the hiring manager.

    Two purposes:
      1. A job PROFILE (title / location / comp / criteria) that materializes onto
         the role + role_criterion.
      2. A hiring BRIEF — the rich, agent-extracted context (success profile,
         weighted priorities, dealbreakers, calibration exemplars, sourcing
         signals, assessment focus, process) that downstream agents (scoring,
         pre-screen, assessment selection, candidate search, decision) read. This
         is the single source of hiring intent, not just a spec.
    """

    __tablename__ = "role_briefs"
    __table_args__ = (
        Index("ix_role_briefs_org_role", "organization_id", "role_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    # Null until the brief is materialized onto a role (recruiter publishes).
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True)
    status = Column(String, nullable=False, server_default=BRIEF_STATUS_DRAFT)
    source_kind = Column(String, nullable=True)

    # --- Consultancy: the client this requisition is opened for + economics ---
    # Null for direct (non-consultancy) hiring. ``client_rate`` is the annual
    # rate billed to the client, in the brief's currency (AED by default);
    # margin (client_rate - cost) is computed, never stored.
    client_id = Column(
        Integer, ForeignKey("clients.id"), nullable=True, index=True
    )
    client_rate = Column(Integer, nullable=True)
    # Unguessable token for the SCOPED, no-login CLIENT INTAKE share link: the
    # recruiter sends it to their client, who describes the role via the same
    # conversational agent (company/economics layers hidden, no pay questions).
    # Null until minted; minted once and reused. Served with NO auth at
    # ``/api/v1/public/intake/{token}``.
    client_intake_token = Column(String, nullable=True, unique=True, index=True)

    # Short, human-friendly requisition code (e.g. ``TAL-7K2QF``), minted on the
    # first publish and reused. Embedded in the spec the recruiter copies into
    # Workable; the read-sync scans the imported job description for it to link
    # the synced Workable role back to this requisition (Workable has no
    # job-creation API, so this paste-the-code bridge is the link mechanism).
    ref_code = Column(String, nullable=True, unique=True, index=True)

    # --- Job profile (structured, queryable) ---
    title = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    department = Column(String, nullable=True)
    location_city = Column(String, nullable=True)
    location_country = Column(String, nullable=True)
    workplace_type = Column(String, nullable=True)  # onsite | remote | hybrid
    employment_type = Column(String, nullable=True)
    seniority = Column(String, nullable=True)
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    salary_currency = Column(String, nullable=True)
    salary_period = Column(String, nullable=True)
    openings = Column(Integer, nullable=True)
    target_start = Column(String, nullable=True)

    # --- Criteria (agent-proposed; materialize to role_criterion + knockouts) ---
    must_haves = Column(JSON, nullable=True)
    preferred = Column(JSON, nullable=True)
    dealbreakers = Column(JSON, nullable=True)

    # --- Hiring brief: the agent-context layers (goal c) ---
    success_profile = Column(Text, nullable=True)
    priorities = Column(JSON, nullable=True)  # [{factor, weight}] weighted trade-offs
    tradeoffs = Column(JSON, nullable=True)  # explicit "prefer X over Y" statements
    calibration_exemplars = Column(JSON, nullable=True)  # [{kind: good|bad, description}]
    sourcing_signals = Column(JSON, nullable=True)  # {companies, industries, titles}
    assessment_focus = Column(JSON, nullable=True)  # what to actually test
    process = Column(JSON, nullable=True)  # {rounds, autonomy_level, urgency}
    evp = Column(JSON, nullable=True)  # selling points / tone for the JD + comms

    # --- Org-template extension fields (keys with no RoleBrief column) ---
    # The org's requisition spec template may add fields that don't map to a
    # column (e.g. "visa_sponsorship"); the chat captures those here keyed by
    # the template field key. Defaults to an empty dict so callers never get
    # ``None`` back.
    custom_fields = Column(JSON, nullable=False, server_default="{}", default=dict)

    # --- Conversation transcript (the chat intake) ---
    # The captured conversation: a list of
    # ``{"role": "user"|"assistant", "content": str,
    #    "attachments": [{"name": str, "kind": "image"|"transcript"|"file"}]}``.
    messages = Column(JSON, nullable=False, server_default="[]", default=list)

    # --- Provenance / intake working state ---
    raw_input = Column(Text, nullable=True)  # transcript / pasted JD / notes
    agent_state = Column(JSON, nullable=True)  # intake agent memory + open questions
    completeness = Column(Integer, nullable=True)  # 0..100 agent's coverage estimate

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    role = relationship("Role")
    client = relationship("Client")
    # The PUBLIC job page published from this brief (one-per-brief, None until
    # the recruiter publishes). View-only: the page is written via
    # ``publish_job_page`` (which sets brief_id directly), never through here.
    job_page = relationship(
        "JobPage",
        uselist=False,
        viewonly=True,
    )
