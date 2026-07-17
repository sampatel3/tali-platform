"""Fail-closed handling for pre-receipt deferred decision deliveries."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from .decision_provider_authority import decision_operation_action
from .document_service import sanitize_json_for_storage


def surface_legacy_decision_delivery(
    db: Session,
    *,
    decision: AgentDecision,
    app: CandidateApplication | None,
    target_stage: str | None,
) -> dict:
    """Never replay an unbound legacy provider side effect.

    Old queued tasks carried only a decision id and optional stage. They cannot
    prove provider target, application version, or whether a prior HTTP call
    landed, so the only safe compatibility behavior is durable reconciliation.
    """

    disposition = "overridden" if decision.status == "overridden" else "approved"
    action = decision_operation_action(
        disposition=disposition,
        decision_type=str(decision.decision_type),
        override_action=decision.override_action,
    )
    if action is None or app is None:
        db.rollback()
        return {
            "status": "skipped",
            "reason": "no_provider_effect" if action is None else "application_not_found",
            "decision_id": int(decision.id),
        }
    state = (
        dict(app.integration_sync_state)
        if isinstance(app.integration_sync_state, dict)
        else {}
    )
    current = state.get("decision_provider_operation")
    if isinstance(current, dict):
        db.rollback()
        return {
            "status": "skipped",
            "reason": "receipt_already_exists",
            "decision_id": int(decision.id),
        }
    provider, target = (
        ("workable", str(app.workable_candidate_id))
        if app.workable_candidate_id
        else (
            ("bullhorn", str(app.bullhorn_job_submission_id))
            if app.bullhorn_job_submission_id
            else ("ats", "")
        )
    )
    now = datetime.now(timezone.utc).isoformat()
    operation_id = (
        f"legacy-decision:{int(decision.organization_id)}:{int(decision.id)}:"
        + hashlib.sha256(
            f"{disposition}:{action}:{target_stage or ''}".encode("utf-8")
        ).hexdigest()[:24]
    )[:200]
    state["decision_provider_operation"] = {
        "operation_id": operation_id,
        "status": "manual_reconciliation_required",
        "organization_id": int(decision.organization_id),
        "application_id": int(decision.application_id),
        "decision_id": int(decision.id),
        "disposition": disposition,
        "operation_action": action,
        "override_action": decision.override_action,
        "provider": provider,
        "provider_target_id": target,
        "target_stage": str(target_stage or "") or None,
        "provider_called": None,
        "provider_succeeded": None,
        "provider_outcome_uncertain": True,
        "manual_reconciliation_required": True,
        "reconciliation_reason": "legacy_delivery_has_no_exact_receipt",
        "requested_at": now,
        "updated_at": now,
    }
    app.integration_sync_state = sanitize_json_for_storage(state)
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    append_application_event(
        db,
        app=app,
        event_type="ats_decision_reconciliation_required",
        actor_type="system",
        reason="A legacy decision delivery needs exact ATS verification",
        metadata={
            "operation_id": operation_id,
            "decision_id": int(decision.id),
            "operation_action": action,
            "provider": provider,
            "provider_target_id": target,
            "local_state_preserved": True,
        },
        idempotency_key=f"{operation_id}:legacy-reconciliation"[:200],
    )
    db.commit()
    return {
        "status": "reconciliation_required",
        "decision_id": int(decision.id),
        "operation_id": operation_id,
    }


__all__ = ["surface_legacy_decision_delivery"]
