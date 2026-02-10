from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text, Boolean
from sqlalchemy.sql import func
from ..core.database import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    name = Column(String, nullable=False)
    description = Column(Text)
    task_type = Column(String)
    difficulty = Column(String)
    duration_minutes = Column(Integer, default=30)
    starter_code = Column(Text)
    test_code = Column(Text)
    sample_data = Column(JSON)
    dependencies = Column(JSON)
    success_criteria = Column(JSON)
    test_weights = Column(JSON)
    is_template = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
