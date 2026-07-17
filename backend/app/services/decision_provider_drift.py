"""Exact phase-C drift checks for provider-gated decisions."""

from __future__ import annotations

from ..models.sister_role_evaluation import SisterRoleEvaluation
from .decision_provider_authority import (
    LockedDecisionAuthority,
    stable_json_fingerprint,
)
from .decision_provider_call import DecisionProviderAuthority
from .decision_provider_claim import DecisionProviderClaim
from .decision_provider_operation import (
    decision_identity_fingerprint,
    decision_provider_receipt,
)


def decision_provider_drift_reason(
    *,
    claim: DecisionProviderClaim,
    current: LockedDecisionAuthority,
    provider: DecisionProviderAuthority,
    expected_receipt_statuses: frozenset[str] = frozenset({"provider_succeeded"}),
) -> str | None:
    snap = claim.snapshot
    app = current.app
    decision = current.decision
    receipt = decision_provider_receipt(app)
    checks = (
        (receipt is None, "operation_receipt_missing"),
        (
            receipt is not None
            and str(receipt.get("operation_id") or "") != claim.operation_id,
            "operation_receipt_replaced",
        ),
        (
            receipt is not None
            and str(receipt.get("snapshot_fingerprint") or "") != snap.fingerprint(),
            "operation_snapshot_changed",
        ),
        (
            receipt is not None
            and str(receipt.get("status") or "") not in expected_receipt_statuses,
            "provider_checkpoint_changed",
        ),
        (app.deleted_at is not None, "application_deleted"),
        (
            int(app.version or 1) != snap.expected_application_version,
            "application_version_changed",
        ),
        (
            str(app.application_outcome or "open").lower()
            != snap.expected_application_outcome,
            "application_outcome_changed",
        ),
        (
            str(app.pipeline_stage or "applied").lower()
            != snap.expected_pipeline_stage,
            "application_stage_changed",
        ),
        (
            bool(app.workable_disqualified) != snap.expected_workable_disqualified,
            "application_disqualification_changed",
        ),
        (int(app.candidate_id) != snap.candidate_id, "application_candidate_changed"),
        (int(app.role_id) != snap.owner_role_id, "application_owner_changed"),
        (int(current.candidate.id) != snap.candidate_id, "candidate_changed"),
        (int(decision.id) != snap.decision_id, "decision_changed"),
        (
            str(decision.status or "") != snap.expected_decision_status,
            "decision_status_changed",
        ),
        (
            str(decision.decision_type or "") != snap.expected_decision_type,
            "decision_type_changed",
        ),
        (
            decision_identity_fingerprint(decision)
            != snap.decision_identity_fingerprint,
            "decision_inputs_changed",
        ),
        (int(current.owner_role.id) != snap.owner_role_id, "owner_role_changed"),
        (
            int(current.owner_role.version or 1) != snap.expected_owner_role_version,
            "owner_role_version_changed",
        ),
        (int(current.acting_role.id) != snap.acting_role_id, "acting_role_changed"),
        (
            int(current.acting_role.version or 1)
            != snap.expected_acting_role_version,
            "acting_role_version_changed",
        ),
        (
            stable_json_fingerprint(current.family_payload)
            != snap.role_family_fingerprint,
            "role_family_changed",
        ),
        (
            current.workspace_version != snap.workspace_control_version,
            "workspace_control_changed",
        ),
        (provider.provider != snap.provider, "provider_changed"),
        (provider.provider_target_id != snap.provider_target_id, "provider_target_changed"),
        (
            provider.provider_remote_stage != snap.provider_remote_stage,
            "provider_stage_mapping_changed",
        ),
        (
            provider.provider_connection_key != snap.provider_connection_key,
            "provider_connection_changed",
        ),
        (
            provider.owner_external_job_id != snap.owner_external_job_id,
            "provider_job_changed",
        ),
        (
            provider.candidate_provider_id != snap.candidate_provider_id,
            "candidate_provider_changed",
        ),
    )
    for changed, reason in checks:
        if changed:
            return reason
    return _related_drift(snap=snap, evaluation=current.evaluation)


def _related_drift(*, snap, evaluation: SisterRoleEvaluation | None) -> str | None:
    if snap.related_evaluation_id is None:
        return None if evaluation is None else "related_roster_added"
    if evaluation is None:
        return "related_roster_removed"
    checks = (
        (int(evaluation.id) != snap.related_evaluation_id, "related_roster_replaced"),
        (
            int(evaluation.source_application_id) != snap.related_source_application_id,
            "related_source_changed",
        ),
        (
            str(evaluation.status or "") != str(snap.related_evaluation_status or ""),
            "related_status_changed",
        ),
        (
            str(evaluation.pipeline_stage or "")
            != str(snap.related_pipeline_stage or ""),
            "related_stage_changed",
        ),
        (
            str(evaluation.spec_fingerprint or "")
            != str(snap.related_spec_fingerprint or ""),
            "related_spec_changed",
        ),
    )
    return next((reason for changed, reason in checks if changed), None)


__all__ = ["decision_provider_drift_reason"]
