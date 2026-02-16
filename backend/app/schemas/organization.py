from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class WorkableConfigBase(BaseModel):
    workflow_mode: Literal["manual", "workable_hybrid"] = "manual"
    email_mode: Literal["manual_taali", "workable_preferred_fallback_manual"] = "manual_taali"
    sync_model: Literal["scheduled_pull_only"] = "scheduled_pull_only"
    sync_scope: Literal["open_jobs_active_candidates"] = "open_jobs_active_candidates"
    score_precedence: Literal["workable_first"] = "workable_first"
    sync_interval_minutes: int = Field(default=30, ge=5, le=1440)
    invite_stage_name: str = Field(default="", min_length=0, max_length=200)


class WorkableConfigUpdate(BaseModel):
    workflow_mode: Optional[Literal["manual", "workable_hybrid"]] = None
    email_mode: Optional[Literal["manual_taali", "workable_preferred_fallback_manual"]] = None
    sync_model: Optional[Literal["scheduled_pull_only"]] = None
    sync_scope: Optional[Literal["open_jobs_active_candidates"]] = None
    score_precedence: Optional[Literal["workable_first"]] = None
    sync_interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    invite_stage_name: Optional[str] = Field(default=None, min_length=0, max_length=200)


class OrgResponse(BaseModel):
    id: int
    name: str
    slug: str
    workable_connected: bool
    workable_subdomain: Optional[str] = None
    workable_config: WorkableConfigBase = Field(default_factory=WorkableConfigBase)
    workable_last_sync_at: Optional[datetime] = None
    workable_last_sync_status: Optional[str] = None
    workable_last_sync_summary: Optional[dict] = None
    plan: str
    assessments_used: int
    assessments_limit: Optional[int] = None
    billing_provider: str = "lemon"
    credits_balance: int = 0
    allowed_email_domains: List[str] = Field(default_factory=list)
    sso_enforced: bool = False
    saml_enabled: bool = False
    saml_metadata_url: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class OrgUpdate(BaseModel):
    name: Optional[str] = None
    workable_config: Optional[WorkableConfigUpdate] = None
    allowed_email_domains: Optional[List[str]] = None
    sso_enforced: Optional[bool] = None
    saml_enabled: Optional[bool] = None
    saml_metadata_url: Optional[str] = None


class WorkableConnect(BaseModel):
    code: str


class WorkableTokenConnect(BaseModel):
    access_token: str = Field(min_length=20, max_length=1000)
    subdomain: str = Field(min_length=1, max_length=100)
    read_only: bool = True
