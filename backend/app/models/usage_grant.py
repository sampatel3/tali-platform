from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


# Grant types
GRANT_FREE_TIER = "free_tier"
GRANT_PROMO = "promo"
GRANT_MANUAL = "manual"
GRANT_TOPUP = "topup"  # Stripe one-time pack purchase


class UsageGrant(Base):
    __tablename__ = "usage_grants"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    grant_type = Column(String, nullable=False)
    credits_granted = Column(BigInteger, nullable=False)
    granted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    external_ref = Column(String, unique=True, nullable=True, index=True)
    grant_metadata = Column("metadata", JSON, nullable=True)

    organization = relationship("Organization")
