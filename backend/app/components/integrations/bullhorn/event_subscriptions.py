"""Crash-safe creation and replacement of Bullhorn event subscriptions."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Literal

from sqlalchemy.orm import Session

from ....models.organization import Organization
from .errors import BullhornApiError
from .event_handlers import SUBSCRIBED_ENTITIES
from .event_state import (
    POLL_INTENT_KEY,
    POISON_STATE_KEY,
    SUBSCRIPTION_STATE_KEY,
    config,
    deployment_namespace,
    new_epoch,
    normalize_request_id,
    set_config_state,
    subscription_state,
)
from .service import BullhornService

_EPOCH_PATTERN = re.compile(r"[0-9a-f]{32}")


class SubscriptionProvenanceError(RuntimeError):
    """Stored subscription state cannot be proven to belong to this deployment."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def deterministic_subscription_id(org: Organization) -> str:
    """Return the stable environment + organization scoped remote id."""
    if org.id is None:
        raise RuntimeError("Bullhorn subscription requires a persisted organization")
    return f"taali-{deployment_namespace()}-org-{int(org.id)}"


def validate_subscription_provenance(org: Organization) -> dict:
    """Fail closed unless local state proves current deployment ownership."""
    stored = str(org.bullhorn_event_subscription_id or "").strip()
    expected = deterministic_subscription_id(org)
    state = subscription_state(org)
    if (
        stored != expected
        or state.get("version") != 1
        or str(state.get("subscription_id") or "") != stored
        or state.get("environment_namespace") != deployment_namespace()
        or state.get("state") not in {"active", "pending"}
        or _EPOCH_PATTERN.fullmatch(str(state.get("anchor_epoch") or "")) is None
    ):
        raise SubscriptionProvenanceError(
            "Bullhorn event subscription ownership is not proven"
        )
    if state.get("state") == "active":
        try:
            normalize_request_id(state.get("last_completed_request_id"))
        except RuntimeError:
            raise SubscriptionProvenanceError(
                "Bullhorn event subscription lifecycle is invalid"
            ) from None
    elif state.get("reason") not in {"create", "recreate"}:
        raise SubscriptionProvenanceError(
            "Bullhorn event subscription lifecycle is invalid"
        )
    return state


def _last_request_id(
    client: BullhornService,
    subscription_id: str,
    *,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    guard = provider_guard or (lambda: None)
    guard()
    try:
        payload = client.get_last_request_id(subscription_id=subscription_id)
    except Exception:
        guard()
        raise
    guard()
    value = payload.get("result") if isinstance(payload, dict) else None
    return normalize_request_id(value)


def _prepare_subscription_intent(
    db: Session,
    org: Organization,
    *,
    subscription_id: str,
    reason: Literal["create", "recreate"],
    provider_guard: Callable[[], None] | None = None,
) -> None:
    org.bullhorn_event_subscription_id = subscription_id
    org.bullhorn_event_request_id = None
    current = config(org)
    current.pop(POLL_INTENT_KEY, None)
    current.pop(POISON_STATE_KEY, None)
    current[SUBSCRIPTION_STATE_KEY] = {
        "version": 1,
        "subscription_id": subscription_id,
        "state": "pending",
        "reason": reason,
        "environment_namespace": deployment_namespace(),
        "anchor_epoch": new_epoch(),
        "prepared_at": _now_iso(),
    }
    org.bullhorn_config = current
    db.add(org)
    if provider_guard is not None:
        provider_guard()
    db.commit()


def _mark_subscription_active(
    db: Session,
    org: Organization,
    *,
    subscription_id: str,
    last_completed_request_id: str,
    provider_guard: Callable[[], None] | None = None,
) -> None:
    state = subscription_state(org)
    if str(state.get("subscription_id") or "") != subscription_id:
        raise RuntimeError("Bullhorn subscription intent changed during creation")
    set_config_state(
        org,
        SUBSCRIPTION_STATE_KEY,
        {
            **state,
            "state": "active",
            "activated_at": _now_iso(),
            "last_completed_request_id": normalize_request_id(
                last_completed_request_id
            ),
        },
    )
    db.add(org)
    if provider_guard is not None:
        provider_guard()
    db.commit()


def _put_and_activate(
    db: Session,
    org: Organization,
    *,
    client: BullhornService,
    subscription_id: str,
    provider_guard: Callable[[], None] | None = None,
) -> None:
    validate_subscription_provenance(org)
    guard = provider_guard or (lambda: None)
    guard()
    try:
        payload = client.create_subscription(
            subscription_id=subscription_id,
            entity_names=list(SUBSCRIBED_ENTITIES),
        )
    except Exception:
        guard()
        raise
    guard()
    response_id = payload.get("subscriptionId") if isinstance(payload, dict) else None
    if response_id is not None and str(response_id) != subscription_id:
        raise RuntimeError("Bullhorn confirmed a different subscription id")
    raw_last = payload.get("lastRequestId") if isinstance(payload, dict) else None
    last_request_id = (
        normalize_request_id(raw_last)
        if raw_last is not None
        else _last_request_id(
            client,
            subscription_id,
            provider_guard=provider_guard,
        )
    )
    _mark_subscription_active(
        db,
        org,
        subscription_id=subscription_id,
        last_completed_request_id=last_request_id,
        provider_guard=provider_guard,
    )


def _resume_pending_subscription(
    db: Session,
    org: Organization,
    *,
    client: BullhornService,
    subscription_id: str,
    provider_guard: Callable[[], None] | None = None,
) -> None:
    validate_subscription_provenance(org)
    try:
        last_request_id = _last_request_id(
            client,
            subscription_id,
            provider_guard=provider_guard,
        )
    except BullhornApiError as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        _put_and_activate(
            db,
            org,
            client=client,
            subscription_id=subscription_id,
            provider_guard=provider_guard,
        )
        return
    _mark_subscription_active(
        db,
        org,
        subscription_id=subscription_id,
        last_completed_request_id=last_request_id,
        provider_guard=provider_guard,
    )


def ensure_subscription(
    db: Session,
    org: Organization,
    *,
    client: BullhornService,
    provider_guard: Callable[[], None] | None = None,
) -> tuple[str, bool]:
    """Return a durable subscription, recovering any ambiguous prior PUT."""
    stored = str(org.bullhorn_event_subscription_id or "").strip()
    state = subscription_state(org)
    if stored:
        state = validate_subscription_provenance(org)
        if (
            state.get("state") == "pending"
            and str(state.get("subscription_id") or "") == stored
        ):
            _resume_pending_subscription(
                db,
                org,
                client=client,
                subscription_id=stored,
                provider_guard=provider_guard,
            )
            return stored, True
        return stored, False

    subscription_id = deterministic_subscription_id(org)
    _prepare_subscription_intent(
        db,
        org,
        subscription_id=subscription_id,
        reason="create",
        provider_guard=provider_guard,
    )
    _put_and_activate(
        db,
        org,
        client=client,
        subscription_id=subscription_id,
        provider_guard=provider_guard,
    )
    return subscription_id, True


def recreate_subscription(
    db: Session,
    org: Organization,
    *,
    client: BullhornService,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    """Replace only Taali's known id, after durably clearing stale anchors."""
    subscription_id = str(org.bullhorn_event_subscription_id or "").strip()
    if not subscription_id:
        raise SubscriptionProvenanceError(
            "Bullhorn event subscription ownership is not proven"
        )
    state = validate_subscription_provenance(org)
    if (
        state.get("state") == "pending"
        and str(state.get("subscription_id") or "") == subscription_id
    ):
        _resume_pending_subscription(
            db,
            org,
            client=client,
            subscription_id=subscription_id,
            provider_guard=provider_guard,
        )
        return subscription_id
    _prepare_subscription_intent(
        db,
        org,
        subscription_id=subscription_id,
        reason="recreate",
        provider_guard=provider_guard,
    )
    _put_and_activate(
        db,
        org,
        client=client,
        subscription_id=subscription_id,
        provider_guard=provider_guard,
    )
    return subscription_id
