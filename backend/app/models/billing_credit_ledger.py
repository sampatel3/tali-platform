from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class BillingCreditLedger(Base):
    __tablename__ = "billing_credit_ledger"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    delta = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    reason = Column(String, nullable=False)
    external_ref = Column(String, nullable=True, unique=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), index=True, nullable=True)
    entry_metadata = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="credit_ledger_entries")
    assessment = relationship("Assessment")
