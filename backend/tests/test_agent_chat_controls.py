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
from app.models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, Role
from app.models.task import Task
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
        role="owner",
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


def _active_task(db, org, role) -> Task:
    task = Task(
        organization_id=org.id,
        name="Active assessment",
        task_key=f"active-chat-{role.id}",
        is_template=False,
        is_active=True,
        repo_structure={"name": "exercise", "files": {"README.md": "Do it"}},
    )
    role.tasks.append(task)
    db.add(role)
    db.flush()
    return task


def _run(db, role, user, name, args):
    return tools.dispatch_tool(name, args, db=db, role=role, user=user)


def test_kick_cycle_dispatches_the_committed_role_revision(db):
    org = _org(db)
    role = _role(db, org, agentic=True, budget=5000)
    role.version = 7
    db.flush()

    with patch(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay"
    ) as dispatch:
        assert _controls._kick_cycle(role, activation=False) is True

    dispatch.assert_called_once_with(
        role.id,
        activation=False,
        dispatch_role_version=7,
    )


@patch.object(_controls, "_kick_cycle")
def test_activate_resumes_paused_role(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=True, budget=5000)
    _active_task(db, org, role)
    role.auto_promote = True
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "paused by recruiter"
    db.flush()

    res = _run(db, role, user, "set_agent_state", {"action": "activate"})
    assert res["ok"] and res["action"] == "activated"
    assert role.agentic_mode_enabled is True
    assert role.auto_promote is True
    assert role.agent_paused_at is None and role.agent_paused_reason is None
    assert role.agent_bootstrap_status == "starting"
    kick.assert_called_once_with(role, activation=False)


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
def test_sister_role_chat_controls_are_score_only(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=False, budget=5_000)
    role.role_kind = "sister"
    role.auto_advance = False
    db.flush()

    activation = _run(
        db, role, user, "set_agent_state", {"action": "activate"}
    )
    settings = _run(
        db,
        role,
        user,
        "adjust_agent_settings",
        {"monthly_budget_cents": 9_000, "auto_advance": True},
    )

    assert activation["ok"] is False
    assert activation["reason"] == "score_only_role"
    assert "score-only" in activation["message"]
    assert settings["ok"] is False
    assert settings["reason"] == "score_only_role"
    assert settings["changed"] == []
    assert "score-only" in settings["message"]
    assert role.agentic_mode_enabled is False
    assert role.monthly_usd_budget_cents == 5_000
    assert role.auto_advance is False
    kick.assert_not_called()


@patch.object(_controls, "_kick_cycle", return_value=True)
def test_chat_activation_opens_native_requisition(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=False, budget=5000)
    role.source = "requisition"
    role.job_status = JOB_STATUS_DRAFT
    _active_task(db, org, role)
    db.flush()

    res = _run(db, role, user, "set_agent_state", {"action": "activate"})

    assert res["ok"] is True
    assert role.job_status == JOB_STATUS_OPEN
    assert role.auto_promote is True
    assert role.starred_for_auto_sync is True
    kick.assert_called_once_with(role, activation=True)


@patch.object(_controls, "_kick_cycle", return_value=False)
def test_activate_dispatch_failure_restores_native_activation_state(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=False, budget=5000)
    role.source = "requisition"
    role.job_status = JOB_STATUS_DRAFT
    role.auto_promote = False
    role.starred_for_auto_sync = False
    _active_task(db, org, role)
    db.flush()

    res = _run(db, role, user, "set_agent_state", {"action": "activate"})

    assert res["ok"] is False and res["reason"] == "dispatch_failed"
    assert role.agentic_mode_enabled is False
    assert role.job_status == JOB_STATUS_DRAFT
    assert role.auto_promote is False
    assert role.starred_for_auto_sync is False
    assert role.agent_bootstrap_status == "failed"
    assert role.agent_bootstrap_error == "agent bootstrap dispatch failed"
    kick.assert_called_once_with(role, activation=True)


@patch.object(_controls, "_kick_cycle")
def test_activation_dispatch_failure_preserves_newer_role_revision(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=False, budget=5000)
    _active_task(db, org, role)
    db.flush()

    def _failed_after_newer_change(dispatched_role, *, activation=False):
        assert activation is True
        dispatched_role.name = "Renamed in newer UI"
        dispatched_role.monthly_usd_budget_cents = 9_000
        dispatched_role.version = int(dispatched_role.version or 1) + 1
        db.commit()
        return False

    kick.side_effect = _failed_after_newer_change
    res = _run(db, role, user, "set_agent_state", {"action": "activate"})

    assert res["ok"] is False and res["reason"] == "dispatch_failed"
    assert res["compensation_skipped"] is True
    db.refresh(role)
    assert role.version == 3
    assert role.name == "Renamed in newer UI"
    assert role.monthly_usd_budget_cents == 9_000
    assert role.agentic_mode_enabled is True
    assert role.agent_bootstrap_status == "starting"
    assert role.agent_bootstrap_error is None


@patch("app.tasks.assessment_tasks.generate_assessment_task_for_role.delay")
@patch.object(_controls, "_kick_cycle")
def test_chat_turn_on_fresh_requisition_persists_durable_activation(
    kick, generation, db
):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=False, budget=6500)
    role.source = "requisition"
    role.job_status = JOB_STATUS_DRAFT
    role.job_spec_text = (
        "Own a production data platform, its reliability roadmap, incident "
        "response, architecture decisions, and measurable delivery outcomes."
    )
    db.flush()

    res = _run(db, role, user, "set_agent_state", {"action": "activate"})

    assert res["ok"] is True
    assert res["action"] == "activation_queued"
    assert res["activation_intent"]["status"] == "pending"
    assert "No second approval click" in res["message"]
    assert role.agentic_mode_enabled is False
    assert role.job_status == JOB_STATUS_DRAFT
    assert role.assessment_task_provisioning["activation_intent"][
        "requested_by_user_id"
    ] == user.id
    generation.assert_called_once_with(role.id, org.id)
    kick.assert_not_called()


@patch.object(_controls, "_kick_cycle")
def test_chat_turn_on_confirms_blocked_republish_through_durable_activation(
    kick, db
):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=False, budget=7000)
    role.source = "requisition"
    role.job_status = JOB_STATUS_DRAFT
    task = _active_task(db, org, role)
    role.assessment_task_provisioning = {
        "status": "succeeded",
        "task_id": int(task.id),
        "activation_intent": {
            "status": "blocked",
            "command": "review_republished_task",
            "last_error": "Confirm the preserved manual task.",
        },
        "reconfiguration": {
            "status": "blocked",
            "preserved_task_id": int(task.id),
            "last_error": "Confirm the preserved manual task.",
        },
    }
    db.commit()

    with patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as cohort:
        res = _run(db, role, user, "set_agent_state", {"action": "activate"})

    assert res["ok"] is True
    assert res["action"] == "activation_queued"
    assert role.agentic_mode_enabled is False
    intent = role.assessment_task_provisioning["activation_intent"]
    assert intent["status"] == "pending"
    assert intent["task_id"] == task.id
    reconfiguration = role.assessment_task_provisioning["reconfiguration"]
    assert reconfiguration["status"] == "pending"
    assert reconfiguration["resolution"] == "preserved_task_confirmed_by_user"
    cohort.assert_called_once_with(
        role.id,
        activation=True,
        activation_intent_id=intent["request_id"],
    )
    kick.assert_not_called()


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


@patch.object(_controls, "_kick_cycle")
def test_adjust_agent_settings_rejects_zero_budget(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=True, budget=5_000)

    res = _run(
        db, role, user, "adjust_agent_settings", {"monthly_budget_cents": 0}
    )

    assert res["ok"] is False
    assert res["reason"] == "invalid_budget"
    assert role.monthly_usd_budget_cents == 5_000
    kick.assert_not_called()


@patch.object(_controls, "_kick_cycle")
def test_chat_cannot_enable_live_assessment_stage_without_task(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=True)
    role.auto_skip_assessment = True
    db.flush()

    res = _run(
        db, role, user, "adjust_agent_settings", {"auto_skip_assessment": False}
    )

    assert res["ok"] is False
    assert res["reason"] == "assessment_task_required"
    assert role.auto_skip_assessment is True
    kick.assert_not_called()


@patch.object(_controls, "_kick_cycle")
def test_budget_edit_does_not_undo_manual_pause(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=True, budget=100)
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "paused by recruiter"
    db.flush()

    res = _run(
        db,
        role,
        user,
        "adjust_agent_settings",
        {"monthly_budget_cents": 7500},
    )

    assert res["resumed"] is False
    assert role.agent_paused_at is not None
    assert role.agent_paused_reason == "paused by recruiter"
    assert not kick.called


@patch.object(_controls, "_kick_cycle", return_value=False)
def test_budget_resume_dispatch_failure_restores_pause(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=True, budget=100)
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "monthly USD cap reached"
    db.flush()

    res = _run(
        db,
        role,
        user,
        "adjust_agent_settings",
        {"monthly_budget_cents": 7500},
    )

    assert res["resumed"] is False
    assert "worker queue" in (res["resume_error"] or "")
    assert role.agent_paused_at is not None
    assert role.agent_bootstrap_status == "failed"
    assert role.agent_bootstrap_error == "agent bootstrap dispatch failed"
    kick.assert_called_once_with(role)


@patch.object(_controls, "_kick_cycle")
def test_budget_resume_dispatch_failure_preserves_newer_role_revision(kick, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=True, budget=100)
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "monthly USD cap reached"
    db.flush()

    def _failed_after_newer_change(dispatched_role, *, activation=False):
        assert activation is False
        dispatched_role.name = "Edited after resume"
        dispatched_role.version = int(dispatched_role.version or 1) + 1
        db.commit()
        return False

    kick.side_effect = _failed_after_newer_change
    res = _run(
        db,
        role,
        user,
        "adjust_agent_settings",
        {"monthly_budget_cents": 7500},
    )

    assert res["resumed"] is False
    assert res["compensation_skipped"] is True
    assert "newer job settings" in (res["resume_error"] or "")
    db.refresh(role)
    assert role.version == 3
    assert role.name == "Edited after resume"
    assert role.agent_paused_at is None
    assert role.agent_bootstrap_status == "starting"
    assert role.agent_bootstrap_error is None
