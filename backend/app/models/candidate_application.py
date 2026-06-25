from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class CandidateApplication(Base):
    __tablename__ = "candidate_applications"
    __table_args__ = (
        UniqueConstraint("candidate_id", "role_id", name="uq_candidate_role_application"),
        Index(
            "ix_candidate_applications_org_role_status",
            "organization_id",
            "role_id",
            "status",
        ),
        Index("ix_candidate_applications_cv_uploaded_at", "cv_uploaded_at"),
        Index("ix_candidate_applications_deleted_at", "deleted_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), index=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), index=True, nullable=False)
    status = Column(String, default="applied", nullable=False)
    pipeline_stage = Column(String, default="applied", nullable=False)
    pipeline_stage_updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    pipeline_stage_source = Column(String, default="system", nullable=False)
    application_outcome = Column(String, default="open", nullable=False)
    application_outcome_updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Recruiter's manually recorded decision (advance/hold/reject + rationale,
    # confidence, next steps) with a draft/submitted lifecycle, version, author
    # stamp and change history. Used for candidates with no assessment linked
    # (e.g. rejected at CV stage); the assessment-backed equivalent lives on
    # Assessment.manual_evaluation. Shape: see evaluation_result_service
    # .build_application_decision / normalize_stored_application_decision.
    manual_decision = Column(JSON, nullable=True)
    external_refs = Column(JSON, nullable=True)
    external_stage_raw = Column(String, nullable=True)
    external_stage_normalized = Column(String, nullable=True)
    integration_sync_state = Column(JSON, nullable=True)
    version = Column(Integer, default=1, nullable=False)
    notes = Column(Text, nullable=True)
    source = Column(String, default="manual", nullable=False)
    # ATS source attribution (P0): 2-level source strategy/name + crediting user
    # (mirrors Greenhouse/Workable source objects). Nullable; populated by native
    # intake (P1) and Workable import.
    source_strategy = Column(String, nullable=True)
    source_name = Column(String, nullable=True)
    credited_to_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Structured disposition reason (reject/withdraw) from the per-org
    # disqualification_reasons catalog + its denormalized category. Distinct from
    # the free-text auto_reject_reason below.
    disposition_reason_id = Column(
        Integer, ForeignKey("disqualification_reasons.id"), nullable=True
    )
    disposition_category = Column(String, nullable=True)
    # Denormalized coarse category of the current pipeline_stage (see
    # PipelineStage.kind / STAGE_KINDS). Backfilled from the canonical stage
    # mapping in migration 120; kept in sync with pipeline_stage in the P0
    # migrate step. Lets analytics/automation group by ATS-generic stage kind.
    stage_kind = Column(String, nullable=True)
    workable_candidate_id = Column(String, nullable=True, index=True)
    workable_stage = Column(String, nullable=True)
    # When Taali itself last wrote workable_stage (a recruiter advance / move).
    # The candidate sync uses this as a local-write-wins guard so it doesn't
    # clobber a just-moved stage with a stale bulk-list snapshot. See
    # workable stage sync_service guard + _workable_decision_summary / op_runner.
    workable_stage_local_write_at = Column(DateTime(timezone=True), nullable=True)
    workable_sourced = Column(Boolean, nullable=True)
    workable_profile_url = Column(String, nullable=True)
    workable_score_raw = Column(Float, nullable=True)
    workable_score = Column(Float, nullable=True)
    workable_score_source = Column(String, nullable=True)
    # Workable disqualification overlay. In Workable a disqualified candidate
    # keeps their stage (e.g. "Technical Interview") and gets this flag laid on
    # top — it is not a stage. Captured so the UI can show a "Disqualified"
    # badge and the candidate can be parked in Tali's terminal `advanced` stage.
    workable_disqualified = Column(Boolean, nullable=True)
    workable_disqualified_at = Column(DateTime(timezone=True), nullable=True)
    rank_score = Column(Float, nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    # Candidate CV scoped to this role application
    cv_file_url = Column(String, nullable=True)
    cv_filename = Column(String, nullable=True)
    cv_text = Column(Text, nullable=True)
    cv_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    # Parsed CV sections (cv_parsing module). Populated after _try_fetch_cv_from_workable
    # extracts text. Shape: ParsedCV in app/cv_parsing/schemas.py.
    cv_sections = Column(JSON, nullable=True)
    cv_match_score = Column(Float, nullable=True)
    cv_match_details = Column(JSON, nullable=True)
    cv_match_scored_at = Column(DateTime(timezone=True), nullable=True)
    # The "best available" display/rank score. Full cv_match scoring
    # overwrites this for ranking (via refresh_pre_screening_fields), so it
    # tracks role-fit once scored — NOT a durable record of the pre-screen
    # verdict. Use ``genuine_pre_screen_score_100`` for the actual cheap
    # pre-screen score; this column stays the directory/detail display value.
    pre_screen_score_100 = Column(Float, nullable=True)
    # The genuine cheap pre-screen score, written once by the pre-screen LLM
    # and NEVER overwritten by full cv_match scoring. This is the durable
    # pre-screen verdict the decision engine's pre_screen gate reads.
    genuine_pre_screen_score_100 = Column(Float, nullable=True)
    requirements_fit_score_100 = Column(Float, nullable=True)
    pre_screen_recommendation = Column(String, nullable=True)
    pre_screen_evidence = Column(JSON, nullable=True)
    # Populated when the most recent pre-screen attempt errored
    # (Anthropic credit exhaustion, network timeout, JSON parse failure,
    # etc.). When set, ``pre_screen_score_100`` + ``cv_match_score`` are
    # kept NULL and the UI surfaces "agent couldn't score — retry
    # needed" instead of falling through to v3 cv_match (which would
    # silently mirror an unrelated CV-fit score into the gate field).
    pre_screen_error_reason = Column(Text, nullable=True)
    # Set whenever the pre-screen LLM completes (whether passed or "Below
    # threshold"). Used by batch actions to skip already-pre-screened apps
    # whose CV hasn't changed since.
    pre_screen_run_at = Column(DateTime(timezone=True), nullable=True)
    auto_reject_state = Column(String, nullable=True)
    auto_reject_reason = Column(Text, nullable=True)
    auto_reject_triggered_at = Column(DateTime(timezone=True), nullable=True)
    screening_pack = Column(JSON, nullable=True)
    tech_interview_pack = Column(JSON, nullable=True)
    screening_interview_summary = Column(JSON, nullable=True)
    tech_interview_summary = Column(JSON, nullable=True)
    interview_evidence_summary = Column(JSON, nullable=True)
    taali_score_cache_100 = Column(Float, nullable=True)
    assessment_score_cache_100 = Column(Float, nullable=True)
    role_fit_score_cache_100 = Column(Float, nullable=True)
    score_mode_cache = Column(String, nullable=True)
    score_cached_at = Column(DateTime(timezone=True), nullable=True)

    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    candidate = relationship("Candidate", back_populates="applications")
    organization = relationship("Organization", back_populates="applications")
    role = relationship("Role", back_populates="applications")
    assessments = relationship("Assessment", back_populates="application")
    events = relationship(
        "CandidateApplicationEvent",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="CandidateApplicationEvent.created_at.desc()",
    )
    interviews = relationship(
        "ApplicationInterview",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationInterview.linked_at.desc()",
    )
    score_jobs = relationship(
        "CvScoreJob",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="CvScoreJob.queued_at.desc()",
    )
