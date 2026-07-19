"""Checkpoint CAS and bounded poison-batch state for Bullhorn events."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ....models.organization import Organization
from . import event_lifecycle

POISON_EVENT_RETRY_LIMIT = 3
_POISON_STATE_KEY = "event_poison_checkpoint"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock_checkpoint(
    db: Session,
    org: Organization,
    *,
    expected_request_id: str,
    expected_event_epoch: str,
) -> str | None:
    db.refresh(org, with_for_update=True)
    current = str(org.bullhorn_event_request_id or "").strip()
    if current != expected_request_id or event_lifecycle.event_epoch(org) != expected_event_epoch:
        db.rollback()
        return None
    return current


def clear_checkpoint(
    db: Session,
    org: Organization,
    *,
    expected_request_id: str,
    expected_event_epoch: str,
    provider_guard: Callable[[], None] | None = None,
) -> bool:
    completed_request_id = _lock_checkpoint(
        db,
        org,
        expected_request_id=expected_request_id,
        expected_event_epoch=expected_event_epoch,
    )
    if completed_request_id is None:
        return False
    poison = _poison_state(org)
    org.bullhorn_event_request_id = None
    event_lifecycle.record_completed_request_id(org, completed_request_id)
    _drop_poison_state(org)
    if poison is not None:
        _record_public_poison(
            org,
            {
                **_public_poison(poison),
                "status": "replayed_successfully",
                "resolved_at": _now().isoformat(),
            },
        )
    db.add(org)
    if provider_guard is not None:
        provider_guard()
    db.commit()
    return True


def clear_checkpoint_after_gap_recovery(
    db: Session,
    org: Organization,
    *,
    expected_request_id: str,
    expected_event_epoch: str,
    reanchor_request_id: object,
    reconciliation: dict | None = None,
    provider_guard: Callable[[], None] | None = None,
) -> bool:
    """Clear an unsafe anchor only if its request and lifecycle epoch still match."""
    current = _lock_checkpoint(
        db,
        org,
        expected_request_id=expected_request_id,
        expected_event_epoch=expected_event_epoch,
    )
    if current is None:
        return False
    poison = _poison_state(org)
    org.bullhorn_event_request_id = None
    event_lifecycle.record_completed_request_id(org, reanchor_request_id)
    _drop_poison_state(org)
    if poison is not None and str(poison.get("request_id") or "") == current:
        discrepancies = (
            reconciliation.get("discrepancies")
            if isinstance(reconciliation, dict)
            and isinstance(reconciliation.get("discrepancies"), dict)
            else {}
        )
        _record_public_poison(
            org,
            {
                **_public_poison(poison),
                "status": "recovered_by_gap_sweep",
                "resolved_at": _now().isoformat(),
                "reconciliation_ok": bool(
                    isinstance(reconciliation, dict)
                    and reconciliation.get("ok") is True
                ),
                "discrepancy_entities": sorted(discrepancies),
            },
        )
    db.add(org)
    if provider_guard is not None:
        provider_guard()
    db.commit()
    return True


def _poison_state(org: Organization) -> dict | None:
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    state = config.get(_POISON_STATE_KEY)
    return dict(state) if isinstance(state, dict) else None


def _drop_poison_state(org: Organization) -> None:
    config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
    if _POISON_STATE_KEY in config:
        config.pop(_POISON_STATE_KEY, None)
        org.bullhorn_config = config


def _public_poison(state: dict) -> dict:
    return {
        "code": "event_handler_failed",
        "attempts": int(state.get("attempts") or 0),
        "retry_limit": POISON_EVENT_RETRY_LIMIT,
        "batch_fingerprint": str(state.get("batch_fingerprint") or ""),
        "batch_size": int(state.get("batch_size") or 0),
        "error_count": int(state.get("error_count") or 0),
        "entity_types": list(state.get("entity_types") or []),
        "event_types": list(state.get("event_types") or []),
        "first_failed_at": state.get("first_failed_at"),
        "last_failed_at": state.get("last_failed_at"),
    }


def _record_public_poison(org: Organization, telemetry: dict) -> None:
    summary = (
        dict(org.bullhorn_last_sync_summary)
        if isinstance(org.bullhorn_last_sync_summary, dict)
        else {}
    )
    summary["event_poison_batch"] = telemetry
    org.bullhorn_last_sync_summary = summary


def record_poison_failure(
    db: Session,
    org: Organization,
    *,
    request_id: str,
    expected_event_epoch: str,
    batch_size: int,
    failure_telemetry: dict,
    provider_guard: Callable[[], None] | None = None,
) -> dict | None:
    """Durably count one identical failed replay without storing event payloads."""
    if (
        _lock_checkpoint(
            db,
            org,
            expected_request_id=request_id,
            expected_event_epoch=expected_event_epoch,
        )
        is None
    ):
        return None
    now = _now().isoformat()
    prior = _poison_state(org)
    signature = _failure_signature(failure_telemetry)
    same_failure = bool(
        prior is not None
        and str(prior.get("request_id") or "") == str(request_id)
        and str(prior.get("failure_signature") or "") == signature
    )
    attempts = min(
        POISON_EVENT_RETRY_LIMIT,
        (int(prior.get("attempts") or 0) if same_failure and prior else 0) + 1,
    )
    state = {
        "request_id": str(request_id),
        "attempts": attempts,
        "batch_fingerprint": batch_fingerprint(request_id),
        "failure_signature": signature,
        "batch_size": int(batch_size),
        "error_count": int(failure_telemetry.get("error_count") or 0),
        "entity_types": list(failure_telemetry.get("entity_types") or []),
        "event_types": list(failure_telemetry.get("event_types") or []),
        "first_failed_at": (
            prior.get("first_failed_at") if same_failure and prior else now
        ),
        "last_failed_at": now,
    }
    config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
    config[_POISON_STATE_KEY] = state
    org.bullhorn_config = config
    public = {
        **_public_poison(state),
        "status": (
            "recovery_due"
            if attempts >= POISON_EVENT_RETRY_LIMIT
            else "retrying_exact_batch"
        ),
    }
    _record_public_poison(org, public)
    db.add(org)
    if provider_guard is not None:
        provider_guard()
    db.commit()
    return public


def batch_fingerprint(request_id: str) -> str:
    return hashlib.sha256(str(request_id).encode("utf-8")).hexdigest()[:12]


def _failure_signature(telemetry: dict) -> str:
    source = "|".join(
        (
            str(int(telemetry.get("error_count") or 0)),
            ",".join(telemetry.get("entity_types") or []),
            ",".join(telemetry.get("event_types") or []),
        )
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
