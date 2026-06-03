"""Agent control tools — activate / pause the role agent + adjust settings.

Covers controls.py via the public dispatch_tool path. The immediate cycle kick
is patched out so the tests don't touch Celery.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.agent_chat import controls as _controls
from app.agent_chat import tools
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _org(db) -> Organization:
    org = Organization(name="Ctl Org", slug=f"ctl-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _user(db, org) -> User:
    u = User(
        email=f"ctl-{id(db)}@x.test", hashed_password="x", full_name="Rec",
        organization_id=org.id, is_active=True, is_verified=True, is_superuser=False,
    )
    db.add(u)
    db.flush()
    return u


def _role(db, org, *, agentic=True, budget=5000) -> Role:
    role = Role(
        organization_id=org.id, name="Role", source="manual", score_threshold=70,
        agentic_mode_enabled=agentic, monthly_usd_budget_cents=budget,
    )
    db.add(role)
    db.flush()
    return role


def _run(db, role, user, name, args):
    return tools.dispatch_tool(name, args, db=db, role=role, user=user)


@patch.object(_controls, "_kick_cycle")
def test_activate_resumes_paused_role(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=False, budget=5000)
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "paused by recruiter"
    db.flush()

    res = _run(db, role, user, "set_agent_state", {"action": "activate"})
    assert res["ok"] and res["action"] == "activated"
    assert role.agentic_mode_enabled is True
    assert role.agent_paused_at is None and role.agent_paused_reason is None
    assert kick.called  # activation/resume kicks an immediate cycle


@patch.object(_controls, "_kick_cycle")
def test_activate_without_budget_asks_for_one(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=False, budget=None)

    res = _run(db, role, user, "set_agent_state", {"action": "activate"})
    assert res["ok"] is False and res["reason"] == "needs_budget"
    assert role.agentic_mode_enabled is False  # unchanged
    assert not kick.called


@patch.object(_controls, "_kick_cycle")
def test_pause_sets_paused_state(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=True)

    res = _run(db, role, user, "set_agent_state", {"action": "pause"})
    assert res["ok"] and res["action"] == "paused"
    assert role.agent_paused_at is not None
    assert role.agent_paused_reason == "paused by recruiter"


@patch.object(_controls, "_kick_cycle")
def test_adjust_agent_settings_updates_fields(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=True)

    res = _run(
        db, role, user, "adjust_agent_settings",
        {"monthly_budget_cents": 7500, "auto_reject": True},
    )
    assert res["ok"]
    assert role.monthly_usd_budget_cents == 7500
    assert role.auto_reject is True
    assert "monthly_budget" in res["changed"] and "auto_reject" in res["changed"]
