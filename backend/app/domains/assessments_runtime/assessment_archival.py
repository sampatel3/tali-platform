"""Serialized, evidence-preserving assessment archival."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    append_assessment_timeline_event,
    utcnow,
)
from ...components.assessments.result_delivery_contracts import (
    DELIVERY_CANCELLED,
    DELIVERY_PROVIDER_STARTED,
    receipt_copy,
    write_receipt,
)
from ...models.assessment import Assessment
from ...models.user import User
from .workspace_serialization import (
    assessment_workspace_mutex,
    prepare_assessment_workspace_mutex,
)


def archive_assessment(
    db: Session,
    *,
    assessment_id: int,
    current_user: User,
) -> None:
    """Archive one org-owned row without erasing workspace/provider evidence."""

    organization_id = int(current_user.organization_id)
    actor_id = int(current_user.id)
    actor_type = (
        "workspace_owner"
        if str(getattr(current_user, "role", "")) == "owner"
        else "workspace_member"
    )
    prepare_assessment_workspace_mutex(db)
    with assessment_workspace_mutex(db, assessment_id=int(assessment_id)):
        assessment = (
            db.query(Assessment)
            .filter(
                Assessment.id == int(assessment_id),
                Assessment.organization_id == organization_id,
                Assessment.is_voided.is_(False),
            )
            .populate_existing()
            .with_for_update(of=Assessment)
            .one_or_none()
        )
        if assessment is None:
            raise HTTPException(status_code=404, detail="Assessment not found")
        try:
            receipt = receipt_copy(assessment.workable_result_delivery_receipt)
            receipt_status = str(assessment.workable_result_delivery_status or "")
            provider_definitively_not_called = (
                receipt
                and receipt.get("provider_called") is False
                and receipt.get("provider_succeeded") is False
                and receipt.get("provider_outcome_uncertain") is False
                and receipt_status != DELIVERY_PROVIDER_STARTED
            )
            if provider_definitively_not_called:
                write_receipt(assessment, receipt, status=DELIVERY_CANCELLED)
            assessment.is_voided = True
            assessment.voided_at = utcnow()
            assessment.void_reason = "archived_by_recruiter"
            append_assessment_timeline_event(
                assessment,
                "assessment_archived",
                {
                    "actor_id": actor_id,
                    "actor_type": actor_type,
                    "reason": "archived_by_recruiter",
                    "result_delivery_evidence_preserved": bool(
                        assessment.workable_result_delivery_status
                        or assessment.workable_result_delivery_receipt
                    ),
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Failed to archive assessment")


__all__ = ["archive_assessment"]
