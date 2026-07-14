"""API contracts for coupled sister roles."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .role import RoleResponse


class SisterRoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    job_spec_text: str = Field(min_length=80, max_length=100_000)


class SisterRolePreview(BaseModel):
    source_role_id: int
    source_role_name: str
    source_ats_provider: str
    candidates_total: int
    candidates_with_cv: int
    candidates_missing_cv: int


class SisterRoleCreateResponse(BaseModel):
    role: RoleResponse
    evaluation_counts: dict[str, int]


class SisterRoleScoringStatus(BaseModel):
    role_id: int
    status: str
    counts: dict[str, int]
    total: int
    completed: int
    progress_percent: float
    top_candidates: list[dict[str, Any]] = Field(default_factory=list)
