from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base

# Disposition category — who ended the candidacy and broadly why. Mirrors the
# Workable disqualification model + Greenhouse rejection reasons so imported
# dispositions map cleanly.
DISPOSITION_WE_REJECTED = "we_rejected"
DISPOSITION_THEY_WITHDREW = "they_withdrew"
DISPOSITION_OTHER = "other"
DISPOSITION_CATEGORIES = (
    DISPOSITION_WE_REJECTED,
    DISPOSITION_THEY_WITHDREW,
    DISPOSITION_OTHER,
)

# Canonical per-org seed set (label, category, position). A sensible default
# recruiters can edit; mirrors common ATS reason lists.
CANONICAL_DISQUALIFICATION_REASONS = (
    ("Underqualified", DISPOSITION_WE_REJECTED, 0),
    ("Missing required skills", DISPOSITION_WE_REJECTED, 1),
    ("Not enough experience", DISPOSITION_WE_REJECTED, 2),
    ("Failed assessment", DISPOSITION_WE_REJECTED, 3),
    ("Better candidate selected", DISPOSITION_WE_REJECTED, 4),
    ("Position filled", DISPOSITION_WE_REJECTED, 5),
    ("Candidate withdrew", DISPOSITION_THEY_WITHDREW, 6),
    ("Declined offer", DISPOSITION_THEY_WITHDREW, 7),
    ("Compensation expectations", DISPOSITION_THEY_WITHDREW, 8),
    ("Unresponsive", DISPOSITION_THEY_WITHDREW, 9),
    ("Other", DISPOSITION_OTHER, 10),
)


class DisqualificationReason(Base):
    """A per-organization, configurable reject/withdraw reason.

    Replaces ad-hoc free-text reject reasons with a structured, reportable
    catalog (the basis for source-effectiveness + rejection analytics in P2).
    ``candidate_applications.disposition_reason_id`` references a row here;
    ``disposition_category`` denormalizes ``category`` onto the application for
    cheap reporting. Seeded with ``CANONICAL_DISQUALIFICATION_REASONS`` per org.
    """

    __tablename__ = "disqualification_reasons"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "label", name="uq_disqualification_reason_org_label"
        ),
        Index(
            "ix_disqualification_reasons_org_position",
            "organization_id",
            "position",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    label = Column(String, nullable=False)
    category = Column(String, nullable=False)
    position = Column(Integer, nullable=False, server_default="0")
    is_default = Column(Boolean, nullable=False, server_default="false")
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization")
