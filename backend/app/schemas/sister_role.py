"""API contracts for coupled sister roles."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .role import RoleResponse


class RelatedRolePublishAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_source_role_id: int = Field(ge=1)
    expected_source_role_name: str = Field(min_length=1, max_length=200)
    expected_source_role_version: int = Field(ge=1)
    expected_default_monthly_budget_cents: int = Field(ge=1, le=10_000_000)
    approved_max_candidates_total: int = Field(ge=0)
    approved_max_scoreable_count: int = Field(ge=0)
    approved_monthly_budget_cents: int = Field(ge=1, le=10_000_000)


class SisterRoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    job_spec_text: str = Field(min_length=80, max_length=100_000)
    related_role_authorization: RelatedRolePublishAuthorization | None = None


class SisterRolePreview(BaseModel):
    source_role_id: int
    source_role_name: str
    source_role_version: int
    source_ats_provider: str
    candidates_total: int
    candidates_with_cv: int
    candidates_missing_cv: int
    candidates_scoreable: int
    candidates_unscorable: int
    candidates_excluded: int
    estimated_cost_usd: float
    minimum_initial_budget_cents: int
    ongoing_score_cost_usd: float
    proposed_monthly_budget_cents: int
    initial_scope_fits_monthly_budget: bool


class SisterRoleCreateResponse(BaseModel):
    role: RoleResponse
    evaluation_counts: dict[str, int]


class SisterRoleRescoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    approved_max_scoreable_count: int = Field(ge=0)


class SisterRoleScoringStatus(BaseModel):
    role_id: int
    role_version: int
    cohort_total: int
    cohort_scoreable: int
    cohort_unscorable: int
    cohort_excluded: int
    status: str
    counts: dict[str, int]
    total: int
    scoreable_total: int
    scored: int
    stale_scored: int
    visible_scored: int
    completed: int
    progress_percent: float
    waiting_reason: str | None = None
    estimated_rescore_cost_usd: float = 0.0
    top_candidates: list[dict[str, Any]] = Field(default_factory=list)
