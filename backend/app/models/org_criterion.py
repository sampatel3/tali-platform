from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


BUCKET_MUST = "must"
BUCKET_PREFERRED = "preferred"
BUCKET_CONSTRAINT = "constraint"

CRITERION_BUCKETS = (BUCKET_MUST, BUCKET_PREFERRED, BUCKET_CONSTRAINT)


class OrganizationCriterion(Base):
    __tablename__ = "org_criteria"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    ordering = Column(Integer, default=0, nullable=False)
    weight = Column(Float, default=1.0, nullable=False)
    bucket = Column(String, default=BUCKET_PREFERRED, nullable=False)
    text = Column(Text, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="criteria")
