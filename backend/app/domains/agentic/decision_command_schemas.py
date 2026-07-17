"""Request/response contracts for Decision Hub approval commands."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ...schemas.role import RoleFamilyResponse


class ApproveBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)
    workable_target_stage: Optional[str] = Field(default=None, max_length=200)
    expected_role_family: Optional[RoleFamilyResponse] = None
    expected_decision_type: Optional[str] = Field(default=None, max_length=64)


class OverrideBody(BaseModel):
    override_action: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=2000)
    workable_target_stage: Optional[str] = Field(default=None, max_length=200)
    expected_role_family: Optional[RoleFamilyResponse] = None
    expected_decision_type: Optional[str] = Field(default=None, max_length=64)


class BulkApproveBody(BaseModel):
    """Explicit visible IDs plus the exact displayed decision/family snapshots."""

    decision_ids: list[int] = Field(min_length=1, max_length=500)
    note: Optional[str] = None
    workable_target_stages: Optional[dict[str, str]] = None
    expected_role_families: Optional[dict[str, RoleFamilyResponse]] = None
    expected_decision_types: Optional[dict[str, str]] = None


class BulkApproveFailure(BaseModel):
    decision_id: int
    error: str


class BulkApproveResult(BaseModel):
    requested: int
    accepted: int
    job_run_id: Optional[int] = None
    failures: list[BulkApproveFailure] = Field(default_factory=list)


BULK_OVERRIDE_ACTIONS = frozenset(
    {"skip_assessment_advance", "advance", "reject"}
)


class BulkOverrideBody(BaseModel):
    """Explicit visible IDs for one supported alternative action."""

    decision_ids: list[int] = Field(min_length=1, max_length=500)
    override_action: str
    note: Optional[str] = None
    workable_target_stages: Optional[dict[str, str]] = None
    expected_role_families: Optional[dict[str, RoleFamilyResponse]] = None
    expected_decision_types: Optional[dict[str, str]] = None


__all__ = [
    "ApproveBody",
    "BULK_OVERRIDE_ACTIONS",
    "BulkApproveBody",
    "BulkApproveFailure",
    "BulkApproveResult",
    "BulkOverrideBody",
    "OverrideBody",
]
