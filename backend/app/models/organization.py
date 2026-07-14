from sqlalchemy import BigInteger, Column, Integer, String, Boolean, DateTime, JSON, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..platform.database import Base

# Per-org ATS posture (P-1/P0). Decides who owns the candidate funnel:
#   standalone       — Taali owns the funnel; Workable disconnected. DEFAULT for
#                      new orgs (matches the MVP_DISABLE_WORKABLE code default).
#   workable_primary — Workable owns the funnel; Taali reads + writes back. The
#                      current live tenant (deeplight-ai) runs in this mode.
#   taali_primary    — Taali owns the funnel but mirrors writes back to Workable;
#                      the transitional mode during a per-org cutover off Workable.
SYNC_MODE_STANDALONE = "standalone"
SYNC_MODE_WORKABLE_PRIMARY = "workable_primary"
SYNC_MODE_TAALI_PRIMARY = "taali_primary"
SYNC_MODES = (
    SYNC_MODE_STANDALONE,
    SYNC_MODE_WORKABLE_PRIMARY,
    SYNC_MODE_TAALI_PRIMARY,
)


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, index=True)
    workable_subdomain = Column(String)
    workable_access_token = Column(String)
    workable_refresh_token = Column(String)
    workable_connected = Column(Boolean, default=False)
    # See SYNC_MODE_* above. Backfilled from workable_connected (connected orgs
    # -> workable_primary, else standalone) in migration 151.
    sync_mode = Column(String, nullable=False, server_default=SYNC_MODE_STANDALONE)
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
    # Bullhorn ATS integration (staging-only until flag-off; see
    # docs/BULLHORN_BUILD_PLAN.md §3). ``bullhorn_client_secret`` and
    # ``bullhorn_refresh_token`` hold Fernet CIPHERTEXT — write with
    # ``encrypt_text(value, settings.SECRET_KEY)`` and read with
    # ``decrypt_text(...)`` (same mechanism as ``fireflies_api_key_encrypted`` /
    # ``anthropic_workspace_key_encrypted``); NEVER store or read them raw. The
    # refresh token is single-use and rotates on every OAuth exchange, so the
    # rotated value MUST be persisted in the same transaction before the new
    # access token is used, or the org is stranded.
    bullhorn_username = Column(String, nullable=True)
    bullhorn_client_id = Column(String, nullable=True)
    bullhorn_client_secret = Column(Text, nullable=True)
    bullhorn_refresh_token = Column(Text, nullable=True)
    bullhorn_rest_url = Column(String, nullable=True)
    bullhorn_connected = Column(Boolean, default=False)
    bullhorn_last_sync_at = Column(DateTime(timezone=True), nullable=True)
    bullhorn_last_sync_status = Column(String, nullable=True)
    bullhorn_last_sync_summary = Column(JSON, nullable=True)
    bullhorn_sync_progress = Column(JSON, nullable=True)
    bullhorn_event_subscription_id = Column(String, nullable=True)
    # Checkpoint for the destructive event-queue read: the requestId of the last
    # fetched batch, so a crash mid-processing can re-fetch the same events
    # instead of losing them.
    bullhorn_event_request_id = Column(String, nullable=True)
    # Mirrors ``workable_config``'s role — poll cadence override, actor defaults
    # (see BullhornConfigBase in schemas/organization.py).
    bullhorn_config = Column(JSON, nullable=True)
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
    # Legacy/reserved workspace-cap value kept for API/database compatibility.
    # It is not an enforced admission control. Active spend controls are funded
    # organization credits plus each role's monthly AI-usage cap; do not expose
    # this field as a hard cap until enforcement is implemented end to end.
    monthly_spend_cap_cents = Column(Integer, nullable=True)
    workspace_settings = Column(JSON, nullable=True)
    # The org's canonical "complete requisition spec" definition that the
    # conversational intake captures against. NULL means "use the built-in
    # DEFAULT_REQUISITION_TEMPLATE" (see requisition_template_service).
    requisition_spec_template = Column(JSON, nullable=True)
    # Cached, auto-derived "About the company" blurb (role-agnostic boilerplate,
    # the same on every spec). Derived once from recent role specs and copied
    # onto each new requisition. NULL = not yet derived; "" = derived, none found.
    company_blurb = Column(Text, nullable=True)
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
    ats_stage_maps = relationship("AtsStageMap", back_populates="organization", cascade="all, delete-orphan")

    @property
    def active_claude_model(self) -> str:
        from ..platform.config import settings
        return settings.resolved_claude_model

    @property
    def active_claude_scoring_model(self) -> str:
        from ..platform.config import settings
        return settings.resolved_claude_scoring_model
