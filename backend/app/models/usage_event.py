from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    feature = Column(String, nullable=False)  # prescreen | score | assessment | other
    entity_id = Column(String, nullable=True)  # application_id / assessment_id
    model = Column(String, nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    cache_read_tokens = Column(Integer, default=0, nullable=False)
    cache_creation_tokens = Column(Integer, default=0, nullable=False)
    cost_usd_micro = Column(BigInteger, default=0, nullable=False)
    markup_multiplier = Column(Numeric(4, 2), nullable=False)
    credits_charged = Column(BigInteger, default=0, nullable=False)
    cache_hit = Column(Integer, default=0, nullable=False)  # 0/1 boolean (sqlite-compat)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    event_metadata = Column("metadata", JSON, nullable=True)

    organization = relationship("Organization")

    __table_args__ = (
        Index("ix_usage_events_org_created", "organization_id", "created_at"),
        Index("ix_usage_events_feature_created", "feature", "created_at"),
    )
