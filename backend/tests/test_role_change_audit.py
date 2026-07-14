from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.organization import Organization
from app.models.role import Role
from app.models.role_change_event import RoleChangeEvent
from app.models.user import User
from app.services.role_change_audit import (
    MAX_AUDIT_CHANGES_BYTES,
    MAX_AUDIT_COLLECTION_ITEMS,
    MAX_AUDIT_QUERY_LIMIT,
    ROLE_CHANGE_ACTION_AGENT_ENABLED,
    add_role_change_event,
    build_role_change_diff,
    capture_role_change_snapshot,
    latest_role_change_actor,
    list_role_change_events,
    serialize_role_change_event,
)


class _RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)

    def commit(self) -> None:  # pragma: no cover - a call is the failure
        raise AssertionError("audit helper must not commit the caller transaction")

    def rollback(self) -> None:  # pragma: no cover - a call is the failure
        raise AssertionError("audit helper must not roll back the caller transaction")

    def flush(self) -> None:  # pragma: no cover - a call is the failure
        raise AssertionError("audit helper must not flush the caller transaction")


def _role(**overrides):
    values = {
        "id": 17,
        "organization_id": 9,
        "name": "Platform Engineer",
        "agentic_mode_enabled": False,
        "job_spec_text": None,
        "description": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _persisted_role(db, *, suffix: str) -> Role:
    org = Organization(name=f"Audit Org {suffix}", slug=f"audit-org-{suffix}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name=f"Role {suffix}")
    db.add(role)
    db.flush()
    return role


def test_model_preserves_role_id_without_cascading_foreign_key() -> None:
    role_id = RoleChangeEvent.__table__.c.role_id
    assert role_id.index is True
    assert not role_id.foreign_keys
    organization_id = RoleChangeEvent.__table__.c.organization_id
    assert organization_id.index is True
    assert not organization_id.foreign_keys

    actor_fk = next(iter(RoleChangeEvent.__table__.c.actor_user_id.foreign_keys))
    assert actor_fk.target_fullname == "users.id"
    assert actor_fk.ondelete == "SET NULL"
    assert RoleChangeEvent.__table__.c.created_at.index is True


def test_snapshot_has_an_explicit_allowlist_and_rejects_unknown_fields() -> None:
    snapshot = capture_role_change_snapshot(
        {
            "name": "Audited",
            "agentic_mode_enabled": False,
            "password": "must-never-enter-an-audit-row",
        }
    )

    assert snapshot["name"] == "Audited"
    assert "password" not in snapshot
    assert "must-never-enter-an-audit-row" not in json.dumps(snapshot)

    with pytest.raises(ValueError, match="Unsupported role audit field"):
        capture_role_change_snapshot({"password": "secret"}, fields=["password"])


def test_job_spec_and_description_changes_store_only_hashes_and_lengths() -> None:
    old_spec = "OLD_PRIVATE_SPEC:" + ("alpha " * 1_000)
    new_spec = "NEW_PRIVATE_SPEC:" + ("beta " * 1_000)
    before = capture_role_change_snapshot(
        {"job_spec_text": old_spec, "description": old_spec},
        fields=["description", "job_spec_text"],
    )
    after = capture_role_change_snapshot(
        {"job_spec_text": new_spec, "description": new_spec},
        fields=["description", "job_spec_text"],
    )

    changes = build_role_change_diff(
        before,
        after,
        fields=["description", "job_spec_text"],
    )
    spec_change = changes["job_spec_text"]
    assert spec_change["before"] == {
        "sha256": hashlib.sha256(old_spec.encode()).hexdigest(),
        "length": len(old_spec),
        "present": True,
    }
    assert spec_change["after"]["sha256"] == hashlib.sha256(
        new_spec.encode()
    ).hexdigest()
    assert spec_change["after"]["length"] == len(new_spec)

    encoded = json.dumps(changes, allow_nan=False)
    assert "OLD_PRIVATE_SPEC" not in encoded
    assert "NEW_PRIVATE_SPEC" not in encoded


def test_snapshot_is_json_safe_and_bounded() -> None:
    many_values = [f"action-{index}" for index in range(100)]
    snapshot = capture_role_change_snapshot(
        {
            "agent_action_allowlist": many_values,
            "agent_paused_at": datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc),
            "agent_paused_reason": "reason-" + ("x" * 10_000),
        },
        fields=[
            "agent_action_allowlist",
            "agent_paused_at",
            "agent_paused_reason",
        ],
    )

    allowlist = snapshot["agent_action_allowlist"]
    assert len(allowlist) == MAX_AUDIT_COLLECTION_ITEMS
    assert allowlist[-1] == {"__audit_omitted_items__": 76}
    assert snapshot["agent_paused_at"] == "2026-07-14T10:30:00+00:00"
    assert snapshot["agent_paused_reason"]["truncated"] is True
    json.dumps(snapshot, allow_nan=False)

    before = capture_role_change_snapshot(
        {"agent_action_allowlist": []}, fields=["agent_action_allowlist"]
    )
    changes = build_role_change_diff(
        before, snapshot, fields=["agent_action_allowlist"]
    )
    assert len(json.dumps(changes).encode()) <= MAX_AUDIT_CHANGES_BYTES


def test_add_event_only_adds_to_the_callers_transaction() -> None:
    role = _role()
    before = capture_role_change_snapshot(role, fields=["agentic_mode_enabled"])
    role.agentic_mode_enabled = True
    session = _RecordingSession()

    event = add_role_change_event(
        session,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        before=before,
        action=f"  {ROLE_CHANGE_ACTION_AGENT_ENABLED}  ",
        actor_user_id=23,
        from_version=4,
        to_version=5,
        reason="  Approved by hiring manager  ",
        request_id=" request-123 ",
        fields=["agentic_mode_enabled"],
    )

    assert session.added == [event]
    assert event is not None
    assert event.action == ROLE_CHANGE_ACTION_AGENT_ENABLED
    assert event.organization_id == 9
    assert event.role_id == 17
    assert event.actor_user_id == 23
    assert event.changes == {
        "agentic_mode_enabled": {"before": False, "after": True}
    }
    assert event.reason == "Approved by hiring manager"
    assert event.request_id == "request-123"


def test_add_event_skips_noop_and_rejects_invalid_versions() -> None:
    role = _role()
    before = capture_role_change_snapshot(role, fields=["agentic_mode_enabled"])
    session = _RecordingSession()

    event = add_role_change_event(
        session,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        before=before,
        action="role_updated",
        actor_user_id=None,
        from_version=1,
        to_version=2,
        fields=["agentic_mode_enabled"],
    )
    assert event is None
    assert session.added == []

    explicit_boundary = add_role_change_event(
        session,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        before=before,
        action="related_configuration_updated",
        actor_user_id=None,
        from_version=1,
        to_version=2,
        fields=["agentic_mode_enabled"],
        allow_empty_changes=True,
    )
    assert explicit_boundary is not None
    assert explicit_boundary.changes == {}

    role.agentic_mode_enabled = True
    with pytest.raises(ValueError, match="greater than"):
        add_role_change_event(
            session,  # type: ignore[arg-type]
            role=role,  # type: ignore[arg-type]
            before=before,
            action="role_updated",
            actor_user_id=None,
            from_version=2,
            to_version=2,
            fields=["agentic_mode_enabled"],
        )


def test_audit_write_rolls_back_with_the_role_transaction(db) -> None:
    role = _persisted_role(db, suffix="atomic")
    db.commit()
    role = db.get(Role, role.id)
    assert role is not None

    before = capture_role_change_snapshot(role, fields=["agentic_mode_enabled"])
    role.agentic_mode_enabled = True
    event = add_role_change_event(
        db,
        role=role,
        before=before,
        action=ROLE_CHANGE_ACTION_AGENT_ENABLED,
        actor_user_id=None,
        from_version=int(role.version),
        to_version=int(role.version) + 1,
        fields=["agentic_mode_enabled"],
    )
    assert event is not None
    db.flush()
    event_id = event.id
    db.rollback()

    assert db.get(RoleChangeEvent, event_id) is None
    restored_role = db.get(Role, role.id)
    assert restored_role is not None
    assert restored_role.agentic_mode_enabled is False


def test_query_is_tenant_scoped_ordered_paginated_and_serializable(db) -> None:
    role = _persisted_role(db, suffix="query-a")
    other_role = _persisted_role(db, suffix="query-b")

    before = capture_role_change_snapshot(role, fields=["agentic_mode_enabled"])
    role.agentic_mode_enabled = True
    first = add_role_change_event(
        db,
        role=role,
        before=before,
        action=ROLE_CHANGE_ACTION_AGENT_ENABLED,
        actor_user_id=None,
        from_version=1,
        to_version=2,
        fields=["agentic_mode_enabled"],
    )
    before = capture_role_change_snapshot(role, fields=["agentic_mode_enabled"])
    role.agentic_mode_enabled = False
    second = add_role_change_event(
        db,
        role=role,
        before=before,
        action="agent_disabled",
        actor_user_id=None,
        from_version=2,
        to_version=3,
        fields=["agentic_mode_enabled"],
    )
    other_before = capture_role_change_snapshot(
        other_role, fields=["agentic_mode_enabled"]
    )
    other_role.agentic_mode_enabled = True
    add_role_change_event(
        db,
        role=other_role,
        before=other_before,
        action=ROLE_CHANGE_ACTION_AGENT_ENABLED,
        actor_user_id=None,
        from_version=1,
        to_version=2,
        fields=["agentic_mode_enabled"],
    )
    db.commit()
    assert first is not None and second is not None

    page = list_role_change_events(
        db,
        organization_id=role.organization_id,
        role_id=role.id,
        limit=MAX_AUDIT_QUERY_LIMIT + 999,
    )
    assert [row.id for row in page] == [second.id, first.id]

    older = list_role_change_events(
        db,
        organization_id=role.organization_id,
        role_id=role.id,
        before_id=second.id,
    )
    assert [row.id for row in older] == [first.id]

    payload = serialize_role_change_event(second)
    assert payload["changes"]["agentic_mode_enabled"] == {
        "before": True,
        "after": False,
    }
    json.dumps(payload, allow_nan=False)


def test_latest_actor_is_tenant_scoped_and_json_safe(db) -> None:
    role = _persisted_role(db, suffix="actor")
    other_role = _persisted_role(db, suffix="actor-other")
    user = User(
        email="auditor@example.test",
        hashed_password="not-used",
        full_name="Audit User",
        organization_id=role.organization_id,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    db.flush()

    before = capture_role_change_snapshot(role, fields=["agentic_mode_enabled"])
    role.agentic_mode_enabled = True
    add_role_change_event(
        db,
        role=role,
        before=before,
        action=ROLE_CHANGE_ACTION_AGENT_ENABLED,
        actor_user_id=user.id,
        from_version=1,
        to_version=2,
        fields=["agentic_mode_enabled"],
    )
    other_before = capture_role_change_snapshot(
        other_role, fields=["agentic_mode_enabled"]
    )
    other_role.agentic_mode_enabled = True
    add_role_change_event(
        db,
        role=other_role,
        before=other_before,
        action=ROLE_CHANGE_ACTION_AGENT_ENABLED,
        actor_user_id=None,
        from_version=1,
        to_version=2,
        fields=["agentic_mode_enabled"],
    )
    db.commit()

    actor = latest_role_change_actor(
        db,
        organization_id=role.organization_id,
        role_id=role.id,
    )
    assert actor is not None
    assert actor["user_id"] == user.id
    assert actor["name"] == "Audit User"
    assert actor["email"] == "auditor@example.test"
    assert actor["changed_at"] is not None
    json.dumps(actor, allow_nan=False)

    assert (
        latest_role_change_actor(
            db,
            organization_id=other_role.organization_id,
            role_id=role.id,
        )
        is None
    )
