"""API contracts for independent related roles."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .role import RoleResponse


class SisterRoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    job_spec_text: str = Field(min_length=80, max_length=100_000)
    source_snapshot_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )


class SisterRolePreview(BaseModel):
    source_role_id: int
    source_role_name: str
    source_ats_provider: str | None
    candidates_total: int
    candidates_with_cv: int
    candidates_missing_cv: int
    source_snapshot_fingerprint: str


class SisterRoleCreateResponse(BaseModel):
    role: RoleResponse
    evaluation_counts: dict[str, int]


class SisterRoleScoringStatus(BaseModel):
    role_id: int
    status: str
    counts: dict[str, int]
    total: int
    scoreable_total: int
    scored: int
    completed: int
    progress_percent: float
    waiting_reason: str | None = None
    top_candidates: list[dict[str, Any]] = Field(default_factory=list)
