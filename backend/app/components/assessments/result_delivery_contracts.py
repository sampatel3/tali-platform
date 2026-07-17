"""Shared contracts for exact Workable assessment-result delivery."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...platform.config import settings


DELIVERY_PENDING = "pending"
DELIVERY_DISPATCHING = "dispatching"
DELIVERY_RETRY_WAIT = "retry_wait"
DELIVERY_PROVIDER_STARTED = "provider_call_started"
DELIVERY_CONFIRMED = "confirmed"
DELIVERY_FAILED = "failed"
DELIVERY_DISPATCH_FAILED = "dispatch_failed"
DELIVERY_CANCELLED = "cancelled"
DELIVERY_SUPERSEDED = "superseded"
DELIVERY_RECONCILIATION_REQUIRED = "manual_reconciliation_required"
DELIVERY_LEGACY_RECONCILIATION_REQUIRED = "legacy_reconciliation_required"

DELIVERABLE_STATUSES = {
    DELIVERY_PENDING,
    DELIVERY_DISPATCHING,
    DELIVERY_RETRY_WAIT,
    # Beat no longer republishes after broker exhaustion, but an earlier
    # accepted message may still arrive and must retain exact claim authority.
    DELIVERY_DISPATCH_FAILED,
}
MAX_PROVIDER_ATTEMPTS = 3
MAX_PUBLISH_ATTEMPTS = 8
DISPATCH_STALE_AFTER = timedelta(minutes=2)
PROVIDER_STALE_AFTER = timedelta(minutes=30)
_SAFE_ERROR_CODES = frozenset(
    {
        "workable_authorization_failed",
        "workable_invalid_response",
        "workable_network_error",
        "workable_not_found",
        "workable_rate_limited",
        "workable_request_rejected",
        "workable_sync_failed",
        "workable_unavailable",
    }
)
_RECEIPT_COUNTER_FIELDS = frozenset(
    {
        "configuration_attempts",
        "intent_revisions",
        "provider_attempts",
        "publish_attempts",
    }
)
_MAX_RECEIPT_COUNTER = 1_000_000
_AUDITED_DELIVERY_STATUSES = frozenset(
    {
        DELIVERY_CANCELLED,
        DELIVERY_CONFIRMED,
        DELIVERY_DISPATCH_FAILED,
        DELIVERY_FAILED,
        DELIVERY_LEGACY_RECONCILIATION_REQUIRED,
        DELIVERY_RECONCILIATION_REQUIRED,
        DELIVERY_SUPERSEDED,
    }
)
_RECEIPT_STATUSES = frozenset(
    {
        DELIVERY_CANCELLED,
        DELIVERY_CONFIRMED,
        DELIVERY_DISPATCH_FAILED,
        DELIVERY_DISPATCHING,
        DELIVERY_FAILED,
        DELIVERY_LEGACY_RECONCILIATION_REQUIRED,
        DELIVERY_PENDING,
        DELIVERY_PROVIDER_STARTED,
        DELIVERY_RECONCILIATION_REQUIRED,
        DELIVERY_RETRY_WAIT,
        DELIVERY_SUPERSEDED,
    }
)


@dataclass(frozen=True)
class AssessmentResultDispatch:
    assessment_id: int
    organization_id: int
    operation_id: str


@dataclass(frozen=True)
class ProviderPlan:
    dispatch: AssessmentResultDispatch
    subdomain: str
    candidate_id: str
    member_id: str
    assessment_data: dict[str, Any]
    attempt: int
    access_token: str = field(repr=False)


@dataclass(frozen=True)
class CurrentContext:
    intent: dict[str, Any]
    access_token: str = field(repr=False)


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat()


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_request_id(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:128] or None


def safe_error_code(value: Any) -> str:
    normalized = str(value or "").strip().split(":", 1)[0].lower()
    return normalized if normalized in _SAFE_ERROR_CODES else "workable_delivery_failed"


def load_locked(
    db: Session, *, assessment_id: int, organization_id: int
) -> Assessment | None:
    return (
        db.query(Assessment)
        .filter(
            Assessment.id == int(assessment_id),
            Assessment.organization_id == int(organization_id),
        )
        .with_for_update()
        .one_or_none()
    )


def _result_payload(
    row: Assessment, *, frontend_url: str, results_url: str | None = None
) -> dict[str, Any]:
    report_url = results_url or (
        f"{str(frontend_url or '').rstrip('/')}/assessments/{int(row.id)}"
    )
    total_seconds = getattr(row, "total_duration_seconds", None)
    if isinstance(total_seconds, (int, float)) and total_seconds >= 0:
        # Workable accepts whole minutes. Round a partial actual minute up so
        # short completed sessions are not reported as zero; zero stays zero.
        time_taken = math.ceil(float(total_seconds) / 60.0) if total_seconds else 0
    else:
        # Legacy rows may predate actual pause-aware duration capture.
        time_taken = max(int(row.duration_minutes or 0), 0)
    return {
        "score": float(row.score or 0),
        "tests_passed": int(row.tests_passed or 0),
        "tests_total": int(row.tests_total or 0),
        "time_taken": time_taken,
        "results_url": report_url,
    }


def current_context(
    db: Session,
    row: Assessment,
    *,
    results_url: str | None = None,
    settings_obj: Any = settings,
) -> tuple[CurrentContext | None, str]:
    if bool(settings_obj.MVP_DISABLE_WORKABLE):
        return None, "workable_disabled"
    if row.status not in {
        AssessmentStatus.COMPLETED,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
    } or bool(getattr(row, "is_voided", False)):
        return None, "assessment_not_deliverable"
    org = (
        db.query(Organization)
        .filter(Organization.id == int(row.organization_id))
        .one_or_none()
    )
    if org is None:
        return None, "organization_missing"

    from ...services.workable_actions_service import (
        resolve_workable_actor_member_id,
        workable_writeback_enabled,
    )

    if not workable_writeback_enabled(org):
        return None, "writeback_disabled"
    if not bool(org.workable_connected):
        return None, "workable_disconnected"
    access_token = str(org.workable_access_token or "").strip()
    subdomain = str(org.workable_subdomain or "").strip().lower()
    candidate_id = str(row.workable_candidate_id or "").strip()
    role_id = row.role_id
    if role_id is None and row.application_id is not None:
        role_id = (
            db.query(CandidateApplication.role_id)
            .filter(
                CandidateApplication.id == int(row.application_id),
                CandidateApplication.organization_id == int(row.organization_id),
            )
            .scalar()
        )
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(row.organization_id),
        )
        .one_or_none()
        if role_id
        else None
    )
    member_id = str(resolve_workable_actor_member_id(org, role=role) or "").strip()
    intent = {
        "assessment_id": int(row.id),
        "organization_id": int(row.organization_id),
        "candidate_id": candidate_id,
        "member_id": member_id,
        "subdomain": subdomain,
        "assessment_data": _result_payload(
            row,
            frontend_url=str(settings_obj.FRONTEND_URL),
            results_url=results_url,
        ),
    }
    context = CurrentContext(intent=intent, access_token=access_token)
    if not access_token:
        return context, "workable_credential_missing"
    if not subdomain:
        return context, "workable_subdomain_missing"
    if not member_id:
        return context, "workable_actor_missing"
    if not candidate_id:
        return context, "workable_candidate_missing"
    return context, "ready"


def provisional_intent(row: Assessment, *, settings_obj: Any = settings) -> dict[str, Any]:
    """Build secret-free evidence even when policy/config prevents delivery."""

    return {
        "assessment_id": int(row.id),
        "organization_id": int(row.organization_id),
        "candidate_id": str(row.workable_candidate_id or "").strip(),
        "member_id": "",
        "subdomain": "",
        "assessment_data": _result_payload(
            row,
            frontend_url=str(settings_obj.FRONTEND_URL),
        ),
    }


def receipt_copy(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def receipt_counter(receipt: Any, field: str) -> int:
    """Read a bounded non-negative receipt counter without ever raising."""

    if field not in _RECEIPT_COUNTER_FIELDS or not isinstance(receipt, dict):
        return 0
    value = receipt.get(field)
    if type(value) is not int or value < 0 or value > _MAX_RECEIPT_COUNTER:
        return 0
    return value


def receipt_hash_history(receipt: Any) -> list[str]:
    """Return a bounded detached intent-hash history for a valid receipt."""

    if not isinstance(receipt, dict):
        return []
    value = receipt.get("prior_intent_sha256")
    if not isinstance(value, list):
        return []
    return [item for item in value[-5:] if isinstance(item, str)][:5]


def write_receipt(
    row: Assessment,
    receipt: dict[str, Any],
    *,
    status: str,
    next_attempt_at: datetime | None = None,
    claimed_at: datetime | None = None,
) -> None:
    previous_status = str(row.workable_result_delivery_status or "")
    updated = dict(receipt)
    updated["status"] = status
    updated["updated_at"] = iso()
    row.workable_result_delivery_status = status
    row.workable_result_delivery_receipt = updated
    row.workable_result_delivery_next_attempt_at = next_attempt_at
    row.workable_result_delivery_claimed_at = claimed_at
    if status != previous_status and status in _AUDITED_DELIVERY_STATUSES:
        timeline = list(row.timeline or [])
        timeline.append(
            {
                "event_type": "workable_result_delivery_status_changed",
                "timestamp": iso(),
                "status": status,
                "operation_id": str(updated.get("operation_id") or "")[:128],
                "provider_outcome_uncertain": bool(
                    updated.get("provider_outcome_uncertain")
                ),
            }
        )
        row.timeline = timeline


def valid_receipt(
    receipt: dict[str, Any],
    *,
    dispatch: AssessmentResultDispatch,
    expected_status: str | None = None,
) -> bool:
    try:
        if not isinstance(receipt, dict):
            return False
        intent = receipt.get("intent")
        operation_id = str(receipt.get("operation_id") or "")
        required_counters_valid = all(
            type(receipt.get(field)) is int
            and 0 <= receipt[field] <= _MAX_RECEIPT_COUNTER
            for field in ("provider_attempts", "publish_attempts")
        )
        optional_counters_valid = all(
            field not in receipt
            or (
                type(receipt.get(field)) is int
                and 0 <= receipt[field] <= _MAX_RECEIPT_COUNTER
            )
            for field in ("configuration_attempts", "intent_revisions")
        )
        history = receipt.get("prior_intent_sha256")
        history_valid = history is None or (
            isinstance(history, list)
            and len(history) <= 5
            and all(isinstance(item, str) and len(item) <= 128 for item in history)
        )
        flags_valid = all(
            type(receipt.get(field)) is bool
            for field in (
                "provider_called",
                "provider_succeeded",
                "provider_outcome_uncertain",
            )
        )
        receipt_status = receipt.get("status")
        return bool(
            isinstance(intent, dict)
            and isinstance(intent.get("assessment_data"), dict)
            and operation_id
            and operation_id == dispatch.operation_id
            and int(intent.get("assessment_id") or 0) == dispatch.assessment_id
            and int(intent.get("organization_id") or 0)
            == dispatch.organization_id
            and str(receipt.get("intent_sha256") or "") == fingerprint(intent)
            and type(receipt.get("version")) is int
            and receipt.get("version") == 1
            and isinstance(receipt_status, str)
            and receipt_status in _RECEIPT_STATUSES
            and (
                expected_status is None
                or receipt_status == str(expected_status or "")
            )
            and required_counters_valid
            and optional_counters_valid
            and history_valid
            and flags_valid
        )
    except (TypeError, ValueError, OverflowError):
        return False


def provider_retry_delay(attempt: int) -> timedelta:
    exponent = min(max(0, int(attempt) - 1), 5)
    return timedelta(seconds=min(1800, 60 * (2**exponent)))


def record_unavailable_context(
    row: Assessment, receipt: dict[str, Any], *, reason: str
) -> str:
    receipt["last_error_code"] = reason
    if reason in {
        "workable_credential_missing",
        "workable_subdomain_missing",
        "workable_actor_missing",
        "workable_candidate_missing",
    }:
        attempts = min(
            receipt_counter(receipt, "configuration_attempts") + 1,
            _MAX_RECEIPT_COUNTER,
        )
        receipt["configuration_attempts"] = attempts
        write_receipt(
            row,
            receipt,
            status=DELIVERY_RETRY_WAIT,
            next_attempt_at=now() + provider_retry_delay(attempts),
        )
        return DELIVERY_RETRY_WAIT
    status = (
        DELIVERY_CANCELLED
        if reason in {"workable_disabled", "writeback_disabled", "workable_disconnected"}
        else DELIVERY_SUPERSEDED
    )
    write_receipt(row, receipt, status=status)
    return status
