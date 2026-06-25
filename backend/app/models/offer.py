from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base

# Offer lifecycle. draft -> (pending_approval ->) approved -> sent ->
# accepted/declined/expired. Any non-terminal offer can be 'deprecated' when
# superseded by a new version.
OFFER_STATUS_DRAFT = "draft"
OFFER_STATUS_PENDING_APPROVAL = "pending_approval"
OFFER_STATUS_APPROVED = "approved"
OFFER_STATUS_SENT = "sent"
OFFER_STATUS_ACCEPTED = "accepted"
OFFER_STATUS_DECLINED = "declined"
OFFER_STATUS_EXPIRED = "expired"
OFFER_STATUS_DEPRECATED = "deprecated"
OFFER_STATUSES = (
    OFFER_STATUS_DRAFT,
    OFFER_STATUS_PENDING_APPROVAL,
    OFFER_STATUS_APPROVED,
    OFFER_STATUS_SENT,
    OFFER_STATUS_ACCEPTED,
    OFFER_STATUS_DECLINED,
    OFFER_STATUS_EXPIRED,
    OFFER_STATUS_DEPRECATED,
)
OFFER_TERMINAL_STATUSES = (
    OFFER_STATUS_ACCEPTED,
    OFFER_STATUS_DECLINED,
    OFFER_STATUS_DEPRECATED,
)


class Offer(Base):
    """A structured offer for an application. Typed compensation fields live
    inline (so the HRIS handoff carries currency + pay frequency, which the
    Workable->BambooHR path loses). Versioned so a re-issued offer deprecates the
    prior one. Approval chain lives in ``offer_approvals``.
    """

    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_org_application", "organization_id", "application_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    application_id = Column(
        Integer, ForeignKey("candidate_applications.id"), nullable=False
    )
    version = Column(Integer, nullable=False, server_default="1")
    status = Column(String, nullable=False, server_default=OFFER_STATUS_DRAFT)

    # Typed compensation.
    base_salary_amount = Column(Integer, nullable=True)
    currency = Column(String, nullable=True)
    pay_frequency = Column(String, nullable=True)  # year | month | hour
    signing_bonus = Column(Integer, nullable=True)
    equity_units = Column(Integer, nullable=True)
    custom_fields = Column(JSON, nullable=True)

    starts_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    declined_at = Column(DateTime(timezone=True), nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    application = relationship("CandidateApplication")
    approvals = relationship(
        "OfferApproval",
        back_populates="offer",
        cascade="all, delete-orphan",
        order_by="OfferApproval.group_order, OfferApproval.id",
    )


class OfferApproval(Base):
    """One required approval on an offer. Sequential groups (``group_order``);
    an offer is approved when every group has met its ``group_quorum``.
    """

    __tablename__ = "offer_approvals"
    __table_args__ = (
        Index("ix_offer_approvals_offer", "offer_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    offer_id = Column(Integer, ForeignKey("offers.id"), nullable=False)
    group_order = Column(Integer, nullable=False, server_default="0")
    group_quorum = Column(Integer, nullable=False, server_default="1")
    approver_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String, nullable=False, server_default="pending")  # pending|approved|rejected
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    offer = relationship("Offer", back_populates="approvals")
