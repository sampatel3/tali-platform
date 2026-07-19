"""Shared durable state primitives for Bullhorn event lifecycle modules."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from ....models.organization import Organization
from ....platform.config import settings

SUBSCRIPTION_STATE_KEY = "event_subscription_lifecycle"
POLL_INTENT_KEY = "event_poll_intent"
POISON_STATE_KEY = "event_poison_checkpoint"


def new_epoch() -> str:
    return uuid.uuid4().hex


def config(org: Organization) -> dict:
    return dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}


def subscription_state(org: Organization) -> dict:
    state = config(org).get(SUBSCRIPTION_STATE_KEY)
    return dict(state) if isinstance(state, dict) else {}


def poll_intent(org: Organization) -> dict:
    state = config(org).get(POLL_INTENT_KEY)
    return dict(state) if isinstance(state, dict) else {}


def event_epoch(org: Organization) -> str:
    return str(subscription_state(org).get("anchor_epoch") or "")


def poll_intent_epoch(org: Organization) -> str:
    return str(poll_intent(org).get("epoch") or "")


def set_config_state(org: Organization, key: str, value: dict | None) -> None:
    current = config(org)
    if value is None:
        current.pop(key, None)
    else:
        current[key] = value
    org.bullhorn_config = current


def lock_expected_intent(
    db: Session,
    org: Organization,
    expected_epoch: str,
) -> dict | None:
    db.refresh(org, with_for_update=True)
    intent = poll_intent(org)
    if not expected_epoch or poll_intent_epoch(org) != expected_epoch:
        db.rollback()
        return None
    return intent


def durable_poll_intent(
    db: Session,
    org: Organization,
    *,
    validator: Callable[[Organization], object] | None = None,
) -> dict:
    db.refresh(org)
    if validator is not None:
        validator(org)
    intent = poll_intent(org)
    if not intent or poll_intent_epoch(org):
        return intent
    db.refresh(org, with_for_update=True)
    if validator is not None:
        validator(org)
    intent = poll_intent(org)
    if not intent:
        db.commit()
        return {}
    if not poll_intent_epoch(org):
        intent["epoch"] = new_epoch()
        set_config_state(org, POLL_INTENT_KEY, intent)
        db.add(org)
    db.commit()
    return poll_intent(org)


def deployment_namespace() -> str:
    raw = str(settings.DEPLOYMENT_ENV or "development").strip().lower()
    normalized = raw or "development"
    label = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:10] or "env"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{label}-{digest}"


def normalize_request_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise RuntimeError("Bullhorn returned an invalid event request id")
    normalized = str(value).strip()
    if (
        not normalized
        or len(normalized) > 64
        or not normalized.isascii()
        or not normalized.isdigit()
    ):
        raise RuntimeError("Bullhorn returned an invalid event request id")
    return str(int(normalized))
