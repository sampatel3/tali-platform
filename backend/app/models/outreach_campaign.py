"""Outreach campaigns — the send/track layer above sourced applications.

A campaign is a single-touch outbound wave for one role: the recruiter builds an
audience (from sourced or past applications), Claude drafts one
message per recipient, the recruiter approves the individual drafts, and only
then are they sent + tracked. No sequences, no reply detection (v1).

Two tables:
- ``outreach_campaigns`` — the wave: role, brief, status, denormalized counts.
- ``outreach_messages`` — one row per recipient: the draft, its approval state,
  the Resend correlation id, and the interest-capture token + lifecycle stamps.

Policy rails encoded here (not debated):
- ``status`` starts at ``pending`` and only reaches ``approved`` on an explicit
  recruiter action; the send task refuses anything not ``approved``.
- ``interest_token`` is minted at row creation (urlsafe random) so the public
  interest-capture link exists the moment a draft does.
- ``UniqueConstraint(campaign_id, email)`` de-dupes within a campaign at the DB
  level (audience-build also filters duplicates before insert).
"""
from __future__ import annotations

import secrets

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


# Campaign lifecycle. draft → generating → ready → sending → sent.
# ``failed`` is the bounded draft-generation terminal state; ``archived`` is a
# user-owned terminal state.
CAMPAIGN_STATUS_DRAFT = "draft"
CAMPAIGN_STATUS_GENERATING = "generating"
CAMPAIGN_STATUS_READY = "ready"
CAMPAIGN_STATUS_SENDING = "sending"
CAMPAIGN_STATUS_SENT = "sent"
CAMPAIGN_STATUS_FAILED = "failed"
CAMPAIGN_STATUS_ARCHIVED = "archived"

CAMPAIGN_STATUSES = (
    CAMPAIGN_STATUS_DRAFT,
    CAMPAIGN_STATUS_GENERATING,
    CAMPAIGN_STATUS_READY,
    CAMPAIGN_STATUS_SENDING,
    CAMPAIGN_STATUS_SENT,
    CAMPAIGN_STATUS_FAILED,
    CAMPAIGN_STATUS_ARCHIVED,
)


# Per-message lifecycle. The approval gate is absolute: only ``approved`` (or a
# post-send tracking state below) may be sent. ``pending`` is the pre-draft /
# rejected resting state; ``draft`` is written-but-unapproved.
MESSAGE_STATUS_PENDING = "pending"
MESSAGE_STATUS_DRAFTING = "drafting"
MESSAGE_STATUS_DRAFT = "draft"
MESSAGE_STATUS_APPROVED = "approved"
MESSAGE_STATUS_QUEUED = "queued"
MESSAGE_STATUS_SENT = "sent"
MESSAGE_STATUS_DELIVERED = "delivered"
MESSAGE_STATUS_OPENED = "opened"
MESSAGE_STATUS_CLICKED = "clicked"
MESSAGE_STATUS_INTERESTED = "interested"
MESSAGE_STATUS_BOUNCED = "bounced"
MESSAGE_STATUS_COMPLAINED = "complained"
MESSAGE_STATUS_FAILED = "failed"
MESSAGE_STATUS_SUPPRESSED = "suppressed"
MESSAGE_STATUS_UNSUBSCRIBED = "unsubscribed"

MESSAGE_STATUSES = (
    MESSAGE_STATUS_PENDING,
    MESSAGE_STATUS_DRAFTING,
    MESSAGE_STATUS_DRAFT,
    MESSAGE_STATUS_APPROVED,
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SENT,
    MESSAGE_STATUS_DELIVERED,
    MESSAGE_STATUS_OPENED,
    MESSAGE_STATUS_CLICKED,
    MESSAGE_STATUS_INTERESTED,
    MESSAGE_STATUS_BOUNCED,
    MESSAGE_STATUS_COMPLAINED,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_SUPPRESSED,
    MESSAGE_STATUS_UNSUBSCRIBED,
)

# Delivery-lifecycle rank so a late-arriving lower event can't downgrade a more
# advanced state (e.g. a 'delivered' webhook landing after the 'clicked' one).
# Failure states (bounced/complained/failed/suppressed) are handled separately
# and always win. ``interested`` (our own interest-capture click) ranks above
# clicked — it's the strongest positive signal.
MESSAGE_STATUS_RANK = {
    MESSAGE_STATUS_SENT: 1,
    MESSAGE_STATUS_DELIVERED: 2,
    MESSAGE_STATUS_OPENED: 3,
    MESSAGE_STATUS_CLICKED: 4,
    MESSAGE_STATUS_INTERESTED: 5,
}


def _mint_interest_token() -> str:
    """Unguessable public interest-capture token, minted at row creation."""
    return secrets.token_urlsafe(32)


class OutreachCampaign(Base):
    __tablename__ = "outreach_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    # The role this campaign sources for. Nullable — a campaign can be role-less
    # (e.g. a general talent-pool re-engagement).
    role_id = Column(Integer, ForeignKey("roles.id"), index=True, nullable=True)

    name = Column(String, nullable=False)
    status = Column(String, nullable=False, server_default=CAMPAIGN_STATUS_DRAFT)
    # Durable retry budget for autonomous draft generation. The worker bumps it
    # once per whole-campaign attempt and stops after its bounded maximum, so an
    # all-failed campaign cannot sit in ``ready`` and block the role forever.
    draft_generation_attempts = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Monotonic compare-and-set fence for every mutation that can change a
    # ready campaign's reviewed outbound snapshot. PostgreSQL also takes a row
    # lock; this revision preserves the same safety boundary on SQLite and
    # protects against stale workers or requests on every database.
    review_revision = Column(Integer, nullable=False, default=0, server_default="0")
    # Recruiter-editable pitch context fed to the drafter.
    brief = Column(Text, nullable=True)
    # CTA target — the role's published JobPage token, resolved at creation if
    # present. When set, the interest click 302s to the public job page.
    job_page_token = Column(String, nullable=True)
    # Provider-neutral application destination captured with the campaign.
    # Native campaigns keep using ``job_page_token``; external ATS campaigns
    # use a validated HTTPS application URL rather than a generic thanks page.
    destination_url = Column(String, nullable=True)
    destination_provider = Column(String, nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Provenance for the agent-first workflow. Manual campaigns remain fully
    # supported, but autonomous preparation is the default path and must be
    # auditable independently from the human outbound approval.
    origin = Column(String, nullable=False, default="manual", server_default="manual")
    prepared_by_agent_run_id = Column(
        BigInteger, ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    idempotency_key = Column(String, nullable=True, unique=True, index=True)
    approved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    # Denormalized rollup: {audience, drafted, approved, sent, delivered,
    # opened, clicked, interested, bounced, failed}. Recomputed opportunistically.
    counts = Column(JSON, nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    messages = relationship(
        "OutreachMessage",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


class OutreachMessage(Base):
    __tablename__ = "outreach_messages"
    __table_args__ = (
        # One message per recipient per campaign — de-dupes the audience at the
        # DB level (audience-build also filters dups before insert).
        UniqueConstraint("campaign_id", "email", name="uq_outreach_message_campaign_email"),
    )

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(
        Integer, ForeignKey("outreach_campaigns.id"), index=True, nullable=False
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    # Candidate/application provenance for the recipient. The physical legacy
    # ``prospect_id`` database column is intentionally left in place but is no
    # longer mapped or written by the live application.
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=True)
    # The past application that put a pool candidate in the audience — carried
    # for attribution (never mutated).
    source_application_id = Column(
        Integer, ForeignKey("candidate_applications.id"), nullable=True
    )

    recipient_name = Column(String, nullable=True)
    email = Column(String, nullable=False)  # normalized

    subject = Column(String, nullable=True)
    body = Column(Text, nullable=True)
    status = Column(String, nullable=False, server_default=MESSAGE_STATUS_PENDING)

    # Resend correlation id, stored at send time; the webhook looks messages up
    # by this. Unique so a stray duplicate event can't fan out.
    resend_email_id = Column(String, nullable=True, unique=True, index=True)
    # Public interest-capture token, minted at creation.
    interest_token = Column(
        String, nullable=False, unique=True, index=True, default=_mint_interest_token
    )
    error = Column(String, nullable=True)

    sent_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    clicked_at = Column(DateTime(timezone=True), nullable=True)
    interested_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    campaign = relationship("OutreachCampaign", back_populates="messages")
    candidate = relationship("Candidate")
