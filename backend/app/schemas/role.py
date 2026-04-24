from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, EmailStr, Field

ROLE_DESCRIPTION_MAX_LENGTH = 20000
ROLE_ADDITIONAL_REQUIREMENTS_MAX_LENGTH = 12000


class InterviewFocusQuestion(BaseModel):
    question: str
    what_to_listen_for: list[str] = Field(default_factory=list)
    concerning_signals: list[str] = Field(default_factory=list)


class InterviewFocus(BaseModel):
    role_summary: Optional[str] = None
    manual_screening_triggers: list[str] = Field(default_factory=list)
    questions: list[InterviewFocusQuestion] = Field(default_factory=list)


class ScoringCriterion(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)
    source: Literal["job_spec", "recruiter"] = "job_spec"
    order: int = Field(ge=1, le=200)
    weight: Optional[int] = Field(default=None, ge=1, le=100)


class ScoringCriterionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    source: Literal["job_spec", "recruiter"] = "recruiter"
    weight: Optional[int] = Field(default=None, ge=1, le=100)


class ScoringCriterionUpdate(BaseModel):
    text: Optional[str] = Field(default=None, min_length=1, max_length=500)
    source: Optional[Literal["job_spec", "recruiter"]] = None
    order: Optional[int] = Field(default=None, ge=1, le=200)
    weight: Optional[int] = Field(default=None, ge=1, le=100)


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=ROLE_DESCRIPTION_MAX_LENGTH)
    additional_requirements: Optional[str] = Field(default=None, max_length=ROLE_ADDITIONAL_REQUIREMENTS_MAX_LENGTH)


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=ROLE_DESCRIPTION_MAX_LENGTH)
    additional_requirements: Optional[str] = Field(default=None, max_length=ROLE_ADDITIONAL_REQUIREMENTS_MAX_LENGTH)
    reject_threshold: Optional[int] = Field(default=None, ge=0, le=100)


class RoleResponse(BaseModel):
    id: int
    organization_id: int
    name: str
    description: Optional[str] = None
    additional_requirements: Optional[str] = None
    source: Optional[str] = "manual"
    workable_job_id: Optional[str] = None
    job_spec_filename: Optional[str] = None
    job_spec_text: Optional[str] = None
    job_spec_uploaded_at: Optional[datetime] = None
    job_spec_present: bool = False
    scoring_criteria: list[ScoringCriterion] = Field(default_factory=list)
    reject_threshold: int = 60
    interview_focus: Optional[InterviewFocus] = None
    interview_focus_generated_at: Optional[datetime] = None
    tasks_count: int = 0
    applications_count: int = 0
    stage_counts: dict[str, int] = Field(default_factory=dict)
    active_candidates_count: int = 0
    last_candidate_activity_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class RoleTaskLinkRequest(BaseModel):
    task_id: int = Field(gt=0)


class ApplicationCreate(BaseModel):
    candidate_email: EmailStr
    candidate_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    candidate_position: Optional[str] = Field(default=None, max_length=200)
    status: Optional[str] = Field(default="applied", max_length=100)
    pipeline_stage: Optional[Literal["applied", "invited", "in_assessment", "review"]] = None
    application_outcome: Optional[Literal["open", "rejected", "withdrawn", "hired"]] = None
    notes: Optional[str] = Field(default=None, max_length=4000)


class ApplicationUpdate(BaseModel):
    status: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Deprecated compatibility field. Prefer pipeline_stage + application_outcome.",
    )
    pipeline_stage: Optional[Literal["applied", "invited", "in_assessment", "review"]] = None
    application_outcome: Optional[Literal["open", "rejected", "withdrawn", "hired"]] = None
    expected_version: Optional[int] = Field(default=None, ge=1)
    notes: Optional[str] = Field(default=None, max_length=4000)
    candidate_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    candidate_position: Optional[str] = Field(default=None, max_length=200)


class ApplicationResponse(BaseModel):
    id: int
    organization_id: int
    candidate_id: int
    role_id: int
    status: str = Field(description="Deprecated compatibility mirror of pipeline_stage + application_outcome.")
    pipeline_stage: Literal["applied", "invited", "in_assessment", "review"] = "applied"
    pipeline_stage_updated_at: Optional[datetime] = None
    pipeline_stage_source: Literal["system", "recruiter", "sync"] = "system"
    application_outcome: Literal["open", "rejected", "withdrawn", "hired"] = "open"
    application_outcome_updated_at: Optional[datetime] = None
    external_refs: Optional[dict[str, Any]] = None
    external_stage_raw: Optional[str] = None
    external_stage_normalized: Optional[str] = None
    integration_sync_state: Optional[dict[str, Any]] = None
    pipeline_external_drift: bool = False
    version: int = 1
    notes: Optional[str] = None
    candidate_email: str
    candidate_name: Optional[str] = None
    candidate_position: Optional[str] = None
    cv_filename: Optional[str] = None
    cv_uploaded_at: Optional[datetime] = None
    cv_match_score: Optional[float] = None
    cv_match_details: Optional[dict] = None
    cv_match_scored_at: Optional[datetime] = None
    source: Optional[str] = "manual"
    workable_candidate_id: Optional[str] = None
    workable_stage: Optional[str] = None
    workable_score_raw: Optional[float] = None
    workable_score: Optional[float] = None
    workable_score_source: Optional[str] = None
    rank_score: Optional[float] = None
    # Rich candidate profile fields
    candidate_headline: Optional[str] = None
    candidate_image_url: Optional[str] = None
    candidate_location: Optional[str] = None
    candidate_phone: Optional[str] = None
    candidate_profile_url: Optional[str] = None
    candidate_social_profiles: Optional[list] = None
    candidate_tags: Optional[list] = None
    candidate_skills: Optional[list] = None
    candidate_education: Optional[list] = None
    candidate_experience: Optional[list] = None
    candidate_summary: Optional[str] = None
    candidate_workable_created_at: Optional[datetime] = None
    workable_sourced: Optional[bool] = None
    workable_profile_url: Optional[str] = None
    workable_enriched: Optional[bool] = None
    taali_score: Optional[float] = None
    score_mode: Optional[str] = None
    valid_assessment_id: Optional[int] = None
    valid_assessment_status: Optional[str] = None
    score_summary: Optional[dict[str, Any]] = None
    role_reject_threshold: Optional[int] = None
    below_role_threshold: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ApplicationDetailResponse(ApplicationResponse):
    """Application with optional full CV text for viewer."""
    cv_text: Optional[str] = None
    assessment_preview: Optional[dict[str, Any]] = None
    assessment_history: list[dict[str, Any]] = Field(default_factory=list)


class ApplicationCvUploadResponse(BaseModel):
    success: bool = True
    application_id: int
    filename: str
    text_preview: str
    uploaded_at: datetime


class ApplicationStageUpdate(BaseModel):
    pipeline_stage: Literal["applied", "invited", "in_assessment", "review"]
    expected_version: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=2000)
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class ApplicationOutcomeUpdate(BaseModel):
    application_outcome: Literal["open", "rejected", "withdrawn", "hired"]
    expected_version: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=2000)
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class ApplicationEventResponse(BaseModel):
    id: int
    application_id: int
    organization_id: int
    event_type: str
    from_stage: Optional[str] = None
    to_stage: Optional[str] = None
    from_outcome: Optional[str] = None
    to_outcome: Optional[str] = None
    actor_type: str
    actor_id: Optional[int] = None
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None
    created_at: datetime


class AssessmentFromApplicationCreate(BaseModel):
    task_id: int = Field(gt=0)
    duration_minutes: int = Field(default=30, ge=15, le=180)


class AssessmentRetakeCreate(AssessmentFromApplicationCreate):
    void_reason: Optional[str] = Field(default=None, max_length=2000)


class ApplicationBulkRejectRequest(BaseModel):
    application_ids: list[int] = Field(min_length=1, max_length=200)
    reason: Optional[str] = Field(default=None, max_length=2000)


class ApplicationBulkRejectResponse(BaseModel):
    success: bool = True
    updated_count: int = 0
    application_ids: list[int] = Field(default_factory=list)
    workable_sync_attempted: int = 0
