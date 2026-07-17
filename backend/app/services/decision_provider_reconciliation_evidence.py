"""Durable recruiter proof for a reconciled decision provider receipt."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.user import User
from .decision_provider_operation import DECISION_PROVIDER_OPERATION_KEY
from .document_service import sanitize_json_for_storage
from .reconciliation_history import (
    append_reconciliation_history_or_conflict,
    require_reconciliation_history_capacity_or_conflict,
)


def complete_decision_reconciliation_audit(
    db: Session,
    *,
    app: CandidateApplication,
    receipt: dict[str, Any],
    observation: dict[str, Any],
    current_user: User,
) -> dict[str, Any]:
    require_reconciliation_history_capacity_or_conflict(
        receipt, "reconciliation_resolution_history"
    )
    resolved_at = datetime.now(timezone.utc).isoformat()
    evidence = sanitize_json_for_storage(
        {
            "observation_id": observation["observation_id"],
            "receipt_key": DECISION_PROVIDER_OPERATION_KEY,
            "operation_id": receipt["operation_id"],
            "provider": str(receipt.get("provider") or "").lower(),
            "provider_target_id": receipt.get("provider_target_id"),
            "operation_action": receipt.get("operation_action"),
            "provider_effect_matches": True,
            "provider_remote_stage": observation.get("provider_remote_stage"),
            "provider_evidence": observation.get("evidence"),
            "checked_at": observation.get("checked_at"),
            "resolved_at": resolved_at,
            "disposition": "confirm_decision_provider_effect",
        }
    )
    receipt.update(
        reconciliation_status="resolved",
        reconciliation_resolved_at=resolved_at,
        provider_reconciled_at=resolved_at,
        resolved_operation_id=receipt["operation_id"],
        resolved_receipt_key=DECISION_PROVIDER_OPERATION_KEY,
        reconciliation_resolved_by_actor_id=int(current_user.id),
        reconciliation_resolved_by_actor_type="recruiter",
        reconciliation_evidence=evidence,
        reconciliation_observation_id=observation["observation_id"],
        reconciliation_disposition="confirm_decision_provider_effect",
    )
    append_reconciliation_history_or_conflict(
        receipt,
        history_key="reconciliation_resolution_history",
        entry=evidence,
        saturated_at=resolved_at,
    )
    receipt.pop("reconciliation_pending", None)
    state = dict(app.integration_sync_state or {})
    state[DECISION_PROVIDER_OPERATION_KEY] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    append_application_event(
        db,
        app=app,
        event_type="ats_decision_reconciliation_resolved",
        actor_type="recruiter",
        actor_id=int(current_user.id),
        reason="Recruiter confirmed the exact decision ATS effect",
        metadata=evidence,
        idempotency_key=(
            f"decision-resolution:{receipt['operation_id']}:"
            f"{observation['observation_id']}"
        )[:200],
    )
    db.commit()
    return evidence


__all__ = ["complete_decision_reconciliation_audit"]
