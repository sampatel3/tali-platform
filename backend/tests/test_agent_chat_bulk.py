"""Bulk agent messaging + the widened sidebar (all live roles).

- list_agent_conversations now includes LIVE roles (Workable published) even
  with the agent off, so the recruiter can activate from Home.
- bulk_agent_message fans one message out to each selected role's own thread.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.agent_chat.service import list_agent_conversations
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from tests.conftest import TestingSessionLocal, auth_headers


def _org(db, name="Bulk Org") -> Organization:
    org = Organization(name=name, slug=f"bulk-{id(db)}-{name[:4]}")
    db.add(org)
    db.flush()
    return org


def _user(db, org) -> User:
    u = User(
        email=f"bulk-{id(db)}-{org.id}@x.test", hashed_password="x", full_name="Rec",
        organization_id=org.id, is_active=True, is_verified=True, is_superuser=False,
    )
    db.add(u)
    db.flush()
    return u


def _role(db, org, *, name="Role", agentic=False, live=False) -> Role:
    role = Role(
        organization_id=org.id, name=name, source="workable", score_threshold=70,
        agentic_mode_enabled=agentic,
        workable_job_data={"state": "published"} if live else {"state": "draft"},
    )
    db.add(role)
    db.flush()
    return role


# --- widened sidebar --------------------------------------------------------
def test_sidebar_includes_live_role_with_agent_off(db):
    org = _org(db)
    user = _user(db, org)
    live_off = _role(db, org, name="Live Off", agentic=False, live=True)

    items = list_agent_conversations(db, organization_id=org.id, user=user)
    ids = {it["role_id"] for it in items}
    assert live_off.id in ids
    row = next(it for it in items if it["role_id"] == live_off.id)
    assert row["agent_enabled"] is False  # shown so you can activate it from Home


def test_sidebar_excludes_nonlive_off_role_without_thread(db):
    org = _org(db)
    user = _user(db, org)
    draft_off = _role(db, org, name="Draft Off", agentic=False, live=False)
    agent_on = _role(db, org, name="Agent On", agentic=True, live=False)

    items = list_agent_conversations(db, organization_id=org.id, user=user)
    ids = {it["role_id"] for it in items}
    assert draft_off.id not in ids   # not live, agent off, no thread → hidden
    assert agent_on.id in ids        # agent-on still shows even if not live


# --- bulk fan-out task ------------------------------------------------------
@patch("app.platform.database.SessionLocal", TestingSessionLocal)
@patch("app.agent_chat.engine.run_agent_turn")
def test_bulk_task_runs_a_turn_per_role(mock_turn, db):
    mock_turn.return_value = []
    org = _org(db)
    user = _user(db, org)
    r1 = _role(db, org, name="R1", agentic=True)
    r2 = _role(db, org, name="R2", agentic=True)
    db.commit()  # task uses its own session — must see committed rows

    from app.tasks.agent_chat_tasks import bulk_agent_message

    res = bulk_agent_message(org.id, user.id, [r1.id, r2.id], "Salary expectation is now AED 30k")
    assert res["status"] == "done"
    assert res["ok"] == 2 and res["failed"] == 0
    assert mock_turn.call_count == 2
    # each call ran in that role's own conversation
    msgs = {c.kwargs["user_message"] for c in mock_turn.call_args_list}
    assert msgs == {"Salary expectation is now AED 30k"}


@patch("app.platform.database.SessionLocal", TestingSessionLocal)
@patch("app.agent_chat.engine.run_agent_turn")
def test_bulk_task_isolates_a_failing_role(mock_turn, db):
    org = _org(db)
    user = _user(db, org)
    r1 = _role(db, org, name="R1", agentic=True)
    r2 = _role(db, org, name="R2", agentic=True)
    db.commit()

    def _turn(**kwargs):
        if kwargs["role"].id == r1.id:
            raise RuntimeError("boom")
        return []

    mock_turn.side_effect = _turn
    from app.tasks.agent_chat_tasks import bulk_agent_message

    res = bulk_agent_message(org.id, user.id, [r1.id, r2.id], "hello")
    assert res["ok"] == 1 and res["failed"] == 1  # r2 still processed


# --- endpoint: ownership filter + enqueue -----------------------------------
def test_bulk_endpoint_enqueues_owned_only(client, db):
    headers, email = auth_headers(client, organization_name="BulkOrgA")
    org_id = int(db.query(User).filter(User.email == email).first().organization_id)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    r1 = _role(db, org, name="R1", agentic=True)
    r2 = _role(db, org, name="R2", agentic=True)
    db.commit()

    # a role in a different org must be dropped
    _h2, email2 = auth_headers(client, organization_name="BulkOrgB")
    org2_id = int(db.query(User).filter(User.email == email2).first().organization_id)
    org2 = db.query(Organization).filter(Organization.id == org2_id).first()
    r_other = _role(db, org2, name="Other", agentic=True)
    db.commit()

    with patch("app.tasks.agent_chat_tasks.bulk_agent_message.delay") as mock_delay:
        resp = client.post(
            "/api/v1/agent-chat/bulk-message",
            headers=headers,
            json={"role_ids": [r1.id, r2.id, r_other.id], "message": "Salary is now AED 30k"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["requested"] == 3
    assert body["accepted"] == 2
    assert r_other.id in body["skipped"]
    assert mock_delay.called
    enqueued_ids = mock_delay.call_args.args[2]
    assert sorted(enqueued_ids) == sorted([r1.id, r2.id])
