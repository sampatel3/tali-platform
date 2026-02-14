from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class InterviewFocusQuestion(BaseModel):
    question: str
    what_to_listen_for: list[str] = Field(default_factory=list)
    concerning_signals: list[str] = Field(default_factory=list)


class InterviewFocus(BaseModel):
    role_summary: Optional[str] = None
    manual_screening_triggers: list[str] = Field(default_factory=list)
    questions: list[InterviewFocusQuestion] = Field(default_factory=list)


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)


class RoleResponse(BaseModel):
    id: int
    organization_id: int
    name: str
    description: Optional[str] = None
    job_spec_filename: Optional[str] = None
    job_spec_uploaded_at: Optional[datetime] = None
    interview_focus: Optional[InterviewFocus] = None
    interview_focus_generated_at: Optional[datetime] = None
    tasks_count: int = 0
    applications_count: int = 0
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
    notes: Optional[str] = Field(default=None, max_length=4000)


class ApplicationUpdate(BaseModel):
    status: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=4000)
    candidate_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    candidate_position: Optional[str] = Field(default=None, max_length=200)


class ApplicationResponse(BaseModel):
    id: int
    organization_id: int
    candidate_id: int
    role_id: int
    status: str
    notes: Optional[str] = None
    candidate_email: str
    candidate_name: Optional[str] = None
    candidate_position: Optional[str] = None
    cv_filename: Optional[str] = None
    cv_uploaded_at: Optional[datetime] = None
    cv_match_score: Optional[float] = None
    cv_match_details: Optional[dict] = None
    cv_match_scored_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ApplicationCvUploadResponse(BaseModel):
    success: bool = True
    application_id: int
    filename: str
    text_preview: str
    uploaded_at: datetime


class AssessmentFromApplicationCreate(BaseModel):
    task_id: int = Field(gt=0)
    duration_minutes: int = Field(default=30, ge=15, le=180)
