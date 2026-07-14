"""The role agent can force a fresh Workable sync to pull recent comments.

A recruiter asked the agent to "sync all comments from Workable" and it said it
"doesn't have a tool to trigger a sync". `sync_workable_comments` now reuses the
existing `kick_off_filtered_sync` (full mode, scoped to this role's job) so the
agent can refresh on demand.
"""
from __future__ import annotations

from unittest.mock import patch

from app.agent_chat import tools
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _setup(db):
    org = Organization(name="WS Org", slug=f"ws-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"ws-{id(db)}@x.test", hashed_password="x", full_name="Rec",
        organization_id=org.id, is_active=True, is_verified=True, is_superuser=False,
        role="owner",
    )
    db.add(user)
    db.flush()
    return org, user


def _role(db, org, *, shortcode="CE0F3B4176", source="workable", data=None):
    role = Role(organization_id=org.id, name="Azure Eng", source=source,
                workable_job_id=shortcode, workable_job_data=data)
    db.add(role)
    db.flush()
    return role


@patch("app.domains.workable_sync.routes.kick_off_filtered_sync")
def test_sync_tool_triggers_full_role_sync(mock_kick, db):
    org, user = _setup(db)
    role = _role(db, org, shortcode="CE0F3B4176")
    mock_kick.return_value = 4242

    res = tools.dispatch_tool("sync_workable_comments", {}, db=db, role=role, user=user)

    assert res["ok"] is True and res["status"] == "started" and res["run_id"] == 4242
    kw = mock_kick.call_args.kwargs
    assert kw["job_shortcodes"] == ["CE0F3B4176"]
    assert kw["mode"] == "full"
    assert kw["requested_by_user_id"] == user.id
    assert kw["org"].id == org.id


@patch("app.domains.workable_sync.routes.kick_off_filtered_sync")
def test_sync_tool_reports_already_running(mock_kick, db):
    org, user = _setup(db)
    role = _role(db, org)
    mock_kick.return_value = None  # a run is already in progress

    res = tools.dispatch_tool("sync_workable_comments", {}, db=db, role=role, user=user)
    assert res["ok"] is True and res["status"] == "already_running"


def test_sync_tool_rejects_non_workable_role(db):
    org, user = _setup(db)
    role = _role(db, org, shortcode=None, source="manual", data=None)

    res = tools.dispatch_tool("sync_workable_comments", {}, db=db, role=role, user=user)
    assert res["ok"] is False and res["reason"] == "not_workable"
