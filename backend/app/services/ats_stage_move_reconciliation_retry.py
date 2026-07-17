"""Exact durable payload construction for a reconciled stage-move retry."""

from __future__ import annotations

from typing import Any


def build_stage_move_retry_payload(
    receipt: dict[str, Any], user_id: int
) -> dict[str, Any]:
    return {
        "application_id": int(receipt["application_id"]),
        "provider": receipt["provider"],
        "provider_target_id": receipt["provider_target_id"],
        "target_stage": receipt["target_stage"],
        "target_intent": receipt["target_intent"],
        "acting_role_id": receipt.get("acting_role_id"),
        "operation_id": receipt["operation_id"],
        "authority_snapshot_version": 1,
        "expected_application_version": receipt["expected_application_version"],
        "expected_application_outcome": receipt["expected_application_outcome"],
        "expected_pipeline_stage": receipt["expected_pipeline_stage"],
        "expected_workable_disqualified": receipt["expected_workable_disqualified"],
        "expected_candidate_id": receipt["expected_candidate_id"],
        "expected_owner_role_id": receipt["expected_owner_role_id"],
        "expected_owner_role_version": receipt["expected_owner_role_version"],
        "expected_owner_external_job_id": receipt.get("owner_external_job_id"),
        "expected_acting_role_id": receipt.get("acting_role_id"),
        "expected_acting_role_version": receipt.get("expected_acting_role_version"),
        "expected_related_evaluation_id": receipt.get("related_evaluation_id"),
        "expected_related_evaluation_status": receipt.get("related_evaluation_status"),
        "expected_related_pipeline_stage": receipt.get("related_pipeline_stage"),
        "expected_related_spec_fingerprint": receipt.get("related_spec_fingerprint"),
        "expected_provider": receipt["provider"],
        "expected_provider_target_id": receipt["provider_target_id"],
        "expected_target_intent": receipt["target_intent"],
        "user_id": int(user_id),
        "actor_type": "recruiter",
        "reason": receipt.get("reason"),
    }


__all__ = ["build_stage_move_retry_payload"]
