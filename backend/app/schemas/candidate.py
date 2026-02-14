from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class CandidateCreate(BaseModel):
    email: EmailStr
    full_name: Optional[str] = Field(default=None, max_length=200)
    position: Optional[str] = Field(default=None, max_length=200)
    work_email: Optional[EmailStr] = None
    company_name: Optional[str] = Field(default=None, max_length=200)
    company_size: Optional[str] = Field(default=None, max_length=100)
    lead_source: Optional[str] = Field(default=None, max_length=100)
    marketing_consent: Optional[bool] = True


class CandidateUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=200)
    position: Optional[str] = Field(default=None, max_length=200)
    work_email: Optional[EmailStr] = None
    company_name: Optional[str] = Field(default=None, max_length=200)
    company_size: Optional[str] = Field(default=None, max_length=100)
    lead_source: Optional[str] = Field(default=None, max_length=100)
    marketing_consent: Optional[bool] = None


class CandidateResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    position: Optional[str] = None
    work_email: Optional[str] = None
    company_name: Optional[str] = None
    company_size: Optional[str] = None
    lead_source: Optional[str] = None
    marketing_consent: Optional[bool] = True
    organization_id: int

    # Document status
    cv_filename: Optional[str] = None
    cv_uploaded_at: Optional[datetime] = None
    cv_text_preview: Optional[str] = None
    job_spec_filename: Optional[str] = None
    job_spec_uploaded_at: Optional[datetime] = None
    job_spec_text_preview: Optional[str] = None

    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentUploadResponse(BaseModel):
    success: bool = True
    candidate_id: int
    doc_type: str
    filename: str
    text_preview: str
    uploaded_at: datetime
