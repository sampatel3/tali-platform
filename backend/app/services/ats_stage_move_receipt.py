"""Durable, provider-neutral evidence for ATS stage moves.

The current receipt lives on the canonical application so every application
response can surface an unresolved move.  Replaced terminal receipts are moved
to append-only history; an older provider result is therefore never lost when a
newer operation becomes current.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from ..models.candidate_application import CandidateApplication
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage


STAGE_MOVE_OPERATION_KEY = "stage_move_operation"
STAGE_MOVE_HISTORY_KEY = "stage_move_operation_history"
ACTIVE_STAGE_MOVE_STATUSES = frozenset(
    {"provider_call_started", "provider_succeeded", "manual_reconciliation_required"}
)


class StageMoveReceiptConflict(RuntimeError):
    """A different unresolved move already owns the application receipt."""


@dataclass(frozen=True)
class StageMoveSnapshot:
    organization_id: int
    application_id: int
    expected_application_version: int
    expected_application_outcome: str
    expected_pipeline_stage: str
    expected_workable_disqualified: bool
    expected_candidate_id: int
    expected_owner_role_id: int
    expected_owner_role_version: int
    provider: str
    provider_target_id: str
    target_stage: str
    target_intent: str
    provider_remote_stage: str | None
    owner_external_job_id: str | None
    provider_connection_key: str
    acting_role_id: int | None = None
    expected_acting_role_version: int | None = None
    related_evaluation_id: int | None = None
    related_evaluation_status: str | None = None
    related_pipeline_stage: str | None = None
    related_spec_fingerprint: str | None = None
    candidate_provider_id: str | None = None

    def operation_fingerprint(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_state(app: CandidateApplication) -> dict[str, Any]:
    return (
        dict(app.integration_sync_state)
        if isinstance(app.integration_sync_state, dict)
        else {}
    )


def stage_move_receipt(app: CandidateApplication) -> dict[str, Any] | None:
    value = _sync_state(app).get(STAGE_MOVE_OPERATION_KEY)
    return dict(value) if isinstance(value, dict) else None


def snapshot_from_stage_move_receipt(receipt: dict[str, Any]) -> StageMoveSnapshot:
    """Rebuild the non-secret claim snapshot for crash-safe finalization."""

    names = StageMoveSnapshot.__dataclass_fields__
    return StageMoveSnapshot(**{name: receipt.get(name) for name in names})


def _history(state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = state.get(STAGE_MOVE_HISTORY_KEY)
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _archive_once(state: dict[str, Any], receipt: dict[str, Any]) -> None:
    operation_id = str(receipt.get("operation_id") or "")
    status = str(receipt.get("status") or "")
    history = _history(state)
    if not any(
        str(item.get("operation_id") or "") == operation_id
        and str(item.get("status") or "") == status
        and str(item.get("updated_at") or "") == str(receipt.get("updated_at") or "")
        for item in history
    ):
        history.append(dict(receipt))
    state[STAGE_MOVE_HISTORY_KEY] = history


def _write_current(
    app: CandidateApplication, receipt: dict[str, Any]
) -> dict[str, Any]:
    state = _sync_state(app)
    state[STAGE_MOVE_OPERATION_KEY] = dict(receipt)
    app.integration_sync_state = sanitize_json_for_storage(state)
    return receipt


def stage_move_operation_id(
    snapshot: StageMoveSnapshot, requested_operation_id: object = None
) -> str:
    requested = sanitize_text_for_storage(str(requested_operation_id or "").strip())
    if requested:
        return requested[:200]
    return (
        f"stage-move:{snapshot.application_id}:"
        f"{snapshot.operation_fingerprint()[:40]}"
    )


def begin_stage_move_receipt(
    app: CandidateApplication,
    *,
    snapshot: StageMoveSnapshot,
    operation_id: str,
    job_run_id: int | None,
    actor_type: str,
    actor_id: int | None,
    reason: str | None,
) -> tuple[dict[str, Any], str]:
    """Claim one exact move and return ``(receipt, disposition)``.

    ``disposition`` is ``call_provider``, ``finalize_provider_success``,
    ``confirmed_replay``, or ``reconciliation_required``.
    """

    state = _sync_state(app)
    current = stage_move_receipt(app)
    safe_retry_authorization = False
    if current is not None:
        same = str(current.get("operation_id") or "") == str(operation_id)
        status = str(current.get("status") or "").strip().lower()
        fingerprint = snapshot.operation_fingerprint()
        if same and str(current.get("snapshot_fingerprint") or "") != fingerprint:
            raise StageMoveReceiptConflict(
                "The ATS stage-move operation snapshot no longer matches"
            )
        if same and status == "confirmed":
            return current, "confirmed_replay"
        if same and status == "provider_succeeded":
            return current, "finalize_provider_success"
        if same and (
            status in {"provider_call_started", "manual_reconciliation_required"}
            or current.get("provider_outcome_uncertain") is True
        ):
            # The first attempt crossed the durable provider boundary. Even a
            # state-setting API is not replayed blindly: inspect/reconcile the
            # exact remote target before another write.
            return current, "reconciliation_required"
        safe_retry_authorization = bool(
            status == "retry_authorized"
            and current.get("reconciliation_retry_observation_id")
            and current.get("reconciliation_retry_authorized_by_actor_id") is not None
        )
        if same and not (
            (status == "failed" and current.get("provider_called") is False)
            or safe_retry_authorization
        ):
            raise StageMoveReceiptConflict(
                "The ATS stage-move operation cannot be rearmed from its current state"
            )
        if not same:
            archived = next(
                (
                    item
                    for item in reversed(_history(state))
                    if str(item.get("operation_id") or "") == str(operation_id)
                ),
                None,
            )
            if archived is not None:
                if str(archived.get("snapshot_fingerprint") or "") != fingerprint:
                    raise StageMoveReceiptConflict(
                        "The archived ATS stage-move snapshot no longer matches"
                    )
                archived_status = str(archived.get("status") or "").lower()
                if archived_status == "confirmed":
                    return archived, "confirmed_replay"
                if (
                    archived_status
                    in {
                        "provider_call_started",
                        "provider_succeeded",
                        "manual_reconciliation_required",
                    }
                    or archived.get("provider_outcome_uncertain") is True
                ):
                    return archived, "reconciliation_required"
                raise StageMoveReceiptConflict(
                    "An archived ATS stage move cannot be rearmed over a newer operation"
                )
        if not same and (
            status in ACTIVE_STAGE_MOVE_STATUSES
            or current.get("manual_reconciliation_required") is True
            or current.get("provider_outcome_uncertain") is True
        ):
            raise StageMoveReceiptConflict(
                "Another ATS stage move is still in flight or needs reconciliation"
            )
        elif not same:
            _archive_once(state, current)

    now = _now()
    same_operation = bool(
        current
        and str(current.get("operation_id") or "") == str(operation_id)
    )
    attempts = (
        int(current.get("provider_attempts") or 0) + 1
        if same_operation
        else 1
    )
    if same_operation and str(current.get("status") or "") == "retry_authorized":
        _archive_once(state, current)
    receipt: dict[str, Any] = {
        **asdict(snapshot),
        "operation_id": str(operation_id),
        "snapshot_fingerprint": snapshot.operation_fingerprint(),
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
        "requested_at": (
            current.get("requested_at") if same_operation else now
        ),
        "updated_at": now,
    }
    if job_run_id is not None:
        receipt["job_run_id"] = int(job_run_id)
    if safe_retry_authorization:
        receipt["reconciliation_retry_authorization"] = {
            "observation_id": current.get("reconciliation_retry_observation_id"),
            "authorized_by_actor_id": current.get(
                "reconciliation_retry_authorized_by_actor_id"
            ),
            "authorized_at": current.get("reconciliation_retry_authorized_at"),
        }
    state[STAGE_MOVE_OPERATION_KEY] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)
    return receipt, "call_provider"


def fail_stage_move_receipt(
    app: CandidateApplication,
    *,
    operation_id: str,
    error_code: str,
    error_message: str,
    provider_called: bool | None,
    retryable: bool,
) -> dict[str, Any] | None:
    current = stage_move_receipt(app)
    if current is None or str(current.get("operation_id") or "") != str(operation_id):
        return None
    now = _now()
    uncertain = provider_called is None
    current.update(
        status=("manual_reconciliation_required" if uncertain else "failed"),
        failure_code=sanitize_text_for_storage(str(error_code or "provider_error")),
        failure_message=sanitize_text_for_storage(str(error_message or "ATS move failed")),
        failure_retryable=bool(retryable),
        failed_at=now,
        provider_called=provider_called,
        provider_succeeded=(False if provider_called is False else None),
        provider_outcome_uncertain=uncertain,
        manual_reconciliation_required=uncertain,
        reconciliation_reason=("provider_result_ambiguous" if uncertain else None),
        observed_application_version=int(app.version or 1),
        observed_application_outcome=str(app.application_outcome or "open"),
        observed_pipeline_stage=str(app.pipeline_stage or "applied"),
        updated_at=now,
    )
    return _write_current(app, current)


def reconcile_stage_move_receipt(
    app: CandidateApplication,
    *,
    operation_id: str,
    drift_reason: str,
    provider_remote_stage: str | None,
) -> dict[str, Any] | None:
    current = stage_move_receipt(app)
    if current is None or str(current.get("operation_id") or "") != str(operation_id):
        return None
    now = _now()
    current.update(
        status="manual_reconciliation_required",
        provider_called=True,
        provider_succeeded=True,
        provider_outcome_uncertain=False,
        manual_reconciliation_required=True,
        provider_remote_stage=(provider_remote_stage or current.get("provider_remote_stage")),
        reconciliation_reason=sanitize_text_for_storage(str(drift_reason)),
        reconciliation_required_at=now,
        observed_application_version=int(app.version or 1),
        observed_application_outcome=str(app.application_outcome or "open"),
        observed_pipeline_stage=str(app.pipeline_stage or "applied"),
        updated_at=now,
    )
    return _write_current(app, current)


def checkpoint_stage_move_provider_success(
    app: CandidateApplication,
    *,
    operation_id: str,
    snapshot_fingerprint: str,
    provider_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist exact remote success before attempting the local projection."""

    current = stage_move_receipt(app)
    if (
        current is None
        or str(current.get("operation_id") or "") != str(operation_id)
        or str(current.get("snapshot_fingerprint") or "")
        != str(snapshot_fingerprint)
        or str(current.get("status") or "") != "provider_call_started"
    ):
        return None
    now = _now()
    safe_result = {
        "success": True,
        "code": sanitize_text_for_storage(str(provider_result.get("code") or "ok")),
        "provider_remote_stage": sanitize_text_for_storage(
            str(provider_result.get("provider_remote_stage") or "")
        )
        or None,
        "response_id": sanitize_text_for_storage(
            str(provider_result.get("response_id") or "")
        )
        or None,
    }
    current.update(
        status="provider_succeeded",
        provider_succeeded_at=now,
        provider_called=True,
        provider_succeeded=True,
        provider_outcome_uncertain=False,
        manual_reconciliation_required=False,
        provider_result=safe_result,
        provider_remote_stage=(
            safe_result["provider_remote_stage"]
            or current.get("provider_remote_stage")
        ),
        observed_application_version=int(app.version or 1),
        observed_application_outcome=str(app.application_outcome or "open"),
        observed_pipeline_stage=str(app.pipeline_stage or "applied"),
        updated_at=now,
    )
    return _write_current(app, current)


def append_stage_move_reconciliation_evidence(
    app: CandidateApplication,
    *,
    snapshot: StageMoveSnapshot,
    operation_id: str,
    drift_reason: str,
    provider_remote_stage: str | None,
    provider_called: bool | None,
    provider_succeeded: bool | None,
) -> dict[str, Any]:
    """Retain late provider evidence without replacing a newer receipt."""

    now = _now()
    evidence: dict[str, Any] = {
        **asdict(snapshot),
        "operation_id": str(operation_id),
        "snapshot_fingerprint": snapshot.operation_fingerprint(),
        "status": "manual_reconciliation_required",
        "provider_called": provider_called,
        "provider_succeeded": provider_succeeded,
        "provider_outcome_uncertain": provider_succeeded is None,
        "manual_reconciliation_required": True,
        "provider_remote_stage": provider_remote_stage,
        "reconciliation_reason": sanitize_text_for_storage(str(drift_reason)),
        "reconciliation_required_at": now,
        "observed_application_version": int(app.version or 1),
        "observed_application_outcome": str(app.application_outcome or "open"),
        "observed_pipeline_stage": str(app.pipeline_stage or "applied"),
        "updated_at": now,
    }
    state = _sync_state(app)
    _archive_once(state, evidence)
    app.integration_sync_state = sanitize_json_for_storage(state)
    return evidence


def confirm_stage_move_receipt(
    app: CandidateApplication,
    *,
    operation_id: str,
    provider_remote_stage: str | None,
    related_note: dict[str, Any] | None,
) -> dict[str, Any] | None:
    current = stage_move_receipt(app)
    if current is None or str(current.get("operation_id") or "") != str(operation_id):
        return None
    now = _now()
    current.update(
        status="confirmed",
        confirmed_at=now,
        provider_called=True,
        provider_succeeded=True,
        provider_outcome_uncertain=False,
        manual_reconciliation_required=False,
        provider_remote_stage=(provider_remote_stage or current.get("provider_remote_stage")),
        observed_application_version=int(app.version or 1),
        observed_application_outcome=str(app.application_outcome or "open"),
        observed_pipeline_stage=str(app.pipeline_stage or "applied"),
        updated_at=now,
    )
    if related_note is not None:
        current["related_note"] = sanitize_json_for_storage(related_note)
    return _write_current(app, current)


def mark_stage_move_related_note(
    app: CandidateApplication,
    *,
    operation_id: str,
    status: str,
    job_run_id: int | None = None,
) -> dict[str, Any] | None:
    current = stage_move_receipt(app)
    if current is None or str(current.get("operation_id") or "") != str(operation_id):
        return None
    note = current.get("related_note")
    if not isinstance(note, dict):
        return current
    note = dict(note)
    note["status"] = str(status)
    note["updated_at"] = _now()
    if job_run_id is not None:
        note["job_run_id"] = int(job_run_id)
    current["related_note"] = note
    current["updated_at"] = note["updated_at"]
    return _write_current(app, current)


__all__ = [
    "ACTIVE_STAGE_MOVE_STATUSES",
    "STAGE_MOVE_HISTORY_KEY",
    "STAGE_MOVE_OPERATION_KEY",
    "StageMoveReceiptConflict",
    "StageMoveSnapshot",
    "begin_stage_move_receipt",
    "append_stage_move_reconciliation_evidence",
    "confirm_stage_move_receipt",
    "checkpoint_stage_move_provider_success",
    "fail_stage_move_receipt",
    "mark_stage_move_related_note",
    "reconcile_stage_move_receipt",
    "snapshot_from_stage_move_receipt",
    "stage_move_operation_id",
    "stage_move_receipt",
]
