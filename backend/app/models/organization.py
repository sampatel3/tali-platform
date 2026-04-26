from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..platform.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, index=True)
    workable_subdomain = Column(String)
    workable_access_token = Column(String)
    workable_refresh_token = Column(String)
    workable_connected = Column(Boolean, default=False)
    workable_config = Column(JSON)
    workable_last_sync_at = Column(DateTime(timezone=True), nullable=True)
    workable_last_sync_status = Column(String, nullable=True)
    workable_last_sync_summary = Column(JSON, nullable=True)
    workable_sync_started_at = Column(DateTime(timezone=True), nullable=True)
    workable_sync_progress = Column(JSON, nullable=True)
    workable_sync_cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    fireflies_api_key_encrypted = Column(String, nullable=True)
    fireflies_webhook_secret = Column(String, nullable=True)
    fireflies_owner_email = Column(String, nullable=True)
    fireflies_invite_email = Column(String, nullable=True)
    fireflies_single_account_mode = Column(Boolean, default=True, nullable=False)
    stripe_customer_id = Column(String)
    stripe_subscription_id = Column(String)
    billing_provider = Column(String, default="lemon")
    billing_config = Column(JSON, nullable=True)
    credits_balance = Column(Integer, default=0)
    default_assessment_duration_minutes = Column(Integer, default=30, nullable=False)
    invite_email_template = Column(Text, nullable=True)
    # Org-wide default for the per-role additional_requirements field used by
    # CV scoring. Auto-copied into role.additional_requirements on role
    # create (manual + Workable import) when the role's own value is empty.
    default_additional_requirements = Column(Text, nullable=True)
    workspace_settings = Column(JSON, nullable=True)
    scoring_policy = Column(JSON, nullable=True)
    ai_tooling_config = Column(JSON, nullable=True)
    notification_preferences = Column(JSON, nullable=True)
    plan = Column(String, default="pay_per_use")
    assessments_used = Column(Integer, default=0)
    assessments_limit = Column(Integer, default=None)
    # Enterprise access controls
    allowed_email_domains = Column(JSON, nullable=True)  # ["company.com", "subsidiary.org"]
    sso_enforced = Column(Boolean, default=False)
    saml_enabled = Column(Boolean, default=False)
    saml_metadata_url = Column(String, nullable=True)
    candidate_feedback_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    users = relationship("User", back_populates="organization")
    assessments = relationship("Assessment", back_populates="organization")
    roles = relationship("Role", cascade="all, delete-orphan")
    applications = relationship("CandidateApplication", back_populates="organization", cascade="all, delete-orphan")
    credit_ledger_entries = relationship("BillingCreditLedger", back_populates="organization", cascade="all, delete-orphan")
    workable_sync_runs = relationship("WorkableSyncRun", back_populates="organization", cascade="all, delete-orphan")

    @property
    def active_claude_model(self) -> str:
        from ..platform.config import settings
        return settings.resolved_claude_model

    @property
    def active_claude_scoring_model(self) -> str:
        from ..platform.config import settings
        return settings.resolved_claude_scoring_model
