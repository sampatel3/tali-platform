from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class WorkableConfigBase(BaseModel):
    workflow_mode: Literal["manual", "workable_hybrid"] = "manual"
    email_mode: Literal["manual_taali", "workable_preferred_fallback_manual"] = "manual_taali"
    sync_model: Literal["scheduled_pull_only"] = "scheduled_pull_only"
    sync_scope: Literal["open_jobs_active_candidates"] = "open_jobs_active_candidates"
    granted_scopes: List[str] = Field(default_factory=list)
    score_precedence: Literal["workable_first"] = "workable_first"
    default_sync_mode: Literal["metadata", "full"] = "full"
    sync_interval_minutes: int = Field(default=30, ge=5, le=1440)
    invite_stage_name: str = Field(default="", min_length=0, max_length=200)
    auto_reject_enabled: bool = False
    auto_reject_threshold_100: Optional[int] = Field(default=None, ge=0, le=100)
    workable_actor_member_id: Optional[str] = Field(default=None, max_length=200)
    workable_disqualify_reason_id: Optional[str] = Field(default=None, max_length=200)
    auto_reject_note_template: Optional[str] = Field(default=None, max_length=4000)


class WorkableConfigUpdate(BaseModel):
    workflow_mode: Optional[Literal["manual", "workable_hybrid"]] = None
    email_mode: Optional[Literal["manual_taali", "workable_preferred_fallback_manual"]] = None
    sync_model: Optional[Literal["scheduled_pull_only"]] = None
    sync_scope: Optional[Literal["open_jobs_active_candidates"]] = None
    score_precedence: Optional[Literal["workable_first"]] = None
    default_sync_mode: Optional[Literal["metadata", "full"]] = None
    sync_interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    invite_stage_name: Optional[str] = Field(default=None, min_length=0, max_length=200)
    auto_reject_enabled: Optional[bool] = None
    auto_reject_threshold_100: Optional[int] = Field(default=None, ge=0, le=100)
    workable_actor_member_id: Optional[str] = Field(default=None, max_length=200)
    workable_disqualify_reason_id: Optional[str] = Field(default=None, max_length=200)
    auto_reject_note_template: Optional[str] = Field(default=None, max_length=4000)


class FirefliesConfig(BaseModel):
    connected: bool = False
    has_api_key: bool = False
    webhook_secret_configured: bool = False
    owner_email: Optional[str] = None
    invite_email: Optional[str] = None
    single_account_mode: bool = True


class FirefliesConfigUpdate(BaseModel):
    api_key: Optional[str] = Field(default=None, min_length=0, max_length=2000)
    webhook_secret: Optional[str] = Field(default=None, min_length=0, max_length=2000)
    owner_email: Optional[str] = Field(default=None, min_length=0, max_length=255)
    invite_email: Optional[str] = Field(default=None, min_length=0, max_length=255)
    single_account_mode: Optional[bool] = None


class WorkspaceSettings(BaseModel):
    candidate_facing_brand: Optional[str] = Field(default=None, max_length=200)
    primary_domain: Optional[str] = Field(default=None, max_length=200)
    locale: str = Field(default="English (US)", min_length=2, max_length=80)


class WorkspaceSettingsUpdate(BaseModel):
    candidate_facing_brand: Optional[str] = Field(default=None, min_length=0, max_length=200)
    primary_domain: Optional[str] = Field(default=None, min_length=0, max_length=200)
    locale: Optional[str] = Field(default=None, min_length=2, max_length=80)


class ScoringPolicy(BaseModel):
    prompt_quality: bool = True
    error_recovery: bool = True
    independence: bool = True
    context_utilization: bool = True
    design_thinking: bool = True
    time_to_first_signal: bool = False


class ScoringPolicyUpdate(BaseModel):
    prompt_quality: Optional[bool] = None
    error_recovery: Optional[bool] = None
    independence: Optional[bool] = None
    context_utilization: Optional[bool] = None
    design_thinking: Optional[bool] = None
    time_to_first_signal: Optional[bool] = None


class AiToolingConfig(BaseModel):
    claude_enabled: bool = True
    cursor_inline_enabled: bool = False
    no_ai_baseline_enabled: bool = True
    claude_credit_per_candidate_usd: float = Field(default=12.0, ge=0, le=1000)
    session_timeout_minutes: int = Field(default=60, ge=15, le=240)


class AiToolingConfigUpdate(BaseModel):
    claude_enabled: Optional[bool] = None
    cursor_inline_enabled: Optional[bool] = None
    no_ai_baseline_enabled: Optional[bool] = None
    claude_credit_per_candidate_usd: Optional[float] = Field(default=None, ge=0, le=1000)
    session_timeout_minutes: Optional[int] = Field(default=None, ge=15, le=240)


class NotificationPreferences(BaseModel):
    candidate_updates: bool = True
    daily_digest: bool = True
    panel_reminders: bool = True
    sync_failures: bool = True


class NotificationPreferencesUpdate(BaseModel):
    candidate_updates: Optional[bool] = None
    daily_digest: Optional[bool] = None
    panel_reminders: Optional[bool] = None
    sync_failures: Optional[bool] = None


class OrgResponse(BaseModel):
    id: int
    name: str
    slug: str
    workable_connected: bool
    workable_subdomain: Optional[str] = None
    workable_config: WorkableConfigBase = Field(default_factory=WorkableConfigBase)
    fireflies_config: FirefliesConfig = Field(default_factory=FirefliesConfig)
    workable_last_sync_at: Optional[datetime] = None
    workable_last_sync_status: Optional[str] = None
    workable_last_sync_summary: Optional[dict] = None
    active_claude_model: str
    active_claude_scoring_model: str
    plan: str
    assessments_used: int
    assessments_limit: Optional[int] = None
    billing_provider: str = "lemon"
    credits_balance: int = 0
    default_assessment_duration_minutes: int = 30
    invite_email_template: Optional[str] = None
    default_additional_requirements: Optional[str] = None
    workspace_settings: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    scoring_policy: ScoringPolicy = Field(default_factory=ScoringPolicy)
    ai_tooling_config: AiToolingConfig = Field(default_factory=AiToolingConfig)
    notification_preferences: NotificationPreferences = Field(default_factory=NotificationPreferences)
    allowed_email_domains: List[str] = Field(default_factory=list)
    sso_enforced: bool = False
    saml_enabled: bool = False
    saml_metadata_url: Optional[str] = None
    candidate_feedback_enabled: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}


class OrgUpdate(BaseModel):
    name: Optional[str] = None
    workable_config: Optional[WorkableConfigUpdate] = None
    fireflies_config: Optional[FirefliesConfigUpdate] = None
    workspace_settings: Optional[WorkspaceSettingsUpdate] = None
    scoring_policy: Optional[ScoringPolicyUpdate] = None
    ai_tooling_config: Optional[AiToolingConfigUpdate] = None
    notification_preferences: Optional[NotificationPreferencesUpdate] = None
    allowed_email_domains: Optional[List[str]] = None
    sso_enforced: Optional[bool] = None
    saml_enabled: Optional[bool] = None
    saml_metadata_url: Optional[str] = None
    candidate_feedback_enabled: Optional[bool] = None
    default_assessment_duration_minutes: Optional[int] = Field(default=None, ge=15, le=180)
    invite_email_template: Optional[str] = Field(default=None, max_length=10000)
    default_additional_requirements: Optional[str] = Field(default=None, max_length=12000)


class WorkableConnect(BaseModel):
    code: str


class WorkableTokenConnect(BaseModel):
    access_token: str = Field(min_length=20, max_length=1000)
    subdomain: str = Field(min_length=1, max_length=100)
    read_only: bool = True
