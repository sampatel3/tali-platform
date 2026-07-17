"""Durable identity and evidence for provider-gated decision execution.

The current receipt is stored on the canonical application because the ATS
target belongs to that application. Replaced terminal receipts are copied to
append-only history; an unresolved receipt is never overwritten by a later
decision or by a retry with different authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from ..models.candidate_application import CandidateApplication
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage


DECISION_PROVIDER_OPERATION_KEY = "decision_provider_operation"
DECISION_PROVIDER_HISTORY_KEY = "decision_provider_operation_history"
_UNRESOLVED_STATUSES = frozenset(
    {
        "authorized",
        "queued",
        "provider_call_started",
        "provider_succeeded",
        "manual_reconciliation_required",
    }
)
_OTHER_OPERATION_KEYS = (
    "auto_reject_operation",
    "cv_gap_rejection_operation",
    "outcome_writeback",
    "outcome_writeback_reconciliation",
    "stage_move_operation",
)


class DecisionProviderReceiptConflict(RuntimeError):
    """A different or uncertain provider operation owns this application."""


@dataclass(frozen=True)
class DecisionProviderSnapshot:
    organization_id: int
    application_id: int
    expected_application_version: int
    expected_application_outcome: str
    expected_pipeline_stage: str
    expected_workable_disqualified: bool
    candidate_id: int
    candidate_provider_id: str | None
    decision_id: int
    expected_decision_status: str
    expected_decision_type: str
    decision_identity_fingerprint: str
    disposition: str
    operation_action: str
    override_action: str | None
    acting_role_id: int
    expected_acting_role_version: int
    owner_role_id: int
    expected_owner_role_version: int
    role_family_fingerprint: str
    workspace_control_version: int
    provider: str
    provider_target_id: str
    provider_remote_stage: str | None
    target_stage: str | None
    provider_connection_key: str
    owner_external_job_id: str | None
    related_evaluation_id: int | None = None
    related_evaluation_status: str | None = None
    related_pipeline_stage: str | None = None
    related_spec_fingerprint: str | None = None
    related_source_application_id: int | None = None

    def fingerprint(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state(app: CandidateApplication) -> dict[str, Any]:
    return (
        dict(app.integration_sync_state)
        if isinstance(app.integration_sync_state, dict)
        else {}
    )


def decision_provider_receipt(app: CandidateApplication) -> dict[str, Any] | None:
    receipt = _state(app).get(DECISION_PROVIDER_OPERATION_KEY)
    return dict(receipt) if isinstance(receipt, dict) else None


def conflicting_ats_operation(app: CandidateApplication) -> str | None:
    """Return a different unresolved receipt key without modifying evidence."""

    from .ats_reconciliation_evidence import has_exact_reconciliation_resolution

    state = _state(app)
    for key in _OTHER_OPERATION_KEYS:
        receipt = state.get(key)
        if not isinstance(receipt, dict):
            continue
        if has_exact_reconciliation_resolution(receipt, receipt_key=key):
            continue
        status = str(receipt.get("status") or "").strip().lower()
        if (
            status in _UNRESOLVED_STATUSES
            or receipt.get("manual_reconciliation_required") is True
            or receipt.get("provider_outcome_uncertain") is True
            or (
                receipt.get("provider_succeeded") is True
                and status not in {"completed", "confirmed", "resolved"}
            )
        ):
            return key
    return None


def snapshot_from_receipt(receipt: dict[str, Any]) -> DecisionProviderSnapshot:
    fields = DecisionProviderSnapshot.__dataclass_fields__
    return DecisionProviderSnapshot(**{name: receipt.get(name) for name in fields})


def decision_identity_fingerprint(decision: Any) -> str:
    """Hash every execution-relevant immutable recommendation input."""

    payload = {
        "application_id": int(decision.application_id),
        "role_id": int(decision.role_id),
        "decision_type": str(decision.decision_type or ""),
        "recommendation": str(decision.recommendation or ""),
        "reasoning": str(decision.reasoning or ""),
        "evidence": decision.evidence if isinstance(decision.evidence, dict) else {},
        "model_version": str(decision.model_version or ""),
        "prompt_version": str(decision.prompt_version or ""),
        "criteria_fingerprint": str(decision.criteria_fingerprint or ""),
        "cv_fingerprint": str(decision.cv_fingerprint or ""),
        "input_fingerprint": (
            decision.input_fingerprint
            if isinstance(decision.input_fingerprint, dict)
            else {}
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def operation_id_for(snapshot: DecisionProviderSnapshot) -> str:
    return (
        f"decision-provider:{snapshot.organization_id}:{snapshot.decision_id}:"
        f"{snapshot.disposition}:{snapshot.operation_action}:"
        f"{snapshot.fingerprint()[:32]}"
    )[:200]


def _history(state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = state.get(DECISION_PROVIDER_HISTORY_KEY)
    return (
        [dict(item) for item in raw if isinstance(item, dict)]
        if isinstance(raw, list)
        else []
    )


def _archive_once(state: dict[str, Any], receipt: dict[str, Any]) -> None:
    history = _history(state)
    identity = (
        str(receipt.get("operation_id") or ""),
        str(receipt.get("status") or ""),
        str(receipt.get("updated_at") or ""),
    )
    if not any(
        (
            str(item.get("operation_id") or ""),
            str(item.get("status") or ""),
            str(item.get("updated_at") or ""),
        )
        == identity
        for item in history
    ):
        history.append(dict(receipt))
    state[DECISION_PROVIDER_HISTORY_KEY] = history


def _write(app: CandidateApplication, receipt: dict[str, Any]) -> dict[str, Any]:
    state = _state(app)
    state[DECISION_PROVIDER_OPERATION_KEY] = dict(receipt)
    app.integration_sync_state = sanitize_json_for_storage(state)
    return receipt


def begin_decision_provider_receipt(
    app: CandidateApplication,
    *,
    snapshot: DecisionProviderSnapshot,
    operation_id: str,
    actor_type: str,
    actor_id: int | None,
    reason: str | None,
    job_run_id: int | None,
) -> tuple[dict[str, Any], str]:
    """Claim an exact provider boundary and classify a replay."""

    state = _state(app)
    current = decision_provider_receipt(app)
    fingerprint = snapshot.fingerprint()
    historical = next(
        (
            item
            for item in reversed(_history(state))
            if str(item.get("operation_id") or "") == str(operation_id)
        ),
        None,
    )
    if historical is not None:
        if str(historical.get("snapshot_fingerprint") or "") != fingerprint:
            raise DecisionProviderReceiptConflict(
                "A historical decision operation reused this id with different authority"
            )
        historical_status = str(historical.get("status") or "").strip().lower()
        if historical_status == "confirmed":
            return historical, "confirmed_replay"
        if (
            historical_status in _UNRESOLVED_STATUSES
            or historical.get("provider_outcome_uncertain") is True
            or historical.get("provider_called") is not False
        ):
            # A replaced operation that crossed the provider boundary can
            # never become callable again merely because another receipt is
            # current. Keep its append-only evidence and fail closed.
            return historical, "reconciliation_required"
    same = bool(
        current
        and str(current.get("operation_id") or "") == str(operation_id)
    )
    if current is not None:
        status = str(current.get("status") or "").strip().lower()
        if same and str(current.get("snapshot_fingerprint") or "") != fingerprint:
            raise DecisionProviderReceiptConflict(
                "The decision provider operation no longer has the same authority"
            )
        if same and status == "confirmed":
            return current, "confirmed_replay"
        if same and status == "provider_succeeded":
            return current, "finalize_provider_success"
        if same and (
            status in {"provider_call_started", "manual_reconciliation_required"}
            or current.get("provider_outcome_uncertain") is True
        ):
            return current, "reconciliation_required"
        if same and not (
            status == "failed" and current.get("provider_called") is False
        ):
            raise DecisionProviderReceiptConflict(
                "The decision provider operation cannot be safely rearmed"
            )
        if not same and (
            status in _UNRESOLVED_STATUSES
            or current.get("manual_reconciliation_required") is True
            or current.get("provider_outcome_uncertain") is True
        ):
            raise DecisionProviderReceiptConflict(
                "Another decision provider operation still needs reconciliation"
            )
        if not same:
            _archive_once(state, current)

    now = _now()
    attempts = int(current.get("provider_attempts") or 0) + 1 if same else 1
    receipt: dict[str, Any] = {
        **asdict(snapshot),
        "operation_id": str(operation_id),
        "snapshot_fingerprint": fingerprint,
        "status": "provider_call_started",
        "provider_attempts": attempts,
        "provider_call_started_at": now,
        "provider_called": None,
        "provider_succeeded": None,
        "provider_outcome_uncertain": True,
        "manual_reconciliation_required": False,
        "actor_type": str(actor_type or "recruiter")[:32],
        "actor_id": int(actor_id) if actor_id else None,
        "reason": sanitize_text_for_storage(str(reason or "").strip()) or None,
        "requested_at": current.get("requested_at") if same else now,
        "updated_at": now,
    }
    if job_run_id is not None:
        receipt["job_run_id"] = int(job_run_id)
    state[DECISION_PROVIDER_OPERATION_KEY] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)
    return receipt, "call_provider"


def fail_decision_provider_receipt(
    app: CandidateApplication,
    *,
    operation_id: str,
    code: str,
    message: str,
    provider_called: bool | None,
    retryable: bool,
    expected_snapshot_fingerprint: str,
    expected_status: str = "provider_call_started",
) -> dict[str, Any] | None:
    receipt = decision_provider_receipt(app)
    if (
        receipt is None
        or str(receipt.get("operation_id") or "") != operation_id
        or str(receipt.get("snapshot_fingerprint") or "")
        != str(expected_snapshot_fingerprint)
        or str(receipt.get("status") or "") != str(expected_status)
    ):
        return None
    uncertain = provider_called is None
    receipt.update(
        status=("manual_reconciliation_required" if uncertain else "failed"),
        provider_called=provider_called,
        provider_succeeded=(False if provider_called is False else None),
        provider_outcome_uncertain=uncertain,
        manual_reconciliation_required=uncertain,
        reconciliation_reason=("provider_result_ambiguous" if uncertain else None),
        failure_code=sanitize_text_for_storage(str(code or "provider_error")),
        failure_message=sanitize_text_for_storage(str(message or "ATS update failed")),
        failure_retryable=bool(retryable),
        observed_application_version=int(app.version or 1),
        observed_application_outcome=str(app.application_outcome or "open"),
        observed_pipeline_stage=str(app.pipeline_stage or "applied"),
        updated_at=_now(),
    )
    return _write(app, receipt)


def checkpoint_decision_provider_success(
    app: CandidateApplication,
    *,
    operation_id: str,
    expected_snapshot_fingerprint: str,
    provider_result: dict[str, Any],
) -> dict[str, Any] | None:
    """CAS provider success before local finalization.

    A worker crash after this checkpoint resumes phase C without issuing a
    second provider request. A missing/replaced receipt is never recreated.
    """

    receipt = decision_provider_receipt(app)
    if (
        receipt is None
        or str(receipt.get("operation_id") or "") != operation_id
        or str(receipt.get("snapshot_fingerprint") or "")
        != str(expected_snapshot_fingerprint)
        or str(receipt.get("status") or "") != "provider_call_started"
    ):
        return None
    now = _now()
    receipt.update(
        status="provider_succeeded",
        provider_called=True,
        provider_succeeded=True,
        provider_outcome_uncertain=False,
        manual_reconciliation_required=False,
        provider_remote_stage=(
            provider_result.get("provider_remote_stage")
            or receipt.get("provider_remote_stage")
        ),
        provider_succeeded_at=now,
        updated_at=now,
    )
    return _write(app, receipt)


def mark_decision_provider_reconciliation(
    app: CandidateApplication,
    *,
    snapshot: DecisionProviderSnapshot,
    operation_id: str,
    reason: str,
    provider_called: bool | None,
    provider_succeeded: bool | None,
) -> dict[str, Any]:
    """Retain reconciliation evidence even when a newer receipt replaced it."""

    now = _now()
    current = decision_provider_receipt(app)
    evidence = {
        **asdict(snapshot),
        "operation_id": operation_id,
        "snapshot_fingerprint": snapshot.fingerprint(),
        "status": "manual_reconciliation_required",
        "provider_called": provider_called,
        "provider_succeeded": provider_succeeded,
        "provider_outcome_uncertain": provider_succeeded is None,
        "manual_reconciliation_required": True,
        "reconciliation_reason": sanitize_text_for_storage(str(reason)),
        "reconciliation_required_at": now,
        "observed_application_version": int(app.version or 1),
        "observed_application_outcome": str(app.application_outcome or "open"),
        "observed_pipeline_stage": str(app.pipeline_stage or "applied"),
        "updated_at": now,
    }
    state = _state(app)
    if current is not None and str(current.get("operation_id") or "") == operation_id:
        evidence = {**current, **evidence}
        state[DECISION_PROVIDER_OPERATION_KEY] = evidence
    else:
        _archive_once(state, evidence)
    app.integration_sync_state = sanitize_json_for_storage(state)
    return evidence


def confirm_decision_provider_receipt(
    app: CandidateApplication,
    *,
    operation_id: str,
    provider_result: dict[str, Any],
    post_operation: dict[str, Any] | None,
    expected_snapshot_fingerprint: str,
    expected_status: str = "provider_succeeded",
) -> dict[str, Any] | None:
    receipt = decision_provider_receipt(app)
    if (
        receipt is None
        or str(receipt.get("operation_id") or "") != operation_id
        or str(receipt.get("snapshot_fingerprint") or "")
        != str(expected_snapshot_fingerprint)
        or str(receipt.get("status") or "") != str(expected_status)
    ):
        return None
    now = _now()
    receipt.update(
        status="confirmed",
        provider_called=True,
        provider_succeeded=True,
        provider_outcome_uncertain=False,
        manual_reconciliation_required=False,
        provider_remote_stage=(
            provider_result.get("provider_remote_stage")
            or receipt.get("provider_remote_stage")
        ),
        confirmed_at=now,
        updated_at=now,
    )
    if post_operation is not None:
        receipt["post_operation"] = dict(post_operation)
    return _write(app, receipt)


def update_decision_post_operation(
    app: CandidateApplication,
    *,
    operation_id: str,
    status: str,
    job_run_id: int | None = None,
) -> dict[str, Any] | None:
    receipt = decision_provider_receipt(app)
    if receipt is None or str(receipt.get("operation_id") or "") != operation_id:
        return None
    post = receipt.get("post_operation")
    if not isinstance(post, dict):
        return receipt
    post = {**post, "status": str(status), "updated_at": _now()}
    if job_run_id is not None:
        post["job_run_id"] = int(job_run_id)
    receipt["post_operation"] = post
    receipt["updated_at"] = _now()
    return _write(app, receipt)


__all__ = [
    "DECISION_PROVIDER_HISTORY_KEY",
    "DECISION_PROVIDER_OPERATION_KEY",
    "DecisionProviderReceiptConflict",
    "DecisionProviderSnapshot",
    "begin_decision_provider_receipt",
    "checkpoint_decision_provider_success",
    "confirm_decision_provider_receipt",
    "conflicting_ats_operation",
    "decision_identity_fingerprint",
    "decision_provider_receipt",
    "fail_decision_provider_receipt",
    "mark_decision_provider_reconciliation",
    "operation_id_for",
    "snapshot_from_receipt",
    "update_decision_post_operation",
]
