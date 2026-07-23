"""System agent holds recover without recruiter clicks; manual pauses do not."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.agent_runtime.budget_guard import MANUAL_PAUSE_REASON
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.role_change_event import RoleChangeEvent


def _paused_role(db, *, reason: str) -> Role:
    org = Organization(name=f"Recovery {reason}", slug=f"recovery-{abs(hash(reason))}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Recovered role",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        agent_paused_at=datetime.now(timezone.utc),
        agent_paused_reason=reason,
    )
    db.add(role)
    db.commit()
    return role


def test_recovery_sweep_resumes_healthy_system_hold(db):
    from app.tasks import agent_tasks
    from tests.conftest import TestingSessionLocal

    role = _paused_role(db, reason="platform credits depleted; top up to resume")
    initial_version = int(role.version or 1)
    with (
        patch("app.platform.database.SessionLocal", new=TestingSessionLocal),
        patch.object(agent_tasks.agent_cohort_tick_role, "delay") as dispatch,
    ):
        result = agent_tasks.agent_recovery_sweep.run()

    db.expire_all()
    current = db.query(Role).filter(Role.id == role.id).one()
    assert result["status"] == "ok"
    assert result["role_ids"] == [role.id]
    assert current.agent_paused_at is None
    assert current.agent_bootstrap_status == "starting"
    assert current.version == initial_version + 1
    dispatch.assert_called_once_with(
        role.id,
        activation=False,
        dispatch_role_version=initial_version + 1,
    )
    event = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .one()
    )
    assert event.action == "agent_resumed"
    assert event.actor_user_id is None
    assert event.from_version == initial_version
    assert event.to_version == initial_version + 1


def test_recovery_sweep_dispatches_related_role_to_dedicated_cycle(db):
    from app.tasks import agent_tasks
    from tests.conftest import TestingSessionLocal

    role = _paused_role(db, reason="related role provider hold cleared")
    role.source = "sister"
    role.role_kind = ROLE_KIND_SISTER
    db.commit()
    role_id = int(role.id)
    initial_version = int(role.version or 1)
    dispatched: list[tuple[int, dict]] = []

    def capture_dispatch(dispatched_role, **kwargs):
        dispatched.append((int(dispatched_role.id), kwargs))

    with (
        patch("app.platform.database.SessionLocal", new=TestingSessionLocal),
        patch(
            "app.services.role_agent_dispatch.dispatch_role_agent_cycle",
            side_effect=capture_dispatch,
        ) as dispatch,
        patch.object(agent_tasks.agent_cohort_tick_role, "delay") as generic_dispatch,
    ):
        result = agent_tasks.agent_recovery_sweep.run()

    assert result["status"] == "ok"
    assert result["role_ids"] == [role_id]
    generic_dispatch.assert_not_called()
    dispatch.assert_called_once()
    assert dispatched == [
        (
            role_id,
            {"activation": False, "role_version": initial_version + 1},
        )
    ]


def test_recovery_sweep_never_clears_manual_pause(db):
    from app.tasks import agent_tasks
    from tests.conftest import TestingSessionLocal

    role = _paused_role(db, reason=MANUAL_PAUSE_REASON)
    with (
        patch("app.platform.database.SessionLocal", new=TestingSessionLocal),
        patch.object(agent_tasks.agent_cohort_tick_role, "delay") as dispatch,
    ):
        result = agent_tasks.agent_recovery_sweep.run()

    db.expire_all()
    current = db.query(Role).filter(Role.id == role.id).one()
    assert result["status"] == "ok"
    assert result["checked"] == 0
    assert current.agent_paused_at is not None
    assert current.agent_paused_reason == MANUAL_PAUSE_REASON
    dispatch.assert_not_called()


def test_recovery_sweep_never_clears_legacy_manual_pause_label(db):
    from app.tasks import agent_tasks
    from tests.conftest import TestingSessionLocal

    role = _paused_role(db, reason="paused by you")
    with (
        patch("app.platform.database.SessionLocal", new=TestingSessionLocal),
        patch.object(agent_tasks.agent_cohort_tick_role, "delay") as dispatch,
    ):
        result = agent_tasks.agent_recovery_sweep.run()

    db.expire_all()
    current = db.query(Role).filter(Role.id == role.id).one()
    assert result["status"] == "ok"
    assert current.agent_paused_at is not None
    assert current.agent_paused_reason == "paused by you"
    dispatch.assert_not_called()


def test_recovery_sweep_repauses_when_broker_dispatch_fails(db):
    from app.tasks import agent_tasks
    from tests.conftest import TestingSessionLocal

    role = _paused_role(db, reason="agent bootstrap failed after retries: provider outage")
    initial_version = int(role.version or 1)
    with (
        patch("app.platform.database.SessionLocal", new=TestingSessionLocal),
        patch.object(
            agent_tasks.agent_cohort_tick_role,
            "delay",
            side_effect=RuntimeError("broker unavailable"),
        ),
    ):
        result = agent_tasks.agent_recovery_sweep.run()

    db.expire_all()
    current = db.query(Role).filter(Role.id == role.id).one()
    assert result["dispatch_failed"] == [role.id]
    assert current.agent_paused_at is not None
    assert current.agent_paused_reason == "agent recovery dispatch failed"
    assert current.agent_bootstrap_status == "failed"
    assert current.version == initial_version + 2
    events = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .order_by(RoleChangeEvent.id)
        .all()
    )
    assert [event.action for event in events] == ["agent_resumed", "agent_paused"]
    assert all(event.actor_user_id is None for event in events)
    assert [(event.from_version, event.to_version) for event in events] == [
        (initial_version, initial_version + 1),
        (initial_version + 1, initial_version + 2),
    ]


def test_recovery_dispatch_failure_preserves_newer_role_revision(db):
    from app.tasks import agent_tasks
    from tests.conftest import TestingSessionLocal

    role = _paused_role(db, reason="provider recovered")
    initial_version = int(role.version or 1)
    dispatched_version = initial_version + 1

    def newer_recruiter_write(*args, **kwargs):
        assert kwargs["dispatch_role_version"] == dispatched_version
        other = TestingSessionLocal()
        try:
            current = other.query(Role).filter(Role.id == role.id).one()
            current.name = "Newer recruiter edit"
            current.version = dispatched_version + 1
            other.commit()
        finally:
            other.close()
        raise RuntimeError("broker unavailable")

    with (
        patch("app.platform.database.SessionLocal", new=TestingSessionLocal),
        patch.object(
            agent_tasks.agent_cohort_tick_role,
            "delay",
            side_effect=newer_recruiter_write,
        ),
    ):
        result = agent_tasks.agent_recovery_sweep.run()

    db.expire_all()
    current = db.query(Role).filter(Role.id == role.id).one()
    assert result["dispatch_failed"] == [role.id]
    assert current.version == dispatched_version + 1
    assert current.name == "Newer recruiter edit"
    assert current.agent_paused_at is None
    assert current.agent_bootstrap_status == "starting"
    events = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .all()
    )
    assert len(events) == 1
    assert events[0].action == "agent_resumed"
    assert events[0].from_version == initial_version
    assert events[0].to_version == dispatched_version
