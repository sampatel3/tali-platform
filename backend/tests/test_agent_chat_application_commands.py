"""Focused tests for role-scoped Agent Chat application commands."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent_chat import application_commands as commands
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _org(db, suffix: str, *, workable: bool = False) -> Organization:
    org = Organization(
        name=f"Application Commands {suffix}",
        slug=f"application-commands-{suffix}-{id(db)}",
        workable_connected=workable,
        workable_access_token="token" if workable else None,
        workable_subdomain="example" if workable else None,
        workable_config=(
            {
                "workable_writeback": True,
                "workable_actor_member_id": "member-1",
            }
            if workable
            else {}
        ),
    )
    db.add(org)
    db.flush()
    return org


def _user(db, org: Organization, suffix: str) -> User:
    user = User(
        email=f"application-commands-{suffix}-{id(db)}@example.test",
        hashed_password="x",
        full_name=f"Recruiter {suffix}",
        organization_id=int(org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    return user


def _role(db, org: Organization, suffix: str, *, enabled: bool = True) -> Role:
    role = Role(
        organization_id=int(org.id),
        name=f"Role {suffix}",
        description="A real job specification for command tests.",
        source="manual",
        agentic_mode_enabled=enabled,
        score_threshold=70,
    )
    db.add(role)
    db.flush()
    return role


def _application(
    db,
    org: Organization,
    role: Role,
    suffix: str,
    *,
    workable: bool = False,
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=int(org.id),
        email=f"candidate-{suffix}-{id(db)}@example.com",
        full_name=f"Candidate {suffix}",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        workable_candidate_id=f"workable-{suffix}" if workable else None,
    )
    db.add(app)
    db.flush()
    return app


def test_preview_and_create_application_reuse_canonical_action(db):
    org = _org(db, "create")
    user = _user(db, org, "create")
    role = _role(db, org, "create")
    candidate = Candidate(
        organization_id=int(org.id),
        email="known@example.com",
        full_name="Old Name",
        position="Old Position",
    )
    db.add(candidate)
    db.flush()

    preview = commands.preview_create_application(
        db,
        role,
        user,
        candidate_email="known@example.com",
        candidate_name="New Name",
        candidate_position="New Position",
        notes="Introduced by the recruiter.",
    )
    assert preview == {
        "type": "create_application_preview",
        "role_id": int(role.id),
        "candidate_email": "known@example.com",
        "candidate_name": "New Name",
        "candidate_position": "New Position",
        "candidate_exists": True,
        "candidate_id": int(candidate.id),
        "application_exists": False,
        "application_id": None,
        "would_update_candidate_profile": True,
        "can_create": True,
        "blocked_reason": None,
    }

    result = commands.create_application(
        db,
        role,
        user,
        candidate_email="known@example.com",
        candidate_name="New Name",
        candidate_position="New Position",
        notes="Introduced by the recruiter.",
    )
    assert result["type"] == "application_created"
    assert result["status"] == "created"
    assert result["role_id"] == int(role.id)
    assert result["candidate_id"] == int(candidate.id)

    app = db.get(CandidateApplication, result["application_id"])
    assert app is not None
    assert app.organization_id == org.id and app.role_id == role.id
    assert app.pipeline_stage_source == "recruiter"
    assert app.notes == "Introduced by the recruiter."
    assert candidate.full_name == "New Name"
    assert candidate.position == "New Position"


def test_create_preview_blocks_existing_application_and_missing_job_spec(db):
    org = _org(db, "blocked")
    user = _user(db, org, "blocked")
    role = _role(db, org, "blocked")
    app = _application(db, org, role, "blocked")

    existing = commands.inspect_application_by_email(
        db,
        role,
        user,
        candidate_email=app.candidate.email,
    )
    assert existing["can_create"] is False
    assert existing["blocked_reason"] == "application_exists"
    assert existing["application_id"] == int(app.id)

    with pytest.raises(commands.ApplicationCommandError) as exc_info:
        commands.create_application(
            db,
            role,
            user,
            candidate_email=app.candidate.email,
        )
    assert exc_info.value.code == "application_exists"

    no_spec = _role(db, org, "no-spec")
    no_spec.description = None
    preview = commands.preview_create_application(
        db,
        no_spec,
        user,
        candidate_email="new@example.com",
    )
    assert preview["can_create"] is False
    assert preview["blocked_reason"] == "job_spec_required"


def test_create_preview_does_not_disclose_candidate_from_another_org(db):
    org_a = _org(db, "tenant-a")
    org_b = _org(db, "tenant-b")
    user_a = _user(db, org_a, "tenant-a")
    role_a = _role(db, org_a, "tenant-a")
    db.add(
        Candidate(
            organization_id=int(org_b.id),
            email="same@example.com",
            full_name="Other Tenant Candidate",
        )
    )
    db.flush()

    preview = commands.preview_create_application(
        db,
        role_a,
        user_a,
        candidate_email="same@example.com",
    )
    assert preview["candidate_exists"] is False
    assert preview["candidate_id"] is None
    assert preview["can_create"] is True


def test_internal_note_uses_application_notes_service_and_is_role_scoped(db):
    org = _org(db, "internal-note")
    user = _user(db, org, "internal-note")
    role = _role(db, org, "internal-note")
    other_role = _role(db, org, "internal-note-other")
    app = _application(db, org, role, "internal-note")
    other_app = _application(db, org, other_role, "internal-note-other")

    result = commands.add_internal_note(
        db,
        role,
        user,
        application_id=int(app.id),
        note="Already interviewed; weigh the recruiter feedback next cycle.",
        for_agent=True,
    )
    assert result["type"] == "internal_note_added"
    assert result["application_id"] == int(app.id)
    assert result["for_agent"] is True

    event = db.get(CandidateApplicationEvent, result["event_id"])
    assert event is not None
    assert event.organization_id == org.id
    assert event.application_id == app.id
    assert event.actor_type == "recruiter" and event.actor_id == user.id
    assert event.event_metadata["for_agent"] is True
    assert "Already interviewed" in event.reason

    with pytest.raises(commands.ApplicationCommandError) as exc_info:
        commands.add_internal_note(
            db,
            role,
            user,
            application_id=int(other_app.id),
            note="Cross-role note",
        )
    assert exc_info.value.code == "application_not_found"


def test_standalone_ats_note_is_blocked_before_serialized_runner(db):
    org = _org(db, "workable-note", workable=True)
    user = _user(db, org, "workable-note")
    role = _role(db, org, "workable-note")
    app = _application(db, org, role, "workable-note", workable=True)

    preview = commands.preview_workable_note(
        db,
        role,
        user,
        application_id=int(app.id),
        body="  Please review the salary context before moving stages.  ",
    )
    assert preview["can_queue"] is False
    assert preview["expected_to_post"] is False
    assert preview["body_preview"] == (
        "Please review the salary context before moving stages."
    )
    assert "internal Taali note" in preview["blocked_reason"]
    assert "Workable or Bullhorn" in preview["blocked_reason"]

    with patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue:
        with pytest.raises(commands.ApplicationCommandError) as exc_info:
            commands.queue_workable_note(
                db,
                role,
                user,
                application_id=int(app.id),
                body="  Please review the salary context before moving stages.  ",
            )

    assert exc_info.value.code == "standalone_ats_notes_disabled"
    assert "internal Taali note" in exc_info.value.message
    enqueue.assert_not_called()


def test_retired_ats_note_boundary_still_enforces_role_scope(db):
    org = _org(db, "workable-guards", workable=True)
    user = _user(db, org, "workable-guards")
    role = _role(db, org, "workable-guards")
    other_role = _role(db, org, "workable-guards-other")
    unlinked = _application(db, org, role, "unlinked")
    other = _application(db, org, other_role, "other", workable=True)

    preview = commands.preview_workable_note(
        db,
        role,
        user,
        application_id=int(unlinked.id),
        body="A valid note",
    )
    assert preview["can_queue"] is False
    assert "Standalone ATS notes are disabled" in preview["blocked_reason"]

    with patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue:
        with pytest.raises(commands.ApplicationCommandError) as unlinked_error:
            commands.queue_workable_note(
                db,
                role,
                user,
                application_id=int(unlinked.id),
                body="A valid note",
            )
        assert unlinked_error.value.code == "standalone_ats_notes_disabled"

        with pytest.raises(commands.ApplicationCommandError) as role_error:
            commands.queue_workable_note(
                db,
                role,
                user,
                application_id=int(other.id),
                body="A valid note",
            )
        assert role_error.value.code == "application_not_found"
        enqueue.assert_not_called()


def test_manual_run_preview_and_enqueue_are_application_scoped(db):
    org = _org(db, "manual-run")
    user = _user(db, org, "manual-run")
    # A manual run intentionally works while always-on mode is disabled.
    role = _role(db, org, "manual-run", enabled=False)
    other_role = _role(db, org, "manual-run-other")
    app = _application(db, org, role, "manual-run")
    other_app = _application(db, org, other_role, "manual-run-other")

    preview = commands.preview_manual_run(
        db,
        role,
        user,
        application_id=int(app.id),
    )
    assert preview["scope"] == "application"
    assert preview["application_id"] == int(app.id)
    assert preview["agent_enabled"] is False
    assert preview["can_queue"] is True

    with patch(
        "app.tasks.agent_tasks.agent_manual_run.delay",
        return_value=SimpleNamespace(id="manual-task-1"),
    ) as delay:
        result = commands.enqueue_manual_run(
            db,
            role,
            user,
            application_id=int(app.id),
        )
        assert result["queued"] is True
        assert result["task_id"] == "manual-task-1"
        delay.assert_called_once_with(
            role_id=int(role.id), application_id=int(app.id)
        )

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        with pytest.raises(commands.ApplicationCommandError) as exc_info:
            commands.enqueue_manual_run(
                db,
                role,
                user,
                application_id=int(other_app.id),
            )
        assert exc_info.value.code == "application_not_found"
        delay.assert_not_called()


def test_manual_run_respects_role_pause_without_dispatch(db):
    org = _org(db, "manual-paused")
    user = _user(db, org, "manual-paused")
    role = _role(db, org, "manual-paused")
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "paused by recruiter"
    db.flush()

    preview = commands.preview_manual_run(db, role, user)
    assert preview["can_queue"] is False
    assert preview["blocked_reason"] == "paused by recruiter"

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        result = commands.enqueue_manual_run(db, role, user)
        assert result["queued"] is False
        assert result["status"] == "not_queued"
        assert "paused by recruiter" in result["detail"]
        delay.assert_not_called()


def test_manual_run_respects_workspace_pause_without_dispatch(db):
    org = _org(db, "manual-workspace-paused")
    user = _user(db, org, "manual-workspace-paused")
    role = _role(db, org, "manual-workspace-paused")
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    org.agent_workspace_paused_by_user_id = int(user.id)
    org.agent_workspace_paused_by_name = user.full_name
    db.flush()

    preview = commands.preview_manual_run(db, role, user)
    assert preview["agent_paused"] is True
    assert preview["pause_scope"] == "workspace"
    assert preview["can_queue"] is False
    assert preview["blocked_reason"] == "workspace agent is paused"

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        result = commands.enqueue_manual_run(db, role, user)
        assert result["queued"] is False
        assert result["status"] == "not_queued"
        assert result["pause_scope"] == "workspace"
        assert "workspace agent is paused" in result["detail"]
        delay.assert_not_called()


def test_all_application_commands_reject_cross_organization_context(db):
    org_a = _org(db, "scope-a")
    org_b = _org(db, "scope-b")
    role_a = _role(db, org_a, "scope-a")
    user_b = _user(db, org_b, "scope-b")

    with pytest.raises(commands.ApplicationCommandError) as exc_info:
        commands.preview_create_application(
            db,
            role_a,
            user_b,
            candidate_email="candidate@example.com",
        )
    assert exc_info.value.code == "scope_mismatch"
