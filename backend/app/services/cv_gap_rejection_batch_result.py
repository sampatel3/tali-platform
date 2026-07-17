"""Shared terminal handling for a confirmed CV-gap provider success."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..schemas.role import RoleFamilyResponse
from .cv_gap_rejection_batch_support import (
    finalize_provider_failure,
    persist_progress,
    record_authority_failure,
    record_processed,
)


@dataclass
class CvGapSuccessReconciler:
    db: Session
    organization_id: int
    role_id: int
    needs_input_id: int
    kind: str
    user_id: int
    expected_version: int
    expected_family: RoleFamilyResponse
    job_run_id: int | None
    progress: dict[str, Any]
    spec: dict[str, str]
    finalize: Any

    def __call__(
        self,
        *,
        application_id: int,
        operation_id: str,
        provider_result: dict[str, Any],
    ) -> bool:
        performed, reason, authority_error = self.finalize(
            self.db,
            organization_id=self.organization_id,
            role_id=self.role_id,
            needs_input_id=self.needs_input_id,
            kind=self.kind,
            user_id=self.user_id,
            expected_version=self.expected_version,
            expected_family=self.expected_family,
            application_id=application_id,
            operation_id=operation_id,
            spec=self.spec,
            provider_result=provider_result,
        )
        record_processed(
            self.progress,
            application_id=application_id,
            outcome="rejected" if performed else "failed",
            reason=reason,
        )
        if authority_error is not None:
            record_authority_failure(self.progress, authority_error)
        persist_progress(self.job_run_id, self.progress)
        return authority_error is not None

    def provider_failure(
        self,
        *,
        application_id: int,
        operation_id: str,
        provider_result: dict[str, Any],
    ) -> None:
        reason = finalize_provider_failure(
            self.db,
            organization_id=self.organization_id,
            application_id=application_id,
            operation_id=operation_id,
            user_id=self.user_id,
            spec=self.spec,
            provider_result=provider_result,
        )
        record_processed(
            self.progress,
            application_id=application_id,
            outcome="failed",
            reason=reason,
        )
        persist_progress(self.job_run_id, self.progress)


__all__ = ["CvGapSuccessReconciler"]
