"""Atomic safety gate for reusing a soft-deleted application row."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .ats_reconciliation_evidence import has_exact_reconciliation_resolution
from .document_service import sanitize_json_for_storage

PROVIDER_OPERATION_RECEIPT_KEYS = (
    "auto_reject_operation",
    "outcome_writeback",
    "outcome_writeback_reconciliation",
    "cv_gap_rejection_operation",
    "stage_move_operation",
    "decision_provider_operation",
    "ats_note_writeback",
)
_RECEIPT_KEYS = PROVIDER_OPERATION_RECEIPT_KEYS
_RECEIPT_HISTORY_KEYS = (
    "stage_move_operation_history",
    "decision_provider_operation_history",
    "ats_note_writeback_history",
)
_PRE_PROVIDER_STATUSES = frozenset({"authorized", "queued"})
_POST_PROVIDER_NONTERMINAL_STATUSES = frozenset(
    {"provider_call_started", "provider_succeeded", "retry_authorized"}
)


class LifecycleRestoreDeferred(RuntimeError):
    """The old lifecycle still has an unresolved ATS side effect."""

    staff_detail = (
        "An ATS outcome update is still in flight or needs reconciliation. "
        "Wait for the background job to finish, then verify the ATS status "
        "before restoring this application."
    )
    public_detail = (
        "We’re still reconciling the prior application with the hiring system. "
        "Please try again shortly or contact the hiring team."
    )

    def __init__(self, receipt_key: str):
        self.receipt_key = receipt_key
        if receipt_key == "stage_move_operation":
            self.staff_detail = (
                "An ATS stage move is still in flight or needs verification. "
                "Verify the exact remote stage before restoring this application."
            )
            self.public_detail = (
                "We’re still verifying a hiring-system stage update. "
                "Please try again shortly or contact the hiring team."
            )
        super().__init__(self.staff_detail)


class LifecycleOutcomeMutationDeferred(RuntimeError):
    """A newer local outcome cannot replace unresolved provider work."""

    detail = (
        "An ATS outcome update is still in flight or needs reconciliation. "
        "Wait for the background job to finish and verify the ATS status "
        "before changing this outcome."
    )

    def __init__(self, receipt_key: str):
        self.receipt_key = receipt_key
        if receipt_key == "stage_move_operation":
            self.detail = (
                "An ATS stage move is still in flight or needs verification. "
                "Verify the exact remote stage before changing this outcome."
            )
        super().__init__(self.detail)


class UnresolvedProviderOperation(RuntimeError):
    """A different operation still owns this application's provider boundary."""

    def __init__(self, receipt_key: str, operation_id: str | None):
        self.receipt_key = receipt_key
        self.operation_id = str(operation_id or "") or None
        super().__init__(
            "Another ATS operation is queued, in flight, or needs verification "
            f"({receipt_key}). Verify or finish it before starting a new provider call."
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolved(receipt: dict[str, Any], *, receipt_key: str) -> bool:
    return has_exact_reconciliation_resolution(
        receipt,
        receipt_key=receipt_key,
    )


def receipt_blocks_lifecycle_restore(
    receipt: dict[str, Any], *, receipt_key: str
) -> bool:
    """Fail closed for in-flight, post-provider, or ambiguous ATS outcomes."""

    if _resolved(receipt, receipt_key=receipt_key):
        return False
    status = str(receipt.get("status") or "").strip().lower()
    return bool(
        status in _POST_PROVIDER_NONTERMINAL_STATUSES
        or status == "manual_reconciliation_required"
        or receipt.get("manual_reconciliation_required") is True
        or receipt.get("provider_outcome_uncertain") is True
        or (
            receipt.get("provider_succeeded") is True
            and status not in {"completed", "confirmed"}
        )
    )


def receipt_is_unresolved_provider_operation(
    receipt: dict[str, Any], *, receipt_key: str
) -> bool:
    """Classify whether a receipt may still call, or has crossed, a provider.

    Cross-operation fencing is intentionally stricter than lifecycle restore:
    a queued/authorized receipt blocks because its worker could otherwise claim
    immediately after this transaction releases the application lock.
    """

    if _resolved(receipt, receipt_key=receipt_key):
        return False
    status = str(receipt.get("status") or "").strip().lower()
    if (
        receipt.get("manual_reconciliation_required") is True
        or receipt.get("provider_outcome_uncertain") is True
        or status
        in {
            "authorized",
            "queued",
            "provider_call_started",
            "provider_succeeded",
            "manual_reconciliation_required",
        }
    ):
        return True
    if status in {"confirmed", "completed"}:
        return False
    if status in {"superseded", "cancelled", "cancelled_before_provider"}:
        return False
    if status == "failed":
        return receipt.get("provider_called") is not False
    # Unknown receipt shapes fail closed. A terminal producer must provide one
    # of the exact statuses above; absence of evidence is not proof of safety.
    return True


def require_no_other_unresolved_provider_operation(
    app: CandidateApplication,
    *,
    receipt_key: str,
    operation_id: str | None,
) -> None:
    """Require exclusive app-level provider ownership under the app row lock.

    The exact current operation may resume its own phase C/replay handling. No
    other receipt is superseded or mutated by this check.
    """

    state = (
        app.integration_sync_state
        if isinstance(app.integration_sync_state, dict)
        else {}
    )
    expected_operation_id = str(operation_id or "")
    for other_key in PROVIDER_OPERATION_RECEIPT_KEYS:
        other = state.get(other_key)
        if not isinstance(other, dict):
            continue
        same_operation = bool(
            other_key == receipt_key
            and expected_operation_id
            and str(other.get("operation_id") or "") == expected_operation_id
        )
        if same_operation:
            continue
        if receipt_is_unresolved_provider_operation(other, receipt_key=other_key):
            raise UnresolvedProviderOperation(
                other_key,
                str(other.get("operation_id") or "") or None,
            )


def _supersede_pre_provider_receipt(
    receipt: dict[str, Any], *, actor_type: str, target_outcome: str, now: str
) -> None:
    status = str(receipt.get("status") or "").strip().lower()
    if (
        status not in _PRE_PROVIDER_STATUSES
        or receipt.get("provider_called") not in {False, None}
        or receipt.get("provider_outcome_uncertain") is True
    ):
        return
    receipt.update(
        status="superseded",
        superseded_at=now,
        superseded_by_actor_type=str(actor_type or "system")[:32],
        superseded_by_target_outcome=str(target_outcome or "open").lower(),
        provider_called=False,
        provider_succeeded=False,
        provider_outcome_uncertain=False,
        manual_reconciliation_required=False,
        updated_at=now,
    )


def fence_application_lifecycle_restore(
    db: Session,
    app: CandidateApplication,
    *,
    actor_type: str,
    target_outcome: str = "open",
) -> bool:
    """Lock, reject unresolved ATS effects, then fence every queued receipt.

    The caller keeps the application lock only through its local restore
    transaction. ATS workers persist ``provider_call_started`` before releasing
    this same lock, so either the restore wins and supersedes their snapshot or
    the provider claim wins and this restore is deferred. No row lock spans ATS
    network I/O.
    """

    if app.deleted_at is None or app.id is None:
        return False
    try:
        locked = lock_application_outcome_snapshot(db, app)
    except LifecycleOutcomeMutationDeferred as exc:
        raise LifecycleRestoreDeferred(exc.receipt_key) from None
    if locked.deleted_at is None:
        return False
    state = (
        dict(locked.integration_sync_state)
        if isinstance(locked.integration_sync_state, dict)
        else {}
    )
    for receipt_key in _RECEIPT_KEYS:
        receipt = state.get(receipt_key)
        if isinstance(receipt, dict) and receipt_blocks_lifecycle_restore(
            receipt, receipt_key=receipt_key
        ):
            raise LifecycleRestoreDeferred(receipt_key)

    now = _now()
    locked.version = int(locked.version or 1) + 1
    for receipt_key in _RECEIPT_KEYS:
        receipt = state.get(receipt_key)
        if isinstance(receipt, dict):
            receipt = dict(receipt)
            previous_status = str(receipt.get("status") or "")
            _supersede_pre_provider_receipt(
                receipt,
                actor_type=actor_type,
                target_outcome=target_outcome,
                now=now,
            )
            if str(receipt.get("status") or "") != previous_status:
                receipt["observed_application_version"] = int(locked.version)
            state[receipt_key] = receipt
    locked.integration_sync_state = sanitize_json_for_storage(state)
    return True


def fence_application_outcome_mutation(
    db: Session,
    app: CandidateApplication,
    *,
    target_outcome: str,
    actor_type: str,
    matching_operation_id: str | None,
    already_locked: bool = False,
) -> bool:
    """Block post-provider A/B races; supersede only provably uncalled work."""

    locked = app if already_locked else lock_application_outcome_snapshot(db, app)
    state = (
        dict(locked.integration_sync_state)
        if isinstance(locked.integration_sync_state, dict)
        else {}
    )
    now = _now()
    changed = False
    for receipt_key in _RECEIPT_KEYS:
        raw = state.get(receipt_key)
        if not isinstance(raw, dict):
            continue
        receipt = dict(raw)
        operation_id = str(receipt.get("operation_id") or "")
        if matching_operation_id and operation_id == str(matching_operation_id):
            continue
        if receipt_blocks_lifecycle_restore(receipt, receipt_key=receipt_key):
            raise LifecycleOutcomeMutationDeferred(receipt_key)
        before = str(receipt.get("status") or "")
        _supersede_pre_provider_receipt(
            receipt,
            actor_type=actor_type,
            target_outcome=target_outcome,
            now=now,
        )
        if str(receipt.get("status") or "") != before:
            receipt["observed_application_version"] = int(locked.version or 1)
            state[receipt_key] = receipt
            changed = True
    if changed:
        locked.integration_sync_state = sanitize_json_for_storage(state)
    return changed


def lock_application_outcome_snapshot(
    db: Session, app: CandidateApplication
) -> CandidateApplication:
    """Reload lifecycle authority under lock before computing a transition."""

    state = inspect(app)
    with db.no_autoflush:
        snapshot = (
            db.query(
                CandidateApplication.version,
                CandidateApplication.application_outcome,
                CandidateApplication.deleted_at,
                CandidateApplication.integration_sync_state,
                CandidateApplication.workable_candidate_id,
                CandidateApplication.bullhorn_job_submission_id,
            )
            .filter(
                CandidateApplication.id == int(app.id),
                CandidateApplication.organization_id == int(app.organization_id),
            )
            .with_for_update(of=CandidateApplication)
            .one_or_none()
        )
    if snapshot is None:
        raise LifecycleOutcomeMutationDeferred("application_unavailable")

    def _reconcile_scalar(name: str, fresh: Any) -> None:
        attribute = state.attrs[name]
        history = attribute.history
        if not history.has_changes():
            setattr(app, name, fresh)
            return
        original = history.deleted[0] if history.deleted else fresh
        if original != fresh:
            raise LifecycleOutcomeMutationDeferred("application_changed")

    _reconcile_scalar("version", snapshot.version)
    _reconcile_scalar("application_outcome", snapshot.application_outcome)
    _reconcile_scalar("deleted_at", snapshot.deleted_at)
    _reconcile_scalar("workable_candidate_id", snapshot.workable_candidate_id)
    _reconcile_scalar(
        "bullhorn_job_submission_id", snapshot.bullhorn_job_submission_id
    )
    fresh_sync_state = (
        dict(snapshot.integration_sync_state)
        if isinstance(snapshot.integration_sync_state, dict)
        else {}
    )
    sync_history = state.attrs.integration_sync_state.history
    if sync_history.has_changes():
        merged_sync_state = (
            dict(app.integration_sync_state)
            if isinstance(app.integration_sync_state, dict)
            else {}
        )
        original_sync_state = (
            dict(sync_history.deleted[0])
            if sync_history.deleted and isinstance(sync_history.deleted[0], dict)
            else {}
        )
        # A restore gate can supersede receipts in this same transaction. With
        # autoflush disabled the locked SELECT still sees the original DB
        # value; in that case the local edit is newer and must survive. Only
        # overlay receipts when the DB diverged from the state first observed
        # by this session, which proves a concurrent provider-phase change.
        if original_sync_state != fresh_sync_state:
            for receipt_key in _RECEIPT_KEYS:
                fresh_receipt = fresh_sync_state.get(receipt_key)
                if isinstance(fresh_receipt, dict):
                    merged_sync_state[receipt_key] = dict(fresh_receipt)
                else:
                    merged_sync_state.pop(receipt_key, None)
            for history_key in _RECEIPT_HISTORY_KEYS:
                fresh_history = fresh_sync_state.get(history_key)
                local_history = merged_sync_state.get(history_key)
                merged_history = _merge_receipt_histories(
                    fresh_history if isinstance(fresh_history, list) else [],
                    local_history if isinstance(local_history, list) else [],
                )
                if merged_history:
                    merged_sync_state[history_key] = merged_history
                else:
                    merged_sync_state.pop(history_key, None)
        app.integration_sync_state = sanitize_json_for_storage(merged_sync_state)
    else:
        app.integration_sync_state = sanitize_json_for_storage(fresh_sync_state)
    return app


def _history_evidence_identity(item: dict[str, Any]) -> tuple[str, ...]:
    operation_id = str(item.get("operation_id") or "")
    status = str(item.get("status") or "")
    updated_at = str(item.get("updated_at") or "")
    if operation_id or status or updated_at:
        return ("receipt", operation_id, status, updated_at)
    fingerprint = str(
        item.get("snapshot_fingerprint") or item.get("fingerprint") or ""
    )
    if fingerprint:
        return ("fingerprint", fingerprint)
    # Anonymous legacy evidence is deduplicated only when every stored value is
    # equal. repr(sorted(...)) is stable for sanitized JSON-compatible values.
    return ("legacy", repr(sorted(item.items())))


def _merge_receipt_histories(
    fresh_history: list[Any], local_history: list[Any]
) -> list[dict[str, Any]]:
    """Union append-only evidence while retaining each source's order."""

    merged: list[dict[str, Any]] = []
    identities: set[tuple[str, ...]] = set()
    for source in (fresh_history, local_history):
        for raw in source:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            identity = _history_evidence_identity(item)
            if identity in identities:
                continue
            identities.add(identity)
            merged.append(item)
    return merged


__all__ = [
    "LifecycleOutcomeMutationDeferred",
    "LifecycleRestoreDeferred",
    "PROVIDER_OPERATION_RECEIPT_KEYS",
    "UnresolvedProviderOperation",
    "fence_application_outcome_mutation",
    "fence_application_lifecycle_restore",
    "lock_application_outcome_snapshot",
    "receipt_is_unresolved_provider_operation",
    "receipt_blocks_lifecycle_restore",
    "require_no_other_unresolved_provider_operation",
]
