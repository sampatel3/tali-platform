"""Strict proof contract shared by reconciliation and lifecycle fencing."""

from __future__ import annotations

from typing import Any

RECONCILIATION_DISPOSITIONS = frozenset(
    {
        "confirm_provider_matches_local",
        "align_local_to_provider",
        "confirm_decision_provider_effect",
    }
)


def has_exact_reconciliation_resolution(
    receipt: dict[str, Any], *, receipt_key: str
) -> bool:
    """Accept only exact operation, actor, observation, and disposition proof."""

    evidence = receipt.get("reconciliation_evidence")
    disposition = str(receipt.get("reconciliation_disposition") or "")
    operation_id = str(receipt.get("operation_id") or "")
    return bool(
        str(receipt.get("reconciliation_status") or "").lower() == "resolved"
        and operation_id
        and str(receipt.get("resolved_operation_id") or "") == operation_id
        and str(receipt.get("resolved_receipt_key") or "") == receipt_key
        and receipt.get("reconciliation_resolved_by_actor_id") is not None
        and str(receipt.get("reconciliation_resolved_by_actor_type") or "").strip()
        and disposition in RECONCILIATION_DISPOSITIONS
        and isinstance(evidence, dict)
        and str(evidence.get("operation_id") or "") == operation_id
        and str(evidence.get("receipt_key") or "") == receipt_key
        and str(evidence.get("provider") or "")
        == str(receipt.get("provider") or "").strip().lower()
        and str(evidence.get("provider_target_id") or "")
        == str(receipt.get("provider_target_id") or "")
        and str(evidence.get("observation_id") or "")
        == str(receipt.get("reconciliation_observation_id") or "")
        and (
            evidence.get("remote_outcome") in {"open", "rejected"}
            or evidence.get("provider_effect_matches") is True
        )
    )


__all__ = [
    "RECONCILIATION_DISPOSITIONS",
    "has_exact_reconciliation_resolution",
]
