"""Workspace/role pause truth for the chat-first agent sidebars."""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent_chat.service import list_agent_conversations
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _role(
    db,
    organization: Organization,
    *,
    name: str,
    enabled: bool,
    role_paused: bool = False,
) -> Role:
    role = Role(
        organization_id=int(organization.id),
        name=name,
        source="workable",
        score_threshold=70,
        agentic_mode_enabled=enabled,
        agent_paused_at=(datetime.now(timezone.utc) if role_paused else None),
        agent_paused_reason=("paused by recruiter" if role_paused else None),
        workable_job_data={"state": "published"},
    )
    db.add(role)
    db.flush()
    return role


def test_sidebar_exposes_workspace_overlay_without_erasing_role_intent(db):
    organization = Organization(name="Chat hold", slug="chat-hold")
    db.add(organization)
    db.flush()
    recruiter = User(
        email="sam-chat-hold@example.test",
        hashed_password="x",
        full_name="Sam Patel",
        organization_id=int(organization.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(recruiter)
    db.flush()

    desired_running = _role(
        db, organization, name="Desired running", enabled=True
    )
    locally_paused = _role(
        db,
        organization,
        name="Locally paused",
        enabled=True,
        role_paused=True,
    )
    off_role = _role(db, organization, name="Off role", enabled=False)

    paused_at = datetime.now(timezone.utc)
    organization.agent_workspace_paused_at = paused_at
    organization.agent_workspace_paused_reason = "workspace paused by recruiter"
    organization.agent_workspace_paused_by_user_id = int(recruiter.id)
    organization.agent_workspace_paused_by_name = recruiter.full_name
    organization.agent_workspace_control_version = 4
    db.flush()

    rows = list_agent_conversations(
        db,
        organization_id=int(organization.id),
        user=recruiter,
    )
    by_id = {row["role_id"]: row for row in rows}

    held = by_id[int(desired_running.id)]
    assert held["agent_enabled"] is True
    assert held["agent_running"] is False
    assert held["agent_paused"] is True
    assert held["agent_effective_paused"] is True
    assert held["agent_pause_scope"] == "workspace"
    assert held["agent_paused_reason"] == "workspace paused by recruiter"
    assert held["role_paused"] is False
    assert held["role_paused_at"] is None
    assert held["workspace_paused"] is True
    assert held["workspace_control_version"] == 4
    assert held["workspace_paused_by"] == {
        "user_id": int(recruiter.id),
        "name": "Sam Patel",
        "is_current_user": True,
        "changed_at": paused_at,
        "attribution": "verified",
        "source": "workspace_control",
    }

    held_local = by_id[int(locally_paused.id)]
    assert held_local["agent_pause_scope"] == "workspace"
    assert held_local["role_paused"] is True
    assert held_local["role_paused_reason"] == "paused by recruiter"
    assert held_local["agent_paused_reason"] == "workspace paused by recruiter"

    off = by_id[int(off_role.id)]
    assert off["agent_enabled"] is False
    assert off["agent_running"] is False
    assert off["agent_effective_paused"] is False
    assert off["agent_pause_scope"] is None
    assert off["workspace_paused"] is True

    # Clearing the workspace overlay reveals, rather than rewrites, the two
    # different local desired states.
    organization.agent_workspace_paused_at = None
    organization.agent_workspace_paused_reason = None
    organization.agent_workspace_paused_by_user_id = None
    organization.agent_workspace_paused_by_name = None
    organization.agent_workspace_control_version = 5
    db.flush()

    after = {
        row["role_id"]: row
        for row in list_agent_conversations(
            db,
            organization_id=int(organization.id),
            user=recruiter,
        )
    }
    assert after[int(desired_running.id)]["agent_running"] is True
    assert after[int(desired_running.id)]["agent_paused"] is False
    assert after[int(locally_paused.id)]["agent_running"] is False
    assert after[int(locally_paused.id)]["agent_pause_scope"] == "role"
    assert after[int(locally_paused.id)]["role_paused"] is True
