"""Bulk agent messaging + the widened sidebar (all live roles).

- list_agent_conversations now includes LIVE roles (Workable published) even
  with the agent off, so the recruiter can activate from Home.
- bulk_agent_message fans one message out to each selected role's own thread.
"""
from __future__ import annotations

from unittest.mock import patch

from app.agent_chat.service import list_agent_conversations
from app.models.agent_conversation import AgentConversation
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


def test_bulk_send_fences_post_commit_role_edits_per_durable_turn(db):
    org = _org(db, name="Bulk race")
    user = _user(db, org)
    r1 = _role(db, org, name="R1", agentic=True)
    r2 = _role(db, org, name="R2", agentic=True)
    db.commit()
    role_ids = [int(r1.id), int(r2.id)]
    accepted_versions = {
        str(int(r1.id)): int(r1.version or 1),
        str(int(r2.id)): int(r2.version or 1),
    }

    from app.domains.agent_chat.route_support import BulkMessageRequest
    from app.domains.agent_chat.routes import bulk_message

    real_commit = db.commit
    raced = False

    def _commit_then_edit_roles():
        nonlocal raced
        real_commit()
        if raced:
            return
        raced = True
        with TestingSessionLocal() as concurrent:
            for rid in role_ids:
                current = concurrent.get(Role, rid)
                current.version = accepted_versions[str(rid)] + 1
            concurrent.commit()

    with patch.object(db, "commit", side_effect=_commit_then_edit_roles), patch(
        "app.tasks.agent_chat_tasks.bulk_agent_message.delay"
    ) as delay:
        result = bulk_message(
            BulkMessageRequest(role_ids=role_ids, message="Review each shortlist"),
            db=db,
            current_user=user,
        )

    assert result["accepted"] == 2
    assert delay.call_args.args[4] == accepted_versions
    db.expire_all()
    conversations = (
        db.query(AgentConversation)
        .filter(AgentConversation.role_id.in_(role_ids))
        .all()
    )
    assert {
        str(int(row.role_id)): int(row.turn_accepted_role_version)
        for row in conversations
    } == accepted_versions
    assert {
        str(rid): int(db.get(Role, rid).version)
        for rid in role_ids
    } == {
        key: version + 1 for key, version in accepted_versions.items()
    }


# --- agent-first grouping ---------------------------------------------------
def test_sidebar_groups_agent_first(db):
    """Each role lands in the first matching section: on/paused, then previously-on
    (agent_last_run_at), then starred, then other active (live)."""
    from datetime import datetime, timezone

    org = _org(db)
    user = _user(db, org)
    r_on = _role(db, org, name="On", agentic=True, live=False)
    r_prev = _role(db, org, name="Prev", agentic=False, live=False)
    r_prev.agent_last_run_at = datetime.now(timezone.utc)
    r_star = _role(db, org, name="Star", agentic=False, live=False)
    r_star.starred_for_auto_sync = True
    r_active = _role(db, org, name="Active", agentic=False, live=True)
    db.flush()

    items = list_agent_conversations(db, organization_id=org.id, user=user)
    by_role = {it["role_id"]: it for it in items}
    assert by_role[r_on.id]["group"] == "on_paused"
    assert by_role[r_prev.id]["group"] == "previously_on"
    assert by_role[r_star.id]["group"] == "starred"
    assert by_role[r_active.id]["group"] == "active"

    # Sections come out in agent-first order.
    order = [it["group"] for it in items]
    assert order.index("on_paused") < order.index("previously_on") < order.index("starred") < order.index("active")
