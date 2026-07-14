"""System agent holds recover without recruiter clicks; manual pauses do not."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.agent_runtime.budget_guard import MANUAL_PAUSE_REASON
from app.models.organization import Organization
from app.models.role import Role


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
    dispatch.assert_called_once_with(role.id, activation=False)


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
