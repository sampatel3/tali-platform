"""Non-sending inventory receipts for pre-outbox assessment completions."""

from __future__ import annotations

import uuid
from typing import Any

from ...models.assessment import Assessment
from .result_delivery_contracts import (
    DELIVERY_LEGACY_RECONCILIATION_REQUIRED,
    fingerprint,
    iso,
    now,
    provisional_intent,
    write_receipt,
)


def classify_legacy_assessment_result_delivery(
    row: Assessment,
    *,
    timestamp: Any | None = None,
    legacy_payload_evidence: dict[str, Any] | None = None,
) -> bool:
    """Inventory an untracked legacy completion without authorizing a send."""

    if (
        bool(getattr(row, "is_voided", False))
        or row.workable_result_delivery_status is not None
        or row.workable_result_delivery_receipt is not None
        or bool(row.posted_to_workable)
    ):
        return False
    captured_at = timestamp or now()
    intent = provisional_intent(row)
    receipt = {
        "version": 1,
        "operation_id": uuid.uuid4().hex,
        "intent": intent,
        "intent_sha256": fingerprint(intent),
        "status": DELIVERY_LEGACY_RECONCILIATION_REQUIRED,
        "provider_attempts": 0,
        "publish_attempts": 0,
        "provider_called": False,
        "provider_succeeded": False,
        "provider_outcome_uncertain": True,
        "legacy_inventory_only": True,
        "last_error_code": "legacy_delivery_state_missing",
        "created_at": iso(captured_at),
        "updated_at": iso(captured_at),
    }
    if legacy_payload_evidence is not None:
        receipt["legacy_payload_evidence"] = legacy_payload_evidence
    write_receipt(
        row,
        receipt,
        status=DELIVERY_LEGACY_RECONCILIATION_REQUIRED,
    )
    return True


__all__ = ["classify_legacy_assessment_result_delivery"]
