"""Transactional audit helpers for shared role mutations.

Typical use::

    before = capture_role_change_snapshot(role)
    role.agentic_mode_enabled = True
    add_role_change_event(
        db,
        role=role,
        before=before,
        action=ROLE_CHANGE_ACTION_AGENT_ENABLED,
        actor_user_id=current_user.id,
        from_version=expected_version,
        to_version=expected_version + 1,
    )
    db.commit()

The helper only calls ``Session.add``.  It never flushes, commits, rolls back,
or swallows errors, so the role update and its audit record have one atomic
outcome owned by the caller.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.role_change_event import RoleChangeEvent
from ..models.user import User


ROLE_CHANGE_ACTION_UPDATED = "role_updated"
ROLE_CHANGE_ACTION_DELETED = "role_deleted"
ROLE_CHANGE_ACTION_SOFT_DELETED = "role_soft_deleted"
ROLE_CHANGE_ACTION_RESTORED = "role_restored"
ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED = "job_spec_updated"
ROLE_CHANGE_ACTION_AGENT_ENABLED = "agent_enabled"
ROLE_CHANGE_ACTION_AGENT_DISABLED = "agent_disabled"
ROLE_CHANGE_ACTION_AGENT_PAUSED = "agent_paused"
ROLE_CHANGE_ACTION_AGENT_RESUMED = "agent_resumed"
ROLE_CHANGE_ACTION_STARRED = "role_starred"
ROLE_CHANGE_ACTION_UNSTARRED = "role_unstarred"

# This allowlist is the contract for the generic role audit.  Adding a field is
# an explicit privacy decision: arbitrary ORM/request attributes must never be
# copied into the audit JSON by accident.
AUDITED_ROLE_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "job_spec_text",
    "job_spec_filename",
    "job_spec_file_url",
    "job_spec_uploaded_at",
    "job_spec_manually_edited_at",
    "assessment_task_provisioning",
    "job_status",
    "screening_pack_template",
    "tech_interview_pack_template",
    "suppressed_org_criterion_ids",
    "auto_reject_threshold_mode",
    "workable_actor_member_id",
    "starred_for_auto_sync",
    "star_auto_managed",
    "agentic_mode_enabled",
    "agent_action_allowlist",
    "agent_token_budget_per_cycle",
    "agent_decision_budget_per_cycle",
    "monthly_usd_budget_cents",
    "score_threshold",
    "agent_paused_at",
    "agent_paused_reason",
    "auto_reject",
    "auto_reject_pre_screen",
    "auto_promote",
    "auto_send_assessment",
    "auto_resend_assessment",
    "auto_advance",
    "auto_skip_assessment",
    "deleted_at",
)
_AUDITED_ROLE_FIELD_SET = frozenset(AUDITED_ROLE_FIELDS)

# Job descriptions mirror ``job_spec_text`` on the current editor path.  Both
# therefore use content fingerprints; otherwise avoiding the job_spec_text key
# would still leak the complete spec through ``description``.  Signed document
# URLs and question-pack payloads are also fingerprint-only in this generic
# stream.  Dedicated revision tables can preserve full content with an
# appropriately narrower access policy later.
_TEXT_FINGERPRINT_FIELDS = frozenset(
    {"description", "job_spec_text", "job_spec_file_url"}
)
_JSON_FINGERPRINT_FIELDS = frozenset(
    {
        "assessment_task_provisioning",
        "screening_pack_template",
        "tech_interview_pack_template",
    }
)

MAX_AUDIT_STRING_CHARS = 512
MAX_AUDIT_COLLECTION_ITEMS = 25
MAX_AUDIT_NESTING_DEPTH = 4
MAX_AUDIT_CHANGE_BYTES = 1_024
MAX_AUDIT_CHANGES_BYTES = 65_536
MAX_AUDIT_REASON_CHARS = 2_000
MAX_AUDIT_REQUEST_ID_CHARS = 128
MAX_AUDIT_QUERY_LIMIT = 100


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _encoded_size(value: Any) -> int:
    return len(_canonical_json(value).encode("utf-8"))


def _text_fingerprint(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    text = str(value)
    return {
        "sha256": _sha256(text.encode("utf-8")),
        "length": len(text),
        "present": bool(text),
    }


def _json_fingerprint(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    # Role JSON columns already contain JSON-native values.  Hash their full
    # canonical representation rather than the bounded display form, otherwise
    # two large packs that differ only after the display truncation point could
    # appear unchanged.  The content is used only as hash input and is never
    # persisted in this generic audit record.
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            default=repr,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(value).encode("utf-8", errors="replace")
    return {
        "sha256": _sha256(encoded),
        "serialized_length": len(encoded),
        "present": True,
    }


def _opaque_fingerprint(value: Any) -> dict[str, Any]:
    """Represent an unsupported/deep value without persisting its contents."""

    representation = repr(value).encode("utf-8", errors="replace")
    return {
        "type": type(value).__name__,
        "sha256": _sha256(representation),
        "representation_length": len(representation),
        "content_omitted": True,
    }


def _bounded_string(value: str) -> str | dict[str, Any]:
    if len(value) <= MAX_AUDIT_STRING_CHARS:
        return value
    return {
        "preview": value[:MAX_AUDIT_STRING_CHARS],
        "sha256": _sha256(value.encode("utf-8")),
        "length": len(value),
        "truncated": True,
    }


def _json_safe(value: Any, *, _depth: int = 0) -> Any:
    """Return deterministic JSON-compatible data with bounded depth/width."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return _bounded_string(value)
    if isinstance(value, Enum):
        return _json_safe(value.value, _depth=_depth)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "sha256": _sha256(value),
            "length": len(value),
            "content_omitted": True,
        }
    if _depth >= MAX_AUDIT_NESTING_DEPTH:
        return _opaque_fingerprint(value)
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        safe: dict[str, Any] = {}
        room = MAX_AUDIT_COLLECTION_ITEMS
        if len(items) > room:
            room -= 1
        for key, item in items[:room]:
            safe[str(key)] = _json_safe(item, _depth=_depth + 1)
        omitted = len(items) - room
        if omitted > 0:
            safe["__audit_omitted_items__"] = omitted
        return safe
    if isinstance(value, set):
        value = sorted(value, key=repr)
    if isinstance(value, Sequence):
        items = list(value)
        room = MAX_AUDIT_COLLECTION_ITEMS
        if len(items) > room:
            room -= 1
        safe_items = [
            _json_safe(item, _depth=_depth + 1) for item in items[:room]
        ]
        omitted = len(items) - room
        if omitted > 0:
            safe_items.append({"__audit_omitted_items__": omitted})
        return safe_items
    return _opaque_fingerprint(value)


def _selected_fields(fields: Iterable[str] | None) -> tuple[str, ...]:
    if fields is None:
        return AUDITED_ROLE_FIELDS
    requested = set(fields)
    unknown = requested - _AUDITED_ROLE_FIELD_SET
    if unknown:
        raise ValueError(
            "Unsupported role audit field(s): " + ", ".join(sorted(unknown))
        )
    # Canonical ordering makes stored JSON and tests deterministic regardless
    # of the caller's set/list order.
    return tuple(field for field in AUDITED_ROLE_FIELDS if field in requested)


def _source_value(source: Role | Mapping[str, Any], field: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(field)
    return getattr(source, field, None)


def capture_role_change_snapshot(
    source: Role | Mapping[str, Any],
    *,
    fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Capture a safe, detached snapshot of explicitly auditable fields.

    The returned mapping is safe to retain across ORM mutation/flushes.  Job
    specifications and spec-like fields are fingerprinted at capture time, so
    raw text can never reach the persisted generic diff.
    """

    snapshot: dict[str, Any] = {}
    for field in _selected_fields(fields):
        value = _source_value(source, field)
        if field in _TEXT_FINGERPRINT_FIELDS:
            snapshot[field] = _text_fingerprint(value)
        elif field in _JSON_FINGERPRINT_FIELDS:
            snapshot[field] = _json_fingerprint(value)
        else:
            snapshot[field] = _json_safe(value)
    return snapshot


def _compacted_value(value: Any) -> dict[str, Any]:
    encoded = _canonical_json(value).encode("utf-8")
    return {
        "sha256": _sha256(encoded),
        "serialized_length": len(encoded),
        "content_omitted": True,
    }


def _bounded_change(before: Any, after: Any) -> dict[str, Any]:
    change = {"before": before, "after": after}
    if _encoded_size(change) <= MAX_AUDIT_CHANGE_BYTES:
        return change
    return {
        "before": _compacted_value(before),
        "after": _compacted_value(after),
        "compacted": True,
    }


def build_role_change_diff(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    fields: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build a deterministic before/after diff from captured snapshots."""

    changes: dict[str, dict[str, Any]] = {}
    for field in _selected_fields(fields):
        before_value = before.get(field)
        after_value = after.get(field)
        if before_value != after_value:
            changes[field] = _bounded_change(before_value, after_value)

    # The fixed allowlist plus per-field cap should already stay under this
    # ceiling.  Keep a final fail-safe so future explicit fields cannot make a
    # single audit row grow without bound.
    if _encoded_size(changes) > MAX_AUDIT_CHANGES_BYTES:
        changes = {
            field: {
                "before": _compacted_value(change["before"]),
                "after": _compacted_value(change["after"]),
                "compacted": True,
            }
            for field, change in changes.items()
        }
    return changes


def add_role_change_event(
    session: Session,
    *,
    role: Role,
    before: Mapping[str, Any],
    action: str,
    actor_user_id: int | None,
    from_version: int,
    to_version: int,
    reason: str | None = None,
    request_id: str | None = None,
    fields: Iterable[str] | None = None,
    allow_empty_changes: bool = False,
) -> RoleChangeEvent | None:
    """Add one audit event to the caller-owned transaction.

    A no-op over the selected audited fields returns ``None`` and adds no row.
    Version/action errors raise rather than silently creating misleading audit
    history.  This function intentionally never commits or rolls back.
    """

    normalized_action = str(action or "").strip()
    if not normalized_action or len(normalized_action) > 64:
        raise ValueError("Role audit action must contain 1 to 64 characters")
    prior_version = int(from_version)
    next_version = int(to_version)
    if prior_version < 0 or next_version <= prior_version:
        raise ValueError("Role audit to_version must be greater than from_version")

    role_id = int(getattr(role, "id", 0) or 0)
    organization_id = int(getattr(role, "organization_id", 0) or 0)
    if role_id <= 0 or organization_id <= 0:
        raise ValueError("Role audit events require a persisted tenant-scoped role")

    selected = _selected_fields(fields)
    after = capture_role_change_snapshot(role, fields=selected)
    changes = build_role_change_diff(before, after, fields=selected)
    if not changes and not allow_empty_changes:
        return None

    normalized_reason = str(reason or "").strip() or None
    if normalized_reason is not None:
        normalized_reason = normalized_reason[:MAX_AUDIT_REASON_CHARS]
    normalized_request_id = str(request_id or "").strip() or None
    if normalized_request_id is not None:
        normalized_request_id = normalized_request_id[:MAX_AUDIT_REQUEST_ID_CHARS]

    event = RoleChangeEvent(
        organization_id=organization_id,
        role_id=role_id,
        actor_user_id=(int(actor_user_id) if actor_user_id is not None else None),
        action=normalized_action,
        from_version=prior_version,
        to_version=next_version,
        changes=changes,
        reason=normalized_reason,
        request_id=normalized_request_id,
    )
    session.add(event)
    return event


def list_role_change_events(
    session: Session,
    *,
    organization_id: int,
    role_id: int,
    limit: int = 50,
    before_id: int | None = None,
) -> list[RoleChangeEvent]:
    """Return a tenant-scoped, newest-first, bounded audit page."""

    bounded_limit = max(1, min(int(limit), MAX_AUDIT_QUERY_LIMIT))
    query = session.query(RoleChangeEvent).filter(
        RoleChangeEvent.organization_id == int(organization_id),
        RoleChangeEvent.role_id == int(role_id),
    )
    if before_id is not None:
        query = query.filter(RoleChangeEvent.id < int(before_id))
    return query.order_by(RoleChangeEvent.id.desc()).limit(bounded_limit).all()


def latest_role_change_actor(
    session: Session,
    organization_id: int,
    role_id: int,
) -> dict[str, Any] | None:
    """Describe the actor on the latest tenant-scoped role change.

    The outer join keeps the conflict response useful after an actor account is
    deleted: ``user_id``, ``name`` and ``email`` become null while
    ``changed_at`` still identifies when the conflicting change occurred.
    """

    row = (
        session.query(RoleChangeEvent, User)
        .outerjoin(
            User,
            and_(
                RoleChangeEvent.actor_user_id == User.id,
                User.organization_id == RoleChangeEvent.organization_id,
            ),
        )
        .filter(
            RoleChangeEvent.organization_id == int(organization_id),
            RoleChangeEvent.role_id == int(role_id),
        )
        .order_by(RoleChangeEvent.id.desc())
        .first()
    )
    if row is None:
        return None
    event, user = row
    return {
        "user_id": int(user.id) if user is not None else None,
        "name": (
            str(user.full_name)[:200]
            if user is not None and user.full_name is not None
            else None
        ),
        "email": (
            str(user.email)[:320]
            if user is not None and user.email is not None
            else None
        ),
        "changed_at": (
            event.created_at.isoformat() if event.created_at is not None else None
        ),
    }


def serialize_role_change_event(event: RoleChangeEvent) -> dict[str, Any]:
    """Return a JSON-safe representation suitable for a later API route."""

    return {
        "id": int(event.id),
        "organization_id": int(event.organization_id),
        "role_id": int(event.role_id),
        "actor_user_id": (
            int(event.actor_user_id) if event.actor_user_id is not None else None
        ),
        "action": event.action,
        "from_version": int(event.from_version),
        "to_version": int(event.to_version),
        "changes": _json_safe(event.changes or {}),
        "reason": event.reason,
        "request_id": event.request_id,
        "created_at": (
            event.created_at.isoformat() if event.created_at is not None else None
        ),
    }
