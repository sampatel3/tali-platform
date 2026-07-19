from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.sql import func

from ..platform.database import Base


def _new_task_template_repository_name(context: Any) -> str:
    """Create one persisted repository namespace for a newly inserted task."""

    organization_id = context.get_current_parameters().get("organization_id")
    organization_part = (
        str(organization_id)
        if type(organization_id) is int and organization_id > 0
        else "0"
    )
    return f"task-o{organization_part}-{uuid.uuid4().hex}"


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "template_repository_name = lower(template_repository_name)",
            name="ck_tasks_template_repository_name_lowercase",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
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
    # Prompt scoring configuration (Phase 2)
    calibration_prompt = Column(Text, nullable=True)
    score_weights = Column(JSON, nullable=True)
    recruiter_weight_preset = Column(String, nullable=True)  # "solution_focused", "prompt_focused", "balanced"
    proctoring_enabled = Column(Boolean, default=False)
    # New fields from task JSON spec
    task_key = Column(String, nullable=True, index=True)  # e.g. "ai_eng_a_prompt_cache"
    # Stable global repository namespace. Unlike task_key, this is immutable in
    # practice and globally unique across organizations and case variants. The
    # migration retains a DB-side default for old writers during rolling deploys.
    template_repository_name = Column(
        String(100),
        nullable=False,
        default=_new_task_template_repository_name,
        unique=True,
        index=True,
    )
    role = Column(String, nullable=True)                  # "ai_engineer" | "data_engineer"
    scenario = Column(Text, nullable=True)                # Problem description shown to candidate
    repo_structure = Column(JSON, nullable=True)          # {name, files: {path: content}}
    evaluation_rubric = Column(JSON, nullable=True)       # {category: {weight, criteria}}
    extra_data = Column(JSON, nullable=True)              # expected_insights, expected_fixes, valid_solutions, etc.
    claude_budget_limit_usd = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
