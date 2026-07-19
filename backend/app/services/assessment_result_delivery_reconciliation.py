"""Owner-attested recovery for unresolved Workable assessment-result delivery."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..components.assessments.repository import append_assessment_timeline_event
from ..components.assessments.result_delivery_contracts import (
    DELIVERY_CONFIRMED,
    AssessmentResultDispatch,
    iso,
    receipt_copy,
    valid_receipt,
    write_receipt,
)
from ..components.assessments.result_delivery_outbox import (
    attach_assessment_result_delivery_receipt,
    publish_assessment_result_delivery,
)
from ..components.assessments.result_delivery_visibility import (
    RECONCILABLE_RESULT_DELIVERY_STATUSES,
    public_result_delivery_evidence,
)
from ..models.assessment import Assessment
from ..models.user import User
from .assessment_result_reconciliation_evidence import (
    _MAX_JSON_DEPTH as _EVIDENCE_MAX_JSON_DEPTH,
    _MAX_JSON_NODES as _EVIDENCE_MAX_JSON_NODES,
    assert_secret_free_receipt_evidence as _assert_secret_free_receipt_evidence,
    preflight_json as _preflight_json,
)

Publisher = Callable[[AssessmentResultDispatch], str]

_MAX_JSON_DEPTH = _EVIDENCE_MAX_JSON_DEPTH
_MAX_JSON_NODES = _EVIDENCE_MAX_JSON_NODES

_MAX_HISTORY_ENTRIES = 100
_MAX_HISTORY_BYTES = 512 * 1024
_MAX_ARCHIVED_RECEIPT_BYTES = 128 * 1024
_HISTORY_ENTRY_KEYS = frozenset({"receipt", "resolution"})
_RESOLUTION_KEYS = frozenset(
    {
        "action",
        "actor_id",
        "actor_type",
        "resolved_at",
        "provider_result_present_attested",
        "provider_result_absent_attested",
        "prior_status",
        "prior_operation_id",
    }
)
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _locked_row(
    db: Session,
    *,
    assessment_id: int,
    expected_operation_id: str,
    current_user: User,
) -> Assessment:
    row = (
        db.query(Assessment)
        .filter(
            Assessment.id == int(assessment_id),
            Assessment.organization_id == int(current_user.organization_id),
            Assessment.is_voided.is_(False),
        )
        .populate_existing()
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if bool(row.posted_to_workable):
        raise _conflict("This assessment result is already marked delivered.")
    if (
        str(row.workable_result_delivery_status or "")
        not in RECONCILABLE_RESULT_DELIVERY_STATUSES
    ):
        raise _conflict(
            "This assessment result is not in a manually reconcilable state."
        )
    current_operation_id = str(
        receipt_copy(row.workable_result_delivery_receipt).get("operation_id")
        or ""
    )
    if not expected_operation_id or expected_operation_id != current_operation_id:
        raise _conflict(
            "The result-delivery operation changed. Refresh before reconciling it."
        )
    return row


def _resolution(
    *,
    action: str,
    current_user: User,
    present: bool,
    absent: bool,
    prior_status: str,
    prior_operation_id: str,
) -> dict[str, Any]:
    return {
        "action": action,
        "actor_id": int(current_user.id),
        "actor_type": "workspace_owner",
        "resolved_at": iso(),
        "provider_result_present_attested": present,
        "provider_result_absent_attested": absent,
        "prior_status": prior_status,
        "prior_operation_id": prior_operation_id[:128],
    }


def _safe_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _validate_resolution(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != _RESOLUTION_KEYS:
        raise _conflict(
            "Stored result-delivery reconciliation resolution is malformed; no evidence was overwritten."
        )
    if (
        value.get("action") != "retry_after_provider_absence"
        or isinstance(value.get("actor_id"), bool)
        or not isinstance(value.get("actor_id"), int)
        or value["actor_id"] <= 0
        or value.get("actor_type") != "workspace_owner"
        or not _safe_iso(value.get("resolved_at"))
        or value.get("provider_result_present_attested") is not False
        or value.get("provider_result_absent_attested") is not True
        or value.get("prior_status")
        not in RECONCILABLE_RESULT_DELIVERY_STATUSES
        or not isinstance(value.get("prior_operation_id"), str)
        or _SAFE_OPERATION_ID.fullmatch(value["prior_operation_id"]) is None
    ):
        raise _conflict(
            "Stored result-delivery reconciliation resolution is malformed; no evidence was overwritten."
        )


def _validate_archived_receipt(value: Any) -> None:
    if not isinstance(value, dict) or "reconciliation_history" in value:
        raise _conflict(
            "Stored result-delivery reconciliation receipt is malformed; no evidence was overwritten."
        )
    _preflight_json(
        value,
        max_bytes=_MAX_ARCHIVED_RECEIPT_BYTES,
        label="result-delivery receipt evidence",
    )
    _assert_secret_free_receipt_evidence(value)
    intent = value.get("intent")
    operation_id = value.get("operation_id")
    if (
        not isinstance(intent, dict)
        or isinstance(intent.get("assessment_id"), bool)
        or not isinstance(intent.get("assessment_id"), int)
        or int(intent["assessment_id"]) <= 0
        or isinstance(intent.get("organization_id"), bool)
        or not isinstance(intent.get("organization_id"), int)
        or int(intent["organization_id"]) <= 0
        or not isinstance(operation_id, str)
        or _SAFE_OPERATION_ID.fullmatch(operation_id) is None
    ):
        raise _conflict(
            "Stored result-delivery reconciliation receipt identity is malformed; no evidence was overwritten."
        )
    dispatch = AssessmentResultDispatch(
        assessment_id=int(intent["assessment_id"]),
        organization_id=int(intent["organization_id"]),
        operation_id=operation_id,
    )
    if not valid_receipt(
        value,
        dispatch=dispatch,
        expected_status=str(value.get("status") or ""),
    ):
        raise _conflict(
            "Stored result-delivery reconciliation receipt is invalid; no evidence was overwritten."
        )


def _validated_history_value(
    raw: Any,
    *,
    require_append_room: bool,
) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > _MAX_HISTORY_ENTRIES:
        raise _conflict(
            "Stored result-delivery reconciliation history is too large or malformed; no evidence was overwritten."
        )
    if require_append_room and len(raw) >= _MAX_HISTORY_ENTRIES:
        raise _conflict(
            "Result-delivery reconciliation history reached its retained-evidence limit; contact support."
        )
    _preflight_json(
        raw,
        max_bytes=_MAX_HISTORY_BYTES,
        label="result-delivery reconciliation history",
    )
    for item in raw:
        if not isinstance(item, dict) or set(item) != _HISTORY_ENTRY_KEYS:
            raise _conflict(
                "Stored result-delivery reconciliation history is malformed; no evidence was overwritten."
            )
        _validate_archived_receipt(item["receipt"])
        _validate_resolution(item["resolution"])
    return deepcopy(raw)


def _history(
    receipt: dict[str, Any],
    *,
    require_append_room: bool,
) -> list[dict[str, Any]]:
    return _validated_history_value(
        receipt.get("reconciliation_history"),
        require_append_room=require_append_room,
    )


def _validate_current_receipt(
    row: Assessment,
    receipt: dict[str, Any],
) -> None:
    _preflight_json(
        receipt,
        max_bytes=_MAX_HISTORY_BYTES + _MAX_ARCHIVED_RECEIPT_BYTES,
        label="result-delivery reconciliation evidence",
    )
    _assert_secret_free_receipt_evidence(receipt)
    dispatch = AssessmentResultDispatch(
        assessment_id=int(row.id),
        organization_id=int(row.organization_id),
        operation_id=str(receipt.get("operation_id") or ""),
    )
    if not valid_receipt(
        receipt,
        dispatch=dispatch,
        expected_status=str(row.workable_result_delivery_status or ""),
    ):
        raise _conflict(
            "Stored result-delivery evidence is invalid; no evidence was overwritten."
        )


def _validate_final_manual_receipt(receipt: dict[str, Any]) -> None:
    history = _history(receipt, require_append_room=False)
    history_free = {
        key: value
        for key, value in receipt.items()
        if key != "reconciliation_history"
    }
    _validate_archived_receipt(history_free)
    _preflight_json(
        {**history_free, "reconciliation_history": history},
        max_bytes=_MAX_HISTORY_BYTES + _MAX_ARCHIVED_RECEIPT_BYTES,
        label="result-delivery reconciliation evidence",
    )


def reconcile_assessment_result_delivery(
    db: Session,
    *,
    assessment_id: int,
    action: str,
    expected_operation_id: str,
    provider_result_present_attested: bool,
    provider_result_absent_attested: bool,
    current_user: User,
    publisher: Publisher | None = None,
) -> dict[str, Any]:
    """Confirm delivery or authorize one new operation from explicit absence."""

    row = _locked_row(
        db,
        assessment_id=int(assessment_id),
        expected_operation_id=expected_operation_id,
        current_user=current_user,
    )
    receipt = receipt_copy(row.workable_result_delivery_receipt)
    prior_status = str(row.workable_result_delivery_status or "")
    prior_operation_id = str(receipt.get("operation_id") or "")
    _validate_current_receipt(row, receipt)
    history = _history(
        receipt,
        require_append_room=action == "retry_after_provider_absence",
    )

    if action == "confirm_delivered":
        if (
            not provider_result_present_attested
            or provider_result_absent_attested
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Confirm that the exact Workable assessment result is present "
                    "before marking it delivered."
                ),
            )
        resolution = _resolution(
            action=action,
            current_user=current_user,
            present=True,
            absent=False,
            prior_status=prior_status,
            prior_operation_id=prior_operation_id,
        )
        receipt["manual_resolution"] = resolution
        receipt["provider_succeeded"] = True
        receipt["provider_outcome_uncertain"] = False
        receipt["last_error_code"] = None
        _validate_final_manual_receipt(receipt)
        row.posted_to_workable = True
        row.posted_to_workable_at = row.posted_to_workable_at or datetime.now(
            timezone.utc
        )
        append_assessment_timeline_event(
            row,
            "workable_result_delivery_manually_reconciled",
            {
                "action": action,
                "actor_id": int(current_user.id),
                "prior_status": prior_status,
                "provider_result_present_attested": True,
            },
        )
        write_receipt(row, receipt, status=DELIVERY_CONFIRMED)
        db.commit()
        return {
            "status": DELIVERY_CONFIRMED,
            "dispatch_status": "not_sent",
            "workable_result_delivery": public_result_delivery_evidence(row),
        }

    if action != "retry_after_provider_absence":
        raise HTTPException(status_code=422, detail="Unsupported reconciliation action")
    if not provider_result_absent_attested or provider_result_present_attested:
        raise HTTPException(
            status_code=422,
            detail=(
                "Retry requires an explicit attestation that the exact Workable "
                "assessment result is absent."
            ),
        )

    resolution = _resolution(
        action=action,
        current_user=current_user,
        present=False,
        absent=True,
        prior_status=prior_status,
        prior_operation_id=prior_operation_id,
    )
    prior_receipt_view = {
        key: value
        for key, value in receipt.items()
        if key != "reconciliation_history"
    }
    _validate_archived_receipt(prior_receipt_view)
    prior_receipt = deepcopy(prior_receipt_view)
    history.append(
        {
            "receipt": prior_receipt,
            "resolution": deepcopy(resolution),
        }
    )
    history = _validated_history_value(history, require_append_room=False)
    append_assessment_timeline_event(
        row,
        "workable_result_delivery_manually_reconciled",
        {
            "action": action,
            "actor_id": int(current_user.id),
            "prior_status": prior_status,
            "provider_result_absent_attested": True,
        },
    )
    row.workable_result_delivery_receipt = None
    row.workable_result_delivery_status = None
    row.workable_result_delivery_next_attempt_at = None
    row.workable_result_delivery_claimed_at = None
    dispatch = attach_assessment_result_delivery_receipt(
        db,
        row,
        request_id=f"owner-attested-retry:{int(current_user.id)}",
    )
    replacement = receipt_copy(row.workable_result_delivery_receipt)
    replacement["manual_resolution"] = resolution
    replacement["reconciliation_history"] = history
    row.workable_result_delivery_receipt = replacement
    db.commit()

    dispatch_status = "not_sent"
    if dispatch is not None:
        send = publisher or publish_assessment_result_delivery
        dispatch_status = send(dispatch)
    db.rollback()
    refreshed = (
        db.query(Assessment)
        .filter(Assessment.id == int(assessment_id))
        .populate_existing()
        .one()
    )
    return {
        "status": str(refreshed.workable_result_delivery_status or ""),
        "dispatch_status": dispatch_status,
        "operation_id": dispatch.operation_id if dispatch is not None else None,
        "workable_result_delivery": public_result_delivery_evidence(refreshed),
    }


__all__ = ["reconcile_assessment_result_delivery"]
