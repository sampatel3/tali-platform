"""Tests for the agent's daily-review cron path.

Covers:
- agent_daily_review_sweep enqueues one task per eligible role
- Sweep skips roles with agentic_mode_enabled=False
- Sweep skips soft-deleted roles
- agent_daily_review_role runs a cron-trigger cycle on an enabled role
- agent_daily_review_role short-circuits when role is paused
- The cron-trigger _initial_user_message variant is emitted (proxy:
  the AgentRun row has trigger="cron" and the orchestrator returns
  successfully with no application_id)

The sweep test patches the per-role task's .delay so we don't actually
fan out work in the test environment.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event

from app.models.agent_run import AgentRun
from app.models.organization import Organization
from app.models.role import Role


# Same SQLite BigInteger PK workaround used elsewhere — Postgres uses
# sequences in prod, SQLite needs help in tests.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_runs": 0, "agent_decisions": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentRun, "before_insert", _assign_big_pk)


def _make_org(db) -> Organization:
    org = Organization(name="Daily Review Org", slug=f"daily-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _make_role(
    db,
    org: Organization,
    *,
    name: str = "Backend",
    agentic: bool = True,
    paused: bool = False,
    deleted: bool = False,
) -> Role:
    from datetime import datetime, timezone

    role = Role(
        organization_id=org.id,
        name=name,
        source="manual",
        agentic_mode_enabled=agentic,
        monthly_usd_budget_cents=0,
    )
    if paused:
        role.agent_paused_at = datetime.now(timezone.utc)
        role.agent_paused_reason = "test"
    if deleted:
        role.deleted_at = datetime.now(timezone.utc)
    db.add(role)
    db.commit()
    return role


# ---------------------------------------------------------------------------
# Sweep — fan-out logic
# ---------------------------------------------------------------------------


def test_daily_review_sweep_enqueues_per_eligible_role(db):
    """Sweep should hit .delay() once per role with agentic mode on."""
    from app.tasks import agent_tasks

    org = _make_org(db)
    role_a = _make_role(db, org, name="A", agentic=True)
    role_b = _make_role(db, org, name="B", agentic=True)
    _make_role(db, org, name="Off", agentic=False)
    _make_role(db, org, name="Deleted", agentic=True, deleted=True)

    with patch.object(agent_tasks.agent_daily_review_role, "delay") as mock_delay:
        result = agent_tasks.agent_daily_review_sweep.run()

    assert result["status"] == "ok"
    enqueued_role_ids = {call.args[0] for call in mock_delay.call_args_list}
    assert enqueued_role_ids == {role_a.id, role_b.id}
    assert mock_delay.call_count == 2


def test_daily_review_sweep_includes_paused_roles_and_lets_per_role_skip(db):
    """Paused roles still get enqueued — the per-role task is the
    authoritative skip point. Keeps the sweep simple + idempotent
    against state changes between sweep and per-role run."""
    from app.tasks import agent_tasks

    org = _make_org(db)
    role_on = _make_role(db, org, name="On", agentic=True, paused=False)
    role_paused = _make_role(db, org, name="Paused", agentic=True, paused=True)

    with patch.object(agent_tasks.agent_daily_review_role, "delay") as mock_delay:
        agent_tasks.agent_daily_review_sweep.run()

    enqueued_role_ids = {call.args[0] for call in mock_delay.call_args_list}
    assert enqueued_role_ids == {role_on.id, role_paused.id}


def test_daily_review_sweep_returns_ok_with_zero_enqueued_when_no_roles(db):
    from app.tasks import agent_tasks

    _make_org(db)  # org exists, no roles

    with patch.object(agent_tasks.agent_daily_review_role, "delay") as mock_delay:
        result = agent_tasks.agent_daily_review_sweep.run()

    assert result["status"] == "ok"
    assert result["enqueued_count"] == 0
    assert mock_delay.call_count == 0


# ---------------------------------------------------------------------------
# Per-role task — runs the cron cycle
# ---------------------------------------------------------------------------


def _scripted_anthropic_client():
    """Stub Anthropic client that immediately calls agent_run_complete."""
    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="tu_1",
                name="agent_run_complete",
                input={"summary": "Daily review: nothing actionable today."},
            ),
        ],
        stop_reason="tool_use",
        usage=SimpleNamespace(
            input_tokens=42,
            output_tokens=12,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )
    client = MagicMock()
    client.messages.create = MagicMock(return_value=response)
    return client


def test_daily_review_role_runs_cron_cycle_on_enabled_role(db):
    """Per-role task should produce an AgentRun with trigger='cron'."""
    from app.tasks import agent_tasks

    org = _make_org(db)
    role = _make_role(db, org, agentic=True)

    # The Celery task opens its own SessionLocal — patch SessionLocal so
    # it returns a connection bound to the test engine + closes cleanly.
    from tests.conftest import TestingSessionLocal

    with patch(
        "app.platform.database.SessionLocal", new=TestingSessionLocal
    ), patch(
        "app.agent_runtime.orchestrator.get_client_for_org",
        return_value=_scripted_anthropic_client(),
    ):
        result = agent_tasks.agent_daily_review_role.run(role_id=int(role.id))

    assert result["status"] == "ok"
    assert result["role_id"] == role.id
    assert result["run_status"] == "succeeded"

    # Reload + confirm the AgentRun has trigger="cron"
    fresh = TestingSessionLocal()
    try:
        run_row = (
            fresh.query(AgentRun)
            .filter(AgentRun.id == result["agent_run_id"])
            .one()
        )
        assert run_row.trigger == "cron"
        assert run_row.status == "succeeded"
    finally:
        fresh.close()


def test_daily_review_role_skips_when_paused(db):
    from app.tasks import agent_tasks

    org = _make_org(db)
    role = _make_role(db, org, agentic=True, paused=True)

    from tests.conftest import TestingSessionLocal

    with patch(
        "app.platform.database.SessionLocal", new=TestingSessionLocal
    ):
        result = agent_tasks.agent_daily_review_role.run(role_id=int(role.id))

    assert result["status"] == "skipped"
    assert result["reason"] == "agent_paused"


def test_daily_review_role_skips_when_agentic_mode_off(db):
    from app.tasks import agent_tasks

    org = _make_org(db)
    role = _make_role(db, org, agentic=False)

    from tests.conftest import TestingSessionLocal

    with patch(
        "app.platform.database.SessionLocal", new=TestingSessionLocal
    ):
        result = agent_tasks.agent_daily_review_role.run(role_id=int(role.id))

    assert result["status"] == "skipped"
    assert result["reason"] == "agentic_mode_disabled"


def test_daily_review_role_returns_skip_when_role_missing(db):
    from app.tasks import agent_tasks

    from tests.conftest import TestingSessionLocal

    with patch(
        "app.platform.database.SessionLocal", new=TestingSessionLocal
    ):
        result = agent_tasks.agent_daily_review_role.run(role_id=999_999)

    assert result["status"] == "skipped"
    assert result["reason"] == "role_not_found"


# ---------------------------------------------------------------------------
# Initial user message routes to the daily-review variant
# ---------------------------------------------------------------------------


def test_initial_user_message_emits_daily_review_variant_for_cron_trigger():
    from app.agent_runtime.orchestrator import _initial_user_message

    msg = _initial_user_message(trigger="cron", application_id=None)
    assert "Daily review tick" in msg
    assert "proactive sweep" in msg.lower()
    # Should still mention search_applications + agent_run_complete + the
    # one-decision-per-cycle ceiling that constrains all cycle types.
    assert "search_applications" in msg
    assert "agent_run_complete" in msg
    assert "ONE queued decision" in msg or "one queued decision" in msg.lower()


def test_initial_user_message_does_not_use_daily_review_for_event_trigger():
    """An event cycle with no application_id is rare but possible — must
    still get the cycle-tick message, not the daily-review one."""
    from app.agent_runtime.orchestrator import _initial_user_message

    msg = _initial_user_message(trigger="event", application_id=None)
    assert "Daily review tick" not in msg
