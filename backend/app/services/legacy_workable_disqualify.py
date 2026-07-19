"""Fail closed for legacy Workable disqualify attempts with ambiguous results."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage


def surface_ambiguous_legacy_disqualify(
    db: Session,
    *,
    app: CandidateApplication,
    reason: str | None,
    source: str,
) -> dict:
    """Persist exact ambiguity; never retry the possibly-applied POST."""

    from ..domains.assessments_runtime.pipeline_service import append_application_event

    state = (
        dict(app.integration_sync_state)
        if isinstance(app.integration_sync_state, dict)
        else {}
    )
    key = "outcome_writeback_reconciliation"
    existing = state.get(key)
    if isinstance(existing, dict) and (
        existing.get("manual_reconciliation_required") is True
        or existing.get("provider_outcome_uncertain") is True
    ):
        return dict(existing)
    target = str(app.workable_candidate_id or "").strip()
    version = int(app.version or 1)
    digest = hashlib.sha256(
        f"{app.organization_id}:{app.id}:{version}:{target}:{reason or ''}".encode(
            "utf-8"
        )
    ).hexdigest()[:28]
    operation_id = f"legacy-workable-disqualify:{app.id}:{digest}"[:200]
    now = datetime.now(timezone.utc).isoformat()
    receipt = {
        "application_id": int(app.id),
        "organization_id": int(app.organization_id),
        "operation_id": operation_id,
        "status": "manual_reconciliation_required",
        "provider": "workable",
        "provider_target_id": target,
        "target_outcome": "rejected",
        "expected_local_outcome": str(app.application_outcome or "open").lower(),
        "expected_application_version": version,
        "provider_called": None,
        "provider_succeeded": None,
        "provider_outcome_uncertain": True,
        "manual_reconciliation_required": True,
        "reconciliation_reason": "legacy_disqualify_result_ambiguous",
        "failure_message": sanitize_text_for_storage(
            "Workable did not confirm whether the disqualify was applied"
        ),
        "source": str(source)[:100],
        "updated_at": now,
    }
    state[key] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)
    append_application_event(
        db,
        app=app,
        event_type="ats_outcome_writeback_manual_reconciliation_required",
        actor_type="system",
        reason="Workable rejection is uncertain; verify it before another write",
        metadata={
            "operation_id": operation_id,
            "ats": "workable",
            "provider_target_id": target,
            "source": source,
            "local_outcome_preserved": app.application_outcome,
        },
        idempotency_key=f"{operation_id}:reconciliation"[:200],
    )
    return receipt


__all__ = ["surface_ambiguous_legacy_disqualify"]
