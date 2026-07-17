"""HTTP contracts for exact, durable CV-gap rejection."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..schemas.role import RoleFamilyResponse
from ..services.cv_gap_rejection_authority import MAX_CV_GAP_REJECTION_BATCH


class CvGapRejectPreview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str
    owner_role_id: int
    application_ids: list[int]
    eligible_count: int
    has_more: bool
    expected_owner_role_version: int
    expected_role_family: RoleFamilyResponse


class RejectCvGapBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_ids: list[int] = Field(
        min_length=1,
        max_length=MAX_CV_GAP_REJECTION_BATCH,
    )
    expected_owner_role_version: int = Field(ge=1)
    expected_role_family: RoleFamilyResponse

    @field_validator("application_ids")
    @classmethod
    def validate_application_ids(cls, value: list[int]) -> list[int]:
        ids = [int(application_id) for application_id in value]
        if any(application_id <= 0 for application_id in ids):
            raise ValueError("application IDs must be positive")
        if ids != sorted(set(ids)):
            raise ValueError("application IDs must be unique and ascending")
        return ids


class RejectCvGapAccepted(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_run_id: int
    status: str
    accepted_count: int
    application_ids: list[int]


__all__ = ["CvGapRejectPreview", "RejectCvGapAccepted", "RejectCvGapBody"]
