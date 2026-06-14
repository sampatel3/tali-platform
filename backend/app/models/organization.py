from sqlalchemy import BigInteger, Column, Integer, String, Boolean, DateTime, JSON, Text
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
    # Workable Assessments-Provider (marketplace add-on) per-org settings, e.g.
    # {"callback_auth_token": "...", "enabled": true}. Distinct from
    # workable_config (the OAuth pull/write integration).
    workable_provider_config = Column(JSON, nullable=True)
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
    billing_provider = Column(String, default="stripe")
    billing_config = Column(JSON, nullable=True)
    # Balance in micro-credits (1 credit = $0.000001 USD). Was Integer (whole
    # credits, 25 AED each) under the legacy Lemon-Squeezy model; switched to
    # BigInteger usage-based on 2026-04-29.
    credits_balance = Column(BigInteger, default=0)
    # Anthropic Workspace key (Admin-API-provisioned, Taali-owned). All Claude
    # calls for this org route through this key; Anthropic dashboard reports
    # cost per workspace. Provisioned lazily on first billed action.
    anthropic_workspace_id = Column(String, nullable=True)
    anthropic_workspace_key_encrypted = Column(Text, nullable=True)
    anthropic_workspace_provisioning_failed_at = Column(DateTime(timezone=True), nullable=True)
    default_assessment_duration_minutes = Column(Integer, default=30, nullable=False)
    invite_email_template = Column(Text, nullable=True)
    # Settings → AI agent defaults (HANDOFF settings.md). Read once at role
    # create time; per-role overrides win and existing roles are never
    # rewritten when these change. The chip list itself lives in
    # ``org_criteria`` — the legacy ``default_role_requirements`` JSON +
    # ``default_additional_requirements`` text columns were dropped in
    # alembic 067.
    default_role_budget_cents = Column(Integer, nullable=True)
    default_score_threshold = Column(Integer, nullable=True)
    # Workspace-wide spend cap (cents). When projected month-end > cap, the
    # agent pauses new invites — surfaced via the "Spend over budget"
    # notification. NULL means no cap configured.
    monthly_spend_cap_cents = Column(Integer, nullable=True)
    workspace_settings = Column(JSON, nullable=True)
    scoring_policy = Column(JSON, nullable=True)
    ai_tooling_config = Column(JSON, nullable=True)
    notification_preferences = Column(JSON, nullable=True)
    plan = Column(String, default="pay_per_use")
    # Enterprise access controls
    allowed_email_domains = Column(JSON, nullable=True)  # ["company.com", "subsidiary.org"]
    sso_enforced = Column(Boolean, default=False)
    saml_enabled = Column(Boolean, default=False)
    two_factor_required = Column(Boolean, default=False, nullable=False)
    saml_metadata_url = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    users = relationship("User", back_populates="organization")
    assessments = relationship("Assessment", back_populates="organization")
    roles = relationship("Role", cascade="all, delete-orphan")
    criteria = relationship(
        "OrganizationCriterion",
        back_populates="organization",
        cascade="all, delete-orphan",
        order_by="OrganizationCriterion.ordering",
    )
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
