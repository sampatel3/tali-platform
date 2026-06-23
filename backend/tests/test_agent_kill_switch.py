"""Org-level + global agent kill switch (incident response).

The per-role pause (``role.agent_paused_at``) is the routine lever. These
tests cover the two coarser switches added for incident response:

- Org switch (``org.workspace_settings["agent_kill_switch"]``) halts every
  cycle for that org's roles; other orgs are unaffected.
- Global switch (``settings.AGENT_GLOBAL_KILL_SWITCH``) halts every cycle
  across all orgs/roles.

Coverage:
- Each cycle entry point (react/daily/cohort/manual) short-circuits with a
  kill-switch reason and creates no AgentRun / emits no decisions.
- The cohort tick skips BEFORE its Phase-1 auto-scoring + deterministic
  decision emitters ("agent off" means no agent-driven writes).
- A second org without the switch keeps running.
- Defaults (no switch set) leave behaviour unchanged.
- ``orchestrator.run_cycle`` aborts (defense-in-depth) before constructing a
  client / spending a token.
- The org settings API surfaces and sets the org switch.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import event

from app.agent_runtime import kill_switch, orchestrator
from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_run import AgentRun
from app.models.organization import Organization
from app.models.role import Role
from app.platform.config import settings
from app.tasks import agent_tasks
from tests.conftest import TestingSessionLocal


# SQLite BigInteger-PK workaround (same pattern as the other agent_runtime
# test modules). AgentDecision is registered globally in conftest.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_runs": 0, "agent_needs_input": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover — fired by SQLA
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentRun, "before_insert", _assign_big_pk)
event.listen(AgentNeedsInput, "before_insert", _assign_big_pk)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_org(db, *, kill_switch_on: bool = False, slug: str | None = None) -> Organization:
    org = Organization(
        name="KS Org",
        slug=slug or f"ks-org-{id(db)}-{_make_org._n}",
        workspace_settings={"agent_kill_switch": True} if kill_switch_on else None,
    )
    _make_org._n += 1
    db.add(org)
    db.commit()
    return org


_make_org._n = 0


def _make_role(db, org: Organization, *, name: str = "Backend") -> Role:
    role = Role(
        organization_id=org.id,
        name=name,
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,  # disables the monthly budget check
        job_spec_text="Requirements\n- 5+ years backend engineering\n",
    )
    db.add(role)
    db.commit()
    return role


def _complete_immediately_client() -> MagicMock:
    """Stub Anthropic client that immediately calls agent_run_complete."""
    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="tu_1",
                name="agent_run_complete",
                input={"summary": "Nothing actionable."},
            ),
        ],
        stop_reason="tool_use",
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )
    client = MagicMock()
    client.messages.create = MagicMock(return_value=response)
    return client


def _agent_runs_for(db, role: Role) -> list[AgentRun]:
    return db.query(AgentRun).filter(AgentRun.role_id == role.id).all()


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_halt_reason_for_org_reads_workspace_settings(db):
    on = _make_org(db, kill_switch_on=True)
    off = _make_org(db, kill_switch_on=False)

    assert kill_switch.halt_reason_for_org(on) == kill_switch.ORG_KILL_SWITCH_REASON
    assert kill_switch.halt_reason_for_org(off) is None
    assert kill_switch.org_kill_switch_active(on) is True
    assert kill_switch.org_kill_switch_active(off) is False
    assert kill_switch.org_kill_switch_active(None) is False


def test_global_switch_takes_precedence_over_org(db, monkeypatch):
    off = _make_org(db, kill_switch_on=False)
    monkeypatch.setattr(settings, "AGENT_GLOBAL_KILL_SWITCH", True)
    assert kill_switch.halt_reason_for_org(off) == kill_switch.GLOBAL_KILL_SWITCH_REASON
    assert kill_switch.global_kill_switch_active() is True


# ---------------------------------------------------------------------------
# Org switch — every cycle entry point skips
# ---------------------------------------------------------------------------


def test_daily_review_skips_when_org_kill_switch_on(db):
    org = _make_org(db, kill_switch_on=True)
    role = _make_role(db, org)

    client = _complete_immediately_client()
    with patch("app.platform.database.SessionLocal", new=TestingSessionLocal), patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        result = agent_tasks.agent_daily_review_role.run(role_id=int(role.id))

    assert result["status"] == "skipped"
    assert result["reason"] == kill_switch.ORG_KILL_SWITCH_REASON
    client.messages.create.assert_not_called()
    assert _agent_runs_for(db, role) == []


def test_react_to_event_skips_when_org_kill_switch_on(db):
    org = _make_org(db, kill_switch_on=True)
    role = _make_role(db, org)

    client = _complete_immediately_client()
    with patch("app.platform.database.SessionLocal", new=TestingSessionLocal), patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        result = agent_tasks.agent_react_to_event.run(role_id=int(role.id))

    assert result["status"] == "skipped"
    assert result["reason"] == kill_switch.ORG_KILL_SWITCH_REASON
    client.messages.create.assert_not_called()
    assert _agent_runs_for(db, role) == []


def test_manual_run_skips_when_org_kill_switch_on(db):
    """Manual runs bypass agentic-mode but must still respect the kill
    switch — incident response has to halt recruiter-triggered runs too."""
    org = _make_org(db, kill_switch_on=True)
    role = _make_role(db, org)

    client = _complete_immediately_client()
    with patch("app.platform.database.SessionLocal", new=TestingSessionLocal), patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        result = agent_tasks.agent_manual_run.run(role_id=int(role.id))

    assert result["status"] == "skipped"
    assert result["reason"] == kill_switch.ORG_KILL_SWITCH_REASON
    client.messages.create.assert_not_called()
    assert _agent_runs_for(db, role) == []


def test_cohort_tick_skips_before_phase1_when_org_kill_switch_on(db):
    """The cohort tick must skip BEFORE Phase-1 auto-scoring and the
    deterministic decision emitters — not just the LLM cycle."""
    org = _make_org(db, kill_switch_on=True)
    role = _make_role(db, org)

    client = _complete_immediately_client()
    with patch("app.platform.database.SessionLocal", new=TestingSessionLocal), patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ), patch.object(agent_tasks, "_auto_enqueue_scoring") as mock_auto_score:
        result = agent_tasks.agent_cohort_tick_role.run(role_id=int(role.id))

    assert result["status"] == "skipped"
    assert result["reason"] == kill_switch.ORG_KILL_SWITCH_REASON
    # Skipped before any agent-driven write path ran.
    mock_auto_score.assert_not_called()
    client.messages.create.assert_not_called()
    assert _agent_runs_for(db, role) == []
    assert db.query(AgentDecision).filter(AgentDecision.role_id == role.id).all() == []


def test_org_kill_switch_does_not_affect_other_orgs(db):
    """Org A's switch halts A's roles but leaves org B running."""
    org_a = _make_org(db, kill_switch_on=True)
    role_a = _make_role(db, org_a, name="A")
    org_b = _make_org(db, kill_switch_on=False)
    role_b = _make_role(db, org_b, name="B")

    client = _complete_immediately_client()
    with patch("app.platform.database.SessionLocal", new=TestingSessionLocal), patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        res_a = agent_tasks.agent_daily_review_role.run(role_id=int(role_a.id))
        res_b = agent_tasks.agent_daily_review_role.run(role_id=int(role_b.id))

    assert res_a["status"] == "skipped"
    assert res_a["reason"] == kill_switch.ORG_KILL_SWITCH_REASON
    # Org B has no switch — cycle runs normally to success.
    assert res_b["status"] == "ok"
    assert res_b["run_status"] == "succeeded"
    assert _agent_runs_for(db, role_a) == []
    assert len(_agent_runs_for(db, role_b)) == 1


# ---------------------------------------------------------------------------
# Global switch — halts every org
# ---------------------------------------------------------------------------


def test_global_kill_switch_skips_all_orgs(db, monkeypatch):
    org_a = _make_org(db, kill_switch_on=False)
    role_a = _make_role(db, org_a, name="A")
    org_b = _make_org(db, kill_switch_on=False)
    role_b = _make_role(db, org_b, name="B")

    monkeypatch.setattr(settings, "AGENT_GLOBAL_KILL_SWITCH", True)

    client = _complete_immediately_client()
    with patch("app.platform.database.SessionLocal", new=TestingSessionLocal), patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        res_a = agent_tasks.agent_daily_review_role.run(role_id=int(role_a.id))
        res_b = agent_tasks.agent_cohort_tick_role.run(role_id=int(role_b.id))

    assert res_a["status"] == "skipped"
    assert res_a["reason"] == kill_switch.GLOBAL_KILL_SWITCH_REASON
    assert res_b["status"] == "skipped"
    assert res_b["reason"] == kill_switch.GLOBAL_KILL_SWITCH_REASON
    client.messages.create.assert_not_called()
    assert _agent_runs_for(db, role_a) == []
    assert _agent_runs_for(db, role_b) == []


# ---------------------------------------------------------------------------
# Defaults — no switch leaves behaviour unchanged
# ---------------------------------------------------------------------------


def test_defaults_leave_cycles_running(db):
    org = _make_org(db, kill_switch_on=False)
    role = _make_role(db, org)

    client = _complete_immediately_client()
    with patch("app.platform.database.SessionLocal", new=TestingSessionLocal), patch(
        "app.agent_runtime.orchestrator.get_client_for_org", return_value=client
    ):
        result = agent_tasks.agent_daily_review_role.run(role_id=int(role.id))

    assert result["status"] == "ok"
    assert result["run_status"] == "succeeded"
    client.messages.create.assert_called()
    assert len(_agent_runs_for(db, role)) == 1


# ---------------------------------------------------------------------------
# run_cycle — defense-in-depth for direct callers (e.g. the manual CLI)
# ---------------------------------------------------------------------------


def test_run_cycle_aborts_on_org_kill_switch(db):
    org = _make_org(db, kill_switch_on=True)
    role = _make_role(db, org)

    with patch("app.agent_runtime.orchestrator.get_client_for_org") as mock_get_client:
        run = orchestrator.run_cycle(db, role=role, trigger="manual", application_id=None)
    db.commit()

    assert run.status == "aborted"
    assert run.error == kill_switch.ORG_KILL_SWITCH_REASON
    assert run.finished_at is not None
    assert run.decisions_emitted == 0
    # Aborted before a client was even constructed — no token spent.
    mock_get_client.assert_not_called()


def test_run_cycle_aborts_on_global_kill_switch(db, monkeypatch):
    org = _make_org(db, kill_switch_on=False)
    role = _make_role(db, org)
    monkeypatch.setattr(settings, "AGENT_GLOBAL_KILL_SWITCH", True)

    with patch("app.agent_runtime.orchestrator.get_client_for_org") as mock_get_client:
        run = orchestrator.run_cycle(db, role=role, trigger="cron", application_id=None)
    db.commit()

    assert run.status == "aborted"
    assert run.error == kill_switch.GLOBAL_KILL_SWITCH_REASON
    mock_get_client.assert_not_called()


def test_run_cycle_runs_normally_when_switch_off(db):
    org = _make_org(db, kill_switch_on=False)
    role = _make_role(db, org)

    client = _complete_immediately_client()
    with patch("app.agent_runtime.orchestrator.get_client_for_org", return_value=client):
        run = orchestrator.run_cycle(db, role=role, trigger="manual", application_id=None)
    db.commit()

    assert run.status == "succeeded"
    client.messages.create.assert_called()


# ---------------------------------------------------------------------------
# Org settings API surfaces + sets the switch
# ---------------------------------------------------------------------------


def test_org_settings_api_sets_and_surfaces_kill_switch(client):
    from tests.conftest import auth_headers

    headers, _ = auth_headers(client)

    # Default is off and surfaced in the response.
    me = client.get("/api/v1/organizations/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["workspace_settings"]["agent_kill_switch"] is False

    # Flip it on via the settings API.
    resp = client.patch(
        "/api/v1/organizations/me",
        json={"workspace_settings": {"agent_kill_switch": True}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["workspace_settings"]["agent_kill_switch"] is True

    # Persisted across a refetch.
    refetch = client.get("/api/v1/organizations/me", headers=headers)
    assert refetch.json()["workspace_settings"]["agent_kill_switch"] is True
