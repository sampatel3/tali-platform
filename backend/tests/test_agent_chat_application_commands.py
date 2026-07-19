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
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.platform.config import settings


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


def test_workable_note_previews_then_uses_only_serialized_runner(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
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
    assert preview["can_queue"] is True
    assert preview["expected_to_post"] is True
    assert all(preview["delivery_checks"].values())
    assert preview["body_preview"] == (
        "Please review the salary context before moving stages."
    )

    with (
        patch(
            "app.services.workable_op_runner.enqueue_workable_op",
            return_value=77,
        ) as enqueue,
        patch("app.actions.post_workable_note.run") as inline_action,
    ):
        result = commands.queue_workable_note(
            db,
            role,
            user,
            application_id=int(app.id),
            body="  Please review the salary context before moving stages.  ",
        )

    assert result == {
        "type": "workable_note_queued",
        "status": "queued",
        "role_id": int(role.id),
        "application_id": int(app.id),
        "job_run_id": 77,
    }
    enqueue.assert_called_once_with(
        organization_id=int(org.id),
        op_type="post_note",
        payload={
            "application_id": int(app.id),
            "user_id": int(user.id),
            "body": "Please review the salary context before moving stages.",
            "provider": "workable",
            "provider_target_id": str(app.workable_candidate_id),
            "candidate_provider_id": str(app.workable_candidate_id),
            "provider_actor_member_id": "member-1",
            "provider_job_order_id": None,
            "provider_note_action": None,
            "actor_type": "recruiter",
            "actor_id": int(user.id),
        },
        scope_id=int(app.id),
        dispatch_key=None,
    )
    inline_action.assert_not_called()


def test_workable_note_refuses_unlinked_and_cross_role_applications(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
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
    assert preview["delivery_checks"]["application_linked"] is False

    with patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue:
        with pytest.raises(commands.ApplicationCommandError) as unlinked_error:
            commands.queue_workable_note(
                db,
                role,
                user,
                application_id=int(unlinked.id),
                body="A valid note",
            )
        assert unlinked_error.value.code == "workable_not_linked"

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
    role = _role(db, org, "manual-run")
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
    assert preview["agent_enabled"] is True
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


def test_manual_run_refuses_disabled_role_without_dispatch(db):
    org = _org(db, "manual-disabled")
    user = _user(db, org, "manual-disabled")
    role = _role(db, org, "manual-disabled", enabled=False)
    app = _application(db, org, role, "manual-disabled")

    preview = commands.preview_manual_run(
        db,
        role,
        user,
        application_id=int(app.id),
    )
    assert preview["scope"] == "application"
    assert preview["application_id"] is None
    assert preview["candidate"] is None
    assert preview["agent_enabled"] is False
    assert preview["can_queue"] is False
    assert preview["blocked_reason"] == "agent is not enabled for this role"

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        result = commands.enqueue_manual_run(
            db,
            role,
            user,
            application_id=int(app.id),
        )

    assert result == {
        "type": "manual_agent_run",
        "status": "not_queued",
        "queued": False,
        "role_id": int(role.id),
        "application_id": None,
        "detail": "agent is not enabled for this role",
    }
    delay.assert_not_called()


def test_related_role_manual_run_uses_source_roster_and_conceals_other_apps(db):
    org = _org(db, "related-manual-run")
    foreign_org = _org(db, "related-manual-run-foreign")
    user = _user(db, org, "related-manual-run")
    source_role = _role(db, org, "related-manual-run-source")
    unrelated_role = _role(db, org, "related-manual-run-unrelated")
    related_role = _role(db, org, "related-manual-run-related")
    related_role.source = "sister"
    related_role.role_kind = ROLE_KIND_SISTER
    related_role.ats_owner_role_id = int(source_role.id)

    visible_app = _application(db, org, source_role, "related-manual-run-visible")
    unrelated_app = _application(
        db,
        org,
        unrelated_role,
        "related-manual-run-unrelated",
    )
    foreign_role = _role(db, foreign_org, "related-manual-run-foreign")
    foreign_app = _application(
        db,
        foreign_org,
        foreign_role,
        "related-manual-run-foreign",
    )
    corrupt_candidate_app = _application(
        db,
        org,
        source_role,
        "related-manual-run-corrupt-candidate",
    )
    corrupt_candidate = db.get(Candidate, int(corrupt_candidate_app.candidate_id))
    assert corrupt_candidate is not None
    corrupt_candidate.organization_id = int(foreign_org.id)
    deleted_candidate_app = _application(
        db,
        org,
        source_role,
        "related-manual-run-deleted-candidate",
    )
    deleted_candidate = db.get(Candidate, int(deleted_candidate_app.candidate_id))
    assert deleted_candidate is not None
    deleted_candidate.deleted_at = datetime.now(timezone.utc)
    closed_app = _application(
        db,
        org,
        source_role,
        "related-manual-run-closed",
    )
    closed_app.application_outcome = "withdrawn"
    disqualified_app = _application(
        db,
        org,
        source_role,
        "related-manual-run-disqualified",
    )
    disqualified_app.workable_disqualified = True

    for index, application in enumerate(
        (
            visible_app,
            corrupt_candidate_app,
            deleted_candidate_app,
            closed_app,
            disqualified_app,
        )
    ):
        db.add(
            SisterRoleEvaluation(
                organization_id=int(org.id),
                role_id=int(related_role.id),
                source_application_id=int(application.id),
                status="pending",
                spec_fingerprint=f"related-manual-run-{index}",
            )
        )
    db.flush()

    preview = commands.preview_manual_run(
        db,
        related_role,
        user,
        application_id=int(visible_app.id),
    )
    assert preview["role_id"] == int(related_role.id)
    assert preview["application_id"] == int(visible_app.id)
    assert preview["candidate"] == "Candidate related-manual-run-visible"
    assert preview["agent_enabled"] is True
    assert preview["can_queue"] is True

    queued = {
        "type": "manual_agent_run",
        "status": "queued",
        "queued": True,
        "role_id": int(related_role.id),
        "application_id": int(visible_app.id),
    }
    with patch(
        "app.agent_chat.application_commands.publish_manual_run",
        return_value=queued,
    ) as publish:
        result = commands.enqueue_manual_run(
            db,
            related_role,
            user,
            application_id=int(visible_app.id),
            dispatch_key="related-manual-run-dispatch",
        )
    assert result == queued
    publish.assert_called_once_with(
        role=related_role,
        application_id=int(visible_app.id),
        dispatch_key="related-manual-run-dispatch",
    )

    hidden_applications = (
        unrelated_app,
        foreign_app,
        corrupt_candidate_app,
        deleted_candidate_app,
        closed_app,
        disqualified_app,
    )
    with patch("app.agent_chat.application_commands.publish_manual_run") as publish:
        for hidden_app in hidden_applications:
            with pytest.raises(commands.ApplicationCommandError) as preview_error:
                commands.preview_manual_run(
                    db,
                    related_role,
                    user,
                    application_id=int(hidden_app.id),
                )
            assert preview_error.value.code == "application_not_found"
            assert hidden_app.candidate.full_name not in str(preview_error.value)

            with pytest.raises(commands.ApplicationCommandError) as enqueue_error:
                commands.enqueue_manual_run(
                    db,
                    related_role,
                    user,
                    application_id=int(hidden_app.id),
                )
            assert enqueue_error.value.code == "application_not_found"
            assert hidden_app.candidate.full_name not in str(enqueue_error.value)
        publish.assert_not_called()


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
