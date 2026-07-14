"""Enabling agentic mode on a role auto-stars it for the periodic sync.

Rationale: agent-on roles need the periodic Workable fetch (comments,
activities, questionnaire answers) running so the agent's pre-screen
and scoring see fresh signal. Forcing the recruiter to remember to
click both the agent toggle AND the star is bad UX and easy to miss,
so we tie the two together.

One-way: disabling the agent does NOT unstar (star is sticky, can be
turned off independently).

We patch ``surface_activation_questions`` to a no-op because the
activation checklist inserts ``agent_needs_input`` rows whose
BigInteger PK doesn't autoincrement in SQLite test mode. The auto-star
logic runs BEFORE the checklist surface, so this doesn't affect what
we're testing.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.conftest import auth_headers


def _create_role_via_api(client, headers, name="Test Role") -> dict:
    resp = client.post("/api/v1/roles", json={"name": name}, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def test_enabling_agentic_mode_auto_stars_role(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Agent Auto-Star Target")
    assert role.get("starred_for_auto_sync") is False
    assert role.get("agentic_mode_enabled") is False

    # Activating the agent requires a budget; PATCH both together.
    with (
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        patch_resp = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["agentic_mode_enabled"] is True
    assert body["starred_for_auto_sync"] is True
    assert body["auto_promote"] is True
    assert body["agent_bootstrap_status"] == "starting"
    assert body["agent_bootstrap_started_at"] is not None


def test_activation_allows_explicit_positive_action_hitl_opt_out(client):
    """Turn-on defaults reversible actions to autonomous, but an API caller
    can explicitly retain HITL in the same atomic activation PATCH."""
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Explicit HITL Target")

    with (
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "auto_promote": False,
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["auto_promote"] is False


def test_turn_on_can_atomically_skip_assessment(client):
    """The no-assessment choice is part of Turn on, not a hidden pre-step."""
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Inline Skip Target")

    with (
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "activation_assessment_action": "skip_assessment",
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["agentic_mode_enabled"] is True
    assert response.json()["auto_skip_assessment"] is True


def _link_generated_draft(role_id: int, *, verdict: str):
    from app.models.role import Role
    from app.models.task import Task
    from tests.conftest import TestingSessionLocal

    db = TestingSessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).one()
        task = Task(
            organization_id=role.organization_id,
            name="Generated role exercise",
            scenario="Diagnose and repair the supplied service.",
            duration_minutes=45,
            is_active=False,
            repo_structure={"name": "exercise", "files": {"README.md": "Fix it"}},
            extra_data={
                "generated": True,
                "needs_review": True,
                "battle_test": {"verdict": verdict},
            },
        )
        db.add(task)
        db.flush()
        role.tasks.append(task)
        db.commit()
        return int(task.id)
    finally:
        db.close()


def test_turn_on_auto_approves_validated_generated_task_in_same_patch(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Inline Approval Target")
    task_id = _link_generated_draft(role["id"], verdict="pass")

    def _approve(db, task, *, user_id):
        extra = dict(task.extra_data or {})
        extra["needs_review"] = False
        extra["approved_by_user_id"] = user_id
        task.extra_data = extra
        task.is_active = True
        db.add(task)
        db.flush()
        return task

    with (
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch(
            "app.services.task_approval_service.approve_task_for_use",
            side_effect=_approve,
        ) as approve,
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["agentic_mode_enabled"] is True
    approve.assert_called_once()
    tasks = client.get(f"/api/v1/roles/{role['id']}/tasks", headers=headers)
    assert tasks.status_code == 200
    approved = next(row for row in tasks.json() if row["id"] == task_id)
    assert approved["is_active"] is True
    assert approved["needs_review"] is False
    assert approved["generated"] is True
    assert approved["battle_test"]["verdict"] == "pass"


def test_turn_on_refuses_generated_task_that_failed_battle_test(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Failed Draft Target")
    task_id = _link_generated_draft(role["id"], verdict="fail")

    with patch(
        "app.services.task_approval_service.approve_task_for_use"
    ) as approve:
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "activation_assessment_action": "approve_generated_task",
            },
            headers=headers,
        )

    assert response.status_code == 409
    assert "battle test" in response.text.lower()
    approve.assert_not_called()
    fetched = client.get(f"/api/v1/roles/{role['id']}", headers=headers).json()
    assert fetched["agentic_mode_enabled"] is False
    task = next(
        row
        for row in client.get(
            f"/api/v1/roles/{role['id']}/tasks", headers=headers
        ).json()
        if row["id"] == task_id
    )
    assert task["is_active"] is False
    assert task["needs_review"] is True


def test_activation_dispatch_failure_is_fail_closed(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Broker Failure Target")

    with (
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch(
            "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
            side_effect=RuntimeError("broker down"),
        ),
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )

    assert response.status_code == 503
    fetched = client.get(f"/api/v1/roles/{role['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["agentic_mode_enabled"] is False
    # Fail-closed restores the pre-activation policy snapshot. New roles carry
    # reversible automation ON by default even while runtime power remains OFF.
    assert fetched.json()["auto_promote"] is True
    assert fetched.json()["agent_effective_policy"]["auto_send_assessment"] is True
    assert fetched.json()["starred_for_auto_sync"] is False
    assert fetched.json()["agent_bootstrap_status"] == "failed"
    assert "dispatch failed" in fetched.json()["agent_bootstrap_error"]


def test_production_activation_requires_fresh_worker_beat(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="No Worker Target")

    with (
        patch("app.platform.startup_validation.is_production_like", return_value=True),
        patch(
            "app.services.agent_worker_health.worker_beat_status",
            return_value={"ready": False, "reason": "heartbeat_stale"},
        ),
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )

    assert response.status_code == 503
    assert "heartbeat_stale" in response.text
    assert client.get(f"/api/v1/roles/{role['id']}", headers=headers).json()[
        "agentic_mode_enabled"
    ] is False


def test_disabling_agentic_mode_leaves_star_in_place(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Agent Toggle-Off Target")

    # Turn on (auto-stars).
    with (
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        on = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )
    assert on.status_code == 200
    assert on.json()["starred_for_auto_sync"] is True

    # Turn off — star must remain (sticky).
    off = client.patch(
        f"/api/v1/roles/{role['id']}",
        json={"agentic_mode_enabled": False},
        headers=headers,
    )
    assert off.status_code == 200, off.text
    body = off.json()
    assert body["agentic_mode_enabled"] is False
    assert body["starred_for_auto_sync"] is True


def test_enabling_agent_on_already_starred_role_is_idempotent(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Pre-starred Target")

    star = client.post(f"/api/v1/roles/{role['id']}/star", headers=headers)
    assert star.status_code == 200

    with (
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        patch_resp = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["agentic_mode_enabled"] is True
    assert body["starred_for_auto_sync"] is True
