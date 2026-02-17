from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON
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
    stripe_customer_id = Column(String)
    stripe_subscription_id = Column(String)
    billing_provider = Column(String, default="lemon")
    billing_config = Column(JSON, nullable=True)
    credits_balance = Column(Integer, default=0)
    claude_api_key_encrypted = Column(String, nullable=True)
    claude_api_key_last_rotated_at = Column(DateTime(timezone=True), nullable=True)
    plan = Column(String, default="pay_per_use")
    assessments_used = Column(Integer, default=0)
    assessments_limit = Column(Integer, default=None)
    # Enterprise access controls
    allowed_email_domains = Column(JSON, nullable=True)  # ["company.com", "subsidiary.org"]
    sso_enforced = Column(Boolean, default=False)
    saml_enabled = Column(Boolean, default=False)
    saml_metadata_url = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    users = relationship("User", back_populates="organization")
    assessments = relationship("Assessment", back_populates="organization")
    roles = relationship("Role", cascade="all, delete-orphan")
    applications = relationship("CandidateApplication", cascade="all, delete-orphan")
    credit_ledger_entries = relationship("BillingCreditLedger", back_populates="organization", cascade="all, delete-orphan")