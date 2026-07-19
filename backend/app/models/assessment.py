from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..platform.database import Base
import enum


class AssessmentStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    COMPLETED_DUE_TO_TIMEOUT = "completed_due_to_timeout"
    EXPIRED = "expired"


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        Index(
            "uq_assessments_candidate_role_active",
            "candidate_id",
            "role_id",
            unique=True,
            sqlite_where=text("role_id IS NOT NULL AND is_voided = 0"),
            postgresql_where=text("role_id IS NOT NULL AND is_voided = false"),
        ),
        Index(
            "ix_assessments_invite_email_recovery",
            "invite_email_status",
            "invite_email_next_attempt_at",
        ),
        Index(
            "ix_assessments_invite_workable_handoff_recovery",
            "invite_workable_handoff_status",
            "invite_workable_handoff_next_attempt_at",
        ),
        Index(
            "ix_assessments_workable_result_delivery_recovery",
            "workable_result_delivery_status",
            "workable_result_delivery_next_attempt_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), index=True)
    role_id = Column(Integer, ForeignKey("roles.id"), index=True, nullable=True)
    application_id = Column(Integer, ForeignKey("candidate_applications.id"), index=True, nullable=True)
    token = Column(String, unique=True, index=True)
    status = Column(Enum(AssessmentStatus), default=AssessmentStatus.PENDING)
    duration_minutes = Column(Integer, default=30)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    score = Column(Float)
    tests_passed = Column(Integer)
    tests_total = Column(Integer)
    code_quality_score = Column(Float)
    time_efficiency_score = Column(Float)
    ai_usage_score = Column(Float)
    test_results = Column(JSON)
    ai_prompts = Column(JSON)
    code_snapshots = Column(JSON)
    timeline = Column(JSON)
    e2b_session_id = Column(String)
    assessment_repo_url = Column(String, nullable=True)
    assessment_branch = Column(String, nullable=True)
    clone_command = Column(Text, nullable=True)
    final_repo_state = Column(String, nullable=True)
    git_evidence = Column(JSON, nullable=True)
    completed_due_to_timeout = Column(Boolean, default=False, nullable=False)
    ai_mode = Column(String, default="claude_cli_terminal", nullable=False)
    cli_transcript = Column(JSON, nullable=True)
    is_timer_paused = Column(Boolean, default=False, nullable=False)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    pause_reason = Column(String, nullable=True)
    total_paused_seconds = Column(Integer, default=0, nullable=False)
    workable_candidate_id = Column(String)
    workable_job_id = Column(String)
    posted_to_workable = Column(Boolean, default=False)
    posted_to_workable_at = Column(DateTime(timezone=True))
    # Secret-free, exact delivery receipt for the OAuth Workable result note.
    # ``posted_to_workable`` remains the backwards-compatible success marker;
    # these fields distinguish broker loss, a safe retry, and an ambiguous
    # provider call so a worker crash can never blindly duplicate the note.
    workable_result_delivery_status = Column(String, nullable=True)
    workable_result_delivery_receipt = Column(JSON, nullable=True)
    workable_result_delivery_next_attempt_at = Column(
        DateTime(timezone=True), nullable=True
    )
    workable_result_delivery_claimed_at = Column(
        DateTime(timezone=True), nullable=True
    )
    # Workable Assessments-Provider (marketplace add-on): the per-assessment
    # results callback Workable supplies on POST /assessments, and the marker
    # set once the completed result has been enqueued for push-back. Distinct
    # from posted_to_workable above (the OAuth-integration comment writeback).
    workable_callback_url = Column(String, nullable=True)
    workable_provider_pushed_at = Column(DateTime(timezone=True), nullable=True)
    invite_channel = Column(String, default="manual", nullable=False)
    invite_sent_at = Column(DateTime(timezone=True), nullable=True)
    # Email-delivery tracking (Resend). ``invite_email_id`` is the Resend
    # message id captured at send time; the Resend webhook correlates
    # delivered/opened/bounced/complained events back to this row by it.
    # ``invite_email_status`` is the latest lifecycle state. Provider delivery
    # moves sent → delivered → opened/clicked (or bounced/complained).
    # The durable outbox additionally uses queued/retrying/retry_wait while a
    # transient provider/broker failure is recovering; only permanent provider
    # refusal is recorded as failed.
    invite_email_id = Column(String, nullable=True, index=True)
    invite_email_status = Column(String, nullable=True)
    invite_email_send_generation = Column(
        Integer, default=0, server_default="0", nullable=False
    )
    invite_email_confirmed_generation = Column(Integer, nullable=True)
    invite_email_retry_count = Column(
        Integer, default=0, server_default="0", nullable=False
    )
    invite_email_next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    invite_email_claimed_at = Column(DateTime(timezone=True), nullable=True)
    invite_email_last_error = Column(Text, nullable=True)
    invite_email_reply_to = Column(String, nullable=True)
    # Frozen local-pipeline transition intent.  Creating/queueing an invite must
    # not claim that the candidate was contacted, so the producer records the
    # requested actor/source here and the Resend-success writeback applies it in
    # the same transaction that stamps ``invite_sent_at``.
    invite_pipeline_transition = Column(JSON, nullable=True)
    # Workable handoff is a second durable outbox, deliberately independent of
    # email delivery.  Once Resend accepts a generation, the handoff worker can
    # retry stage + note without ever submitting that email generation again.
    invite_workable_handoff_status = Column(String, nullable=True)
    invite_workable_handoff_generation = Column(Integer, nullable=True)
    invite_workable_handoff_stage = Column(String, nullable=True)
    invite_workable_handoff_retry_count = Column(
        Integer, default=0, server_default="0", nullable=False
    )
    invite_workable_handoff_next_attempt_at = Column(
        DateTime(timezone=True), nullable=True
    )
    invite_workable_handoff_claimed_at = Column(DateTime(timezone=True), nullable=True)
    invite_workable_handoff_last_error = Column(Text, nullable=True)
    invite_workable_stage_moved_at = Column(DateTime(timezone=True), nullable=True)
    invite_workable_note_posted_at = Column(DateTime(timezone=True), nullable=True)
    invite_delivered_at = Column(DateTime(timezone=True), nullable=True)
    invite_opened_at = Column(DateTime(timezone=True), nullable=True)
    invite_bounced_at = Column(DateTime(timezone=True), nullable=True)
    # First hit of the candidate preview/welcome page — the funnel step
    # between "opened the invite email" and "clicked Start".
    preview_viewed_at = Column(DateTime(timezone=True), nullable=True)
    credit_consumed_at = Column(DateTime(timezone=True), nullable=True)
    # Prompt scoring fields (Phase 2)
    prompt_quality_score = Column(Float, nullable=True)
    prompt_efficiency_score = Column(Float, nullable=True)
    independence_score = Column(Float, nullable=True)
    context_utilization_score = Column(Float, nullable=True)
    design_thinking_score = Column(Float, nullable=True)
    debugging_strategy_score = Column(Float, nullable=True)
    written_communication_score = Column(Float, nullable=True)
    learning_velocity_score = Column(Float, nullable=True)
    error_recovery_score = Column(Float, nullable=True)
    requirement_comprehension_score = Column(Float, nullable=True)
    calibration_score = Column(Float, nullable=True)
    calibration_warmup_prompt = Column(Text, nullable=True)
    prompt_fraud_flags = Column(JSON, nullable=True)
    prompt_analytics = Column(JSON, nullable=True)
    browser_focus_ratio = Column(Float, nullable=True)
    tab_switch_count = Column(Integer, default=0)
    time_to_first_prompt_seconds = Column(Integer, nullable=True)
    cv_file_url = Column(String, nullable=True)
    cv_filename = Column(String, nullable=True)
    cv_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    final_score = Column(Float, nullable=True)
    assessment_score = Column(Float, nullable=True)
    taali_score = Column(Float, nullable=True)
    score_breakdown = Column(JSON, nullable=True)
    score_weights_used = Column(JSON, nullable=True)
    flags = Column(JSON, nullable=True)
    scored_at = Column(DateTime(timezone=True), nullable=True)
    total_duration_seconds = Column(Integer, nullable=True)
    total_prompts = Column(Integer, nullable=True)
    total_input_tokens = Column(Integer, nullable=True)
    total_output_tokens = Column(Integer, nullable=True)
    tests_run_count = Column(Integer, nullable=True)
    tests_pass_count = Column(Integer, nullable=True)
    # CV-Job fit matching (Phase 2)
    cv_job_match_score = Column(Float, nullable=True)
    cv_job_match_details = Column(JSON, nullable=True)
    manual_evaluation = Column(JSON, nullable=True)  # { category_scores: { key: { score, evidence } }, overall_score?, strengths?, improvements? }
    is_voided = Column(Boolean, default=False, nullable=False)
    voided_at = Column(DateTime(timezone=True), nullable=True)
    void_reason = Column(Text, nullable=True)
    superseded_by_assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=True)
    interview_debrief_json = Column(JSON, nullable=True)
    interview_debrief_generated_at = Column(DateTime(timezone=True), nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    demo_track = Column(String, nullable=True)
    demo_profile = Column(JSON, nullable=True)
    # A/B experiment assignment (Phase 2 trial). Co-located on the assessment row;
    # written in the same txn that creates the assessment.
    experiment_id = Column(
        Integer, ForeignKey("assessment_experiments.id"), nullable=True, index=True
    )
    experiment_arm_id = Column(
        Integer, ForeignKey("assessment_experiment_arms.id"), nullable=True, index=True
    )
    assignment_method = Column(String, nullable=True)  # random|forced|single_task_default|no_experiment
    assignment_key = Column(String, nullable=True)  # stable hash input used for the draw (audit)
    knob_variant_applied = Column(JSON, nullable=True)  # frozen copy of the arm's knob_overrides
    score_weights_override = Column(JSON, nullable=True)  # per-assessment weights knob (NULL = use task.score_weights)
    calibration_enabled = Column(Boolean, nullable=True)  # NULL = inherit global/task default
    # Scoring/runtime failure flags (hardening). NULL/False = no failure.
    scoring_failed = Column(Boolean, default=False, nullable=True)
    scoring_partial = Column(Boolean, default=False, nullable=True)
    repo_capture_failed = Column(Boolean, default=False, nullable=True)
    test_parse_error = Column(Boolean, default=False, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="assessments")
    candidate = relationship("Candidate", back_populates="assessments")
    task = relationship("Task")
    role = relationship("Role", back_populates="assessments")
    application = relationship("CandidateApplication", back_populates="assessments")
