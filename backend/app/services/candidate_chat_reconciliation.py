"""Owner-attested no-replay recovery for unresolved candidate chat claims."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..components.assessments.candidate_chat_reconciliation import (
    CHAT_RECONCILIATION_ARCHIVE_KEY,
    CHAT_RECONCILIATION_HISTORY_KEY,
    CandidateChatReconciliationRecord,
    candidate_chat_evidence_within_limits,
    discover_candidate_chat_reconciliation_records,
    public_candidate_chat_reconciliation,
)
from ..components.assessments.chat_idempotency import (
    CHAT_CLAIMS_KEY,
    CLOSED_NO_REPLAY_STATE,
)
from ..components.assessments.repository import append_assessment_timeline_event
from ..models.assessment import Assessment

_MAX_RECONCILIATION_HISTORY = 100
_MAX_OPERATOR_HISTORY_BYTES = 32 * 1024
_MAX_ARCHIVE_HISTORY_BYTES = 1024 * 1024
_OPERATION_ID_PATTERN = re.compile(r"^chatrec_[a-f0-9]{64}$")
_REQUEST_REFERENCE_PATTERN = re.compile(r"^chatreq_[a-f0-9]{32}$")
_ISSUE_CODES = frozenset(
    {
        "ambiguous_provider_outcome",
        "claim_finalization_state_malformed",
        "claim_identity_malformed",
        "claim_record_malformed",
        "claim_state_malformed",
        "claims_container_malformed",
        "finalization_input_malformed",
        "provider_checkpoint_malformed",
        "provider_checkpoint_unsuccessful",
    }
)
_RESOLUTION_KEYS = frozenset(
    {
        "action",
        "actor_id",
        "actor_type",
        "resolved_at",
        "operation_id",
        "request_reference",
        "issue_code",
        "provider_outcome_discarded_attested",
        "disposition",
    }
)
_OPERATOR_HISTORY_KEYS = _RESOLUTION_KEYS | {
    "prior_state",
    "prior_updated_at",
}


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _locked_assessment(
    db: Session,
    *,
    assessment_id: int,
    organization_id: int,
) -> Assessment:
    row = (
        db.query(Assessment)
        .filter(
            Assessment.id == int(assessment_id),
            Assessment.organization_id == int(organization_id),
            Assessment.is_voided.is_(False),
        )
        .populate_existing()
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return row


def list_candidate_chat_reconciliation_operations(
    db: Session,
    *,
    assessment_id: int,
    organization_id: int,
) -> dict[str, Any]:
    row = (
        db.query(Assessment)
        .filter(
            Assessment.id == int(assessment_id),
            Assessment.organization_id == int(organization_id),
            Assessment.is_voided.is_(False),
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return public_candidate_chat_reconciliation(row, can_reconcile=True)


def _find_exact_record(
    row: Assessment,
    *,
    operation_id: str,
    expected_request_reference: str,
) -> CandidateChatReconciliationRecord:
    _validate_operation_identity(
        operation_id=operation_id,
        expected_request_reference=expected_request_reference,
    )
    record = next(
        (
            item
            for item in discover_candidate_chat_reconciliation_records(row)
            if item.operation_id == str(operation_id)
        ),
        None,
    )
    if record is None:
        raise _conflict(
            "The candidate-chat recovery operation changed. Refresh before reconciling it."
        )
    if record.request_reference != str(expected_request_reference):
        raise _conflict(
            "The candidate-chat request identity changed. Refresh before reconciling it."
        )
    if not bool(record.public.get("can_close_without_replay")):
        raise _conflict(
            "This candidate-chat evidence is too large to reconcile safely; no evidence was changed."
        )
    return record


def _validate_operation_identity(
    *,
    operation_id: str,
    expected_request_reference: str,
) -> None:
    if not _OPERATION_ID_PATTERN.fullmatch(str(operation_id)):
        raise HTTPException(
            status_code=422,
            detail="candidate-chat operation_id is malformed",
        )
    if not _REQUEST_REFERENCE_PATTERN.fullmatch(str(expected_request_reference)):
        raise HTTPException(
            status_code=422,
            detail="candidate-chat request reference is malformed",
        )


def _safe_iso(value: Any, *, optional: bool = False) -> bool:
    if value is None:
        return optional
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _valid_resolution(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == _RESOLUTION_KEYS
        and value.get("action") == "close_without_replay"
        and isinstance(value.get("actor_id"), int)
        and not isinstance(value.get("actor_id"), bool)
        and int(value["actor_id"]) > 0
        and value.get("actor_type") == "workspace_owner"
        and _safe_iso(value.get("resolved_at"))
        and _OPERATION_ID_PATTERN.fullmatch(str(value.get("operation_id") or ""))
        and _REQUEST_REFERENCE_PATTERN.fullmatch(
            str(value.get("request_reference") or "")
        )
        and value.get("issue_code") in _ISSUE_CODES
        and value.get("provider_outcome_discarded_attested") is True
        and value.get("disposition") == "provider_outcome_not_replayed"
    )


def _valid_operator_history_entry(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == _OPERATOR_HISTORY_KEYS
        and _valid_resolution(
            {key: item for key, item in value.items() if key in _RESOLUTION_KEYS}
        )
        and isinstance(value.get("prior_state"), str)
        and re.fullmatch(r"[a-z_]{1,64}", value["prior_state"])
        and _safe_iso(value.get("prior_updated_at"), optional=True)
    )


def _valid_archive_history_entry(value: Any) -> bool:
    scope = value.get("scope") if isinstance(value, dict) else None
    claim_key = value.get("claim_key") if isinstance(value, dict) else None
    return bool(
        isinstance(value, dict)
        and set(value) == {"scope", "claim_key", "prior_evidence", "resolution"}
        and (
            (
                scope == "request"
                and isinstance(claim_key, str)
                and 1 <= len(claim_key) <= 128
            )
            or (scope == "claims_container" and claim_key is None)
        )
        and _valid_resolution(value.get("resolution"))
    )


def _validated_history(
    value: Any,
    *,
    entry_validator: Callable[[Any], bool],
    max_bytes: int,
    require_append_room: bool,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _conflict(
            "Stored candidate-chat reconciliation history is malformed; no evidence was changed."
        )
    limit = _MAX_RECONCILIATION_HISTORY - (1 if require_append_room else 0)
    if len(value) > limit:
        raise _conflict(
            "Candidate-chat reconciliation history reached its safety limit; no evidence was changed."
        )
    if not candidate_chat_evidence_within_limits(
        value,
        max_bytes=max_bytes,
        max_nodes=50_000,
    ):
        raise _conflict(
            "Candidate-chat reconciliation history exceeds its byte limit; no evidence was changed."
        )
    if not all(entry_validator(item) for item in value):
        raise _conflict(
            "Stored candidate-chat reconciliation history is malformed; no evidence was changed."
        )
    return deepcopy(value)


def _resolution(
    *,
    record: CandidateChatReconciliationRecord,
    actor_id: int,
    resolved_at: str,
) -> dict[str, Any]:
    return {
        "action": "close_without_replay",
        "actor_id": int(actor_id),
        "actor_type": "workspace_owner",
        "resolved_at": resolved_at,
        "operation_id": record.operation_id,
        "request_reference": record.request_reference,
        "issue_code": record.issue_code,
        "provider_outcome_discarded_attested": True,
        "disposition": "provider_outcome_not_replayed",
    }


def _archive_unreadable_evidence(
    analytics: dict[str, Any],
    *,
    record: CandidateChatReconciliationRecord,
    prior_evidence: Any,
    resolution: dict[str, Any],
) -> None:
    archives = _validated_history(
        analytics.get(CHAT_RECONCILIATION_ARCHIVE_KEY),
        entry_validator=_valid_archive_history_entry,
        max_bytes=_MAX_ARCHIVE_HISTORY_BYTES,
        require_append_room=True,
    )
    archives.append(
        {
            "scope": record.scope,
            "claim_key": record.claim_key,
            "prior_evidence": prior_evidence,
            "resolution": resolution,
        }
    )
    analytics[CHAT_RECONCILIATION_ARCHIVE_KEY] = _validated_history(
        archives,
        entry_validator=_valid_archive_history_entry,
        max_bytes=_MAX_ARCHIVE_HISTORY_BYTES,
        require_append_room=False,
    )


def _close_request_record(
    analytics: dict[str, Any],
    *,
    record: CandidateChatReconciliationRecord,
    resolution: dict[str, Any],
) -> None:
    stored_claims = analytics.get(CHAT_CLAIMS_KEY)
    if not isinstance(stored_claims, dict) or record.claim_key not in stored_claims:
        raise _conflict(
            "The candidate-chat claim changed. Refresh before reconciling it."
        )
    claims = dict(stored_claims)
    current = claims[record.claim_key]
    if not isinstance(current, dict):
        _archive_unreadable_evidence(
            analytics,
            record=record,
            prior_evidence=current,
            resolution=resolution,
        )
        claims[record.claim_key] = {
            "state": CLOSED_NO_REPLAY_STATE,
            "reconciliation_original_state": "malformed_claim_record",
            "operator_reconciliation": deepcopy(resolution),
        }
        analytics[CHAT_CLAIMS_KEY] = claims
        return

    claim = deepcopy(current)
    history = _validated_history(
        claim.get(CHAT_RECONCILIATION_HISTORY_KEY),
        entry_validator=_valid_operator_history_entry,
        max_bytes=_MAX_OPERATOR_HISTORY_BYTES,
        require_append_room=True,
    )
    history.append(
        {
            **deepcopy(resolution),
            "prior_state": str(record.public.get("state") or "unrecognized"),
            "prior_updated_at": record.public.get("updated_at"),
        }
    )
    history = _validated_history(
        history,
        entry_validator=_valid_operator_history_entry,
        max_bytes=_MAX_OPERATOR_HISTORY_BYTES,
        require_append_room=False,
    )
    claim[CHAT_RECONCILIATION_HISTORY_KEY] = history
    claim["state"] = CLOSED_NO_REPLAY_STATE
    claims[record.claim_key] = claim
    analytics[CHAT_CLAIMS_KEY] = claims


def _close_claims_container(
    analytics: dict[str, Any],
    *,
    record: CandidateChatReconciliationRecord,
    resolution: dict[str, Any],
) -> None:
    current = analytics.get(CHAT_CLAIMS_KEY)
    if not bool(record.public.get("can_close_without_replay")):
        raise _conflict(
            "This candidate-chat evidence is too large to reconcile safely; no evidence was changed."
        )
    _archive_unreadable_evidence(
        analytics,
        record=record,
        prior_evidence=current,
        resolution=resolution,
    )
    analytics[CHAT_CLAIMS_KEY] = {}


def reconcile_candidate_chat_operation(
    db: Session,
    *,
    assessment_id: int,
    organization_id: int,
    actor_id: int,
    operation_id: str,
    expected_request_reference: str,
    action: str,
    provider_outcome_discarded_attested: bool,
) -> dict[str, Any]:
    """Close one exact claim without importing, finalizing, or replaying it."""

    if action != "close_without_replay":
        raise HTTPException(status_code=422, detail="Unsupported reconciliation action")
    if not provider_outcome_discarded_attested:
        raise HTTPException(
            status_code=422,
            detail=(
                "Confirm that the unresolved AI response will be discarded and "
                "will not be replayed before closing this request."
            ),
        )
    _validate_operation_identity(
        operation_id=str(operation_id),
        expected_request_reference=str(expected_request_reference),
    )
    row = _locked_assessment(
        db,
        assessment_id=int(assessment_id),
        organization_id=int(organization_id),
    )
    record = _find_exact_record(
        row,
        operation_id=str(operation_id),
        expected_request_reference=str(expected_request_reference),
    )
    if not isinstance(row.prompt_analytics, dict):
        raise _conflict(
            "Stored candidate-chat analytics are malformed; no evidence was changed."
        )
    analytics = dict(row.prompt_analytics)
    resolved_at = datetime.now(timezone.utc).isoformat()
    resolution = _resolution(
        record=record,
        actor_id=int(actor_id),
        resolved_at=resolved_at,
    )
    if record.scope == "request":
        _close_request_record(
            analytics,
            record=record,
            resolution=resolution,
        )
    elif record.scope == "claims_container":
        _close_claims_container(
            analytics,
            record=record,
            resolution=resolution,
        )
    else:  # pragma: no cover - records are constructed by the pure discovery module
        raise _conflict("Unsupported candidate-chat reconciliation scope")

    row.prompt_analytics = analytics
    append_assessment_timeline_event(
        row,
        "candidate_chat_reconciled_no_replay_by_owner",
        {
            "actor_id": int(actor_id),
            "request_reference": record.request_reference,
            "issue_code": record.issue_code,
            "scope": record.scope,
            "disposition": "provider_outcome_not_replayed",
        },
    )
    db.commit()
    return {
        "status": CLOSED_NO_REPLAY_STATE,
        "resolved_operation_id": record.operation_id,
        "request_reference": record.request_reference,
        "candidate_chat_reconciliation": public_candidate_chat_reconciliation(
            row,
            can_reconcile=True,
        ),
    }


__all__ = [
    "list_candidate_chat_reconciliation_operations",
    "reconcile_candidate_chat_operation",
]
