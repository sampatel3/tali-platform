from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from ..platform.database import Base


# A data-subject request under GDPR-style regimes.
DSR_TYPE_ACCESS = "access"      # export a copy of the subject's data (portability)
DSR_TYPE_ERASURE = "erasure"    # "right to be forgotten" — anonymize + soft-delete
DSR_TYPES = (DSR_TYPE_ACCESS, DSR_TYPE_ERASURE)

DSR_STATUS_PENDING = "pending"
DSR_STATUS_COMPLETED = "completed"
DSR_STATUS_REJECTED = "rejected"
DSR_STATUSES = (DSR_STATUS_PENDING, DSR_STATUS_COMPLETED, DSR_STATUS_REJECTED)


class DataSubjectRequest(Base):
    """A logged request from (or on behalf of) a candidate to access or erase
    their personal data. The log itself is the compliance evidence — it records
    who asked, what was done, and when — and outlives an erased candidate row."""

    __tablename__ = "data_subject_requests"
    __table_args__ = (
        Index("ix_data_subject_requests_org", "organization_id"),
        Index("ix_data_subject_requests_email", "subject_email"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    # Nullable: a request may arrive by email before (or after) the candidate row
    # is resolved/erased.
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=True)
    subject_email = Column(String, nullable=True)
    request_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default=DSR_STATUS_PENDING)
    notes = Column(Text, nullable=True)
    requested_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
