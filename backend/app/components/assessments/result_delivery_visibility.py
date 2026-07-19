"""Secret-free recruiter evidence for Workable assessment-result delivery."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ...models.assessment import Assessment
from .result_delivery_contracts import (
    DELIVERY_DISPATCH_FAILED,
    DELIVERY_FAILED,
    DELIVERY_LEGACY_RECONCILIATION_REQUIRED,
    DELIVERY_RECONCILIATION_REQUIRED,
    receipt_copy,
    receipt_counter,
)

RECONCILABLE_RESULT_DELIVERY_STATUSES = frozenset(
    {
        DELIVERY_DISPATCH_FAILED,
        DELIVERY_FAILED,
        DELIVERY_LEGACY_RECONCILIATION_REQUIRED,
        DELIVERY_RECONCILIATION_REQUIRED,
    }
)

_SAFE_ERROR_CODES = frozenset(
    {
        "broker_publish_exhausted",
        "broker_publish_failed",
        "delivery_receipt_invalid",
        "legacy_delivery_state_missing",
        "provider_attempts_exhausted",
        "provider_call_already_started",
        "provider_worker_stale_after_call_started",
        "workable_actor_missing",
        "workable_authorization_failed",
        "workable_candidate_missing",
        "workable_credential_missing",
        "workable_credential_unavailable",
        "workable_delivery_failed",
        "workable_disabled",
        "workable_disconnected",
        "workable_invalid_response",
        "workable_network_error",
        "workable_not_found",
        "workable_rate_limited",
        "workable_request_rejected",
        "workable_subdomain_missing",
        "workable_sync_failed",
        "workable_unavailable",
        "writeback_disabled",
    }
)
_OPERATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _safe_timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 64:
        return None
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    return normalized


def _safe_operation_id(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized if _OPERATION_ID.fullmatch(normalized) else None


def _safe_error_code(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return normalized if normalized in _SAFE_ERROR_CODES else "workable_delivery_failed"


def public_result_delivery_evidence(row: Assessment) -> dict[str, Any] | None:
    """Return operational evidence without provider target data or credentials."""

    status = str(row.workable_result_delivery_status or "").strip()
    receipt = receipt_copy(row.workable_result_delivery_receipt)
    if not status and not receipt:
        return None
    resolution = receipt.get("manual_resolution")
    safe_resolution = None
    if isinstance(resolution, dict):
        action = str(resolution.get("action") or "")
        if action in {"confirm_delivered", "retry_after_provider_absence"}:
            actor_id = resolution.get("actor_id")
            safe_resolution = {
                "action": action,
                "actor_id": int(actor_id) if type(actor_id) is int else None,
                "actor_type": "workspace_owner",
                "resolved_at": _safe_timestamp(resolution.get("resolved_at")),
                "provider_result_present_attested": bool(
                    resolution.get("provider_result_present_attested")
                ),
                "provider_result_absent_attested": bool(
                    resolution.get("provider_result_absent_attested")
                ),
            }
    operation_id = _safe_operation_id(receipt.get("operation_id"))
    return {
        "status": status or "unknown",
        "operation_id": operation_id,
        "provider_attempts": receipt_counter(receipt, "provider_attempts"),
        "publish_attempts": receipt_counter(receipt, "publish_attempts"),
        "configuration_attempts": receipt_counter(
            receipt, "configuration_attempts"
        ),
        "provider_called": bool(receipt.get("provider_called")),
        "provider_succeeded": bool(receipt.get("provider_succeeded")),
        "provider_outcome_uncertain": bool(
            receipt.get("provider_outcome_uncertain")
        ),
        "last_error_code": _safe_error_code(receipt.get("last_error_code")),
        "created_at": _safe_timestamp(receipt.get("created_at")),
        "updated_at": _safe_timestamp(receipt.get("updated_at")),
        "next_attempt_at": _safe_timestamp(
            row.workable_result_delivery_next_attempt_at
        ),
        "claimed_at": _safe_timestamp(row.workable_result_delivery_claimed_at),
        "reconciliation_required": status
        in RECONCILABLE_RESULT_DELIVERY_STATUSES,
        "retry_requires_provider_absence_attestation": True,
        "confirm_requires_provider_presence_attestation": True,
        "manual_resolution": safe_resolution,
    }


__all__ = [
    "RECONCILABLE_RESULT_DELIVERY_STATUSES",
    "public_result_delivery_evidence",
]
