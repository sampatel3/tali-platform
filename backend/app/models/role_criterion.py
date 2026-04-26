from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base


CRITERION_SOURCE_RECRUITER = "recruiter"
CRITERION_SOURCE_DERIVED = "derived_from_spec"
CRITERION_SOURCE_RECRUITER_CONSTRAINT = "recruiter_constraint"


class RoleCriterion(Base):
    __tablename__ = "role_criteria"

    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), index=True, nullable=False)
    source = Column(String, default=CRITERION_SOURCE_RECRUITER, nullable=False)
    ordering = Column(Integer, default=0, nullable=False)
    weight = Column(Float, default=1.0, nullable=False)
    must_have = Column(Boolean, default=False, nullable=False)
    text = Column(Text, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    role = relationship("Role", back_populates="criteria")
