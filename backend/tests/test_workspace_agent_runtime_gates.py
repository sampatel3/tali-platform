"""Workspace pause is an overlay on autonomous runtime authority."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, Role
from app.models.user import User
from app.services.job_page_lifecycle import (
    INTAKE_WORKSPACE_PAUSED,
    native_intake_state,
    role_allows_new_paid_ats_work,
)
from app.services.role_execution_guard import automatic_role_action_block_reason
from app.tasks.agent_tasks import agent_cohort_tick_role, agent_manual_run
from tests.conftest import auth_headers


def _running_role(db, *, suffix: str) -> tuple[Organization, Role]:
    org = Organization(name=f"Workspace gate {suffix}", slug=f"workspace-gate-{suffix}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Platform Engineer",
        source="requisition",
        job_status=JOB_STATUS_OPEN,
        job_spec_text="Build and operate reliable distributed services.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.commit()
    return org, role


def _pause_workspace(db, org: Organization, *, version: int = 2) -> None:
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    org.agent_workspace_control_version = int(version)
    db.add(org)
    db.commit()


def test_workspace_pause_blocks_task_admission_and_manual_agent_run(db):
    org, role = _running_role(db, suffix="task")
    _pause_workspace(db, org)

    with (
        patch("app.tasks.agent_tasks._auto_enqueue_scoring") as auto_score,
        patch("app.agent_runtime.orchestrator.run_cycle") as run_cycle,
    ):
        cohort = agent_cohort_tick_role.run(int(role.id))
        manual = agent_manual_run.run(int(role.id))

    assert cohort == {
        "status": "skipped",
        "reason": "workspace_paused",
        "role_id": int(role.id),
    }
    assert manual == {
        "status": "skipped",
        "reason": "workspace_paused",
        "detail": "workspace agent is paused",
        "role_id": int(role.id),
    }
    assert automatic_role_action_block_reason(role, db=db) == (
        "workspace agent is paused"
    )
    auto_score.assert_not_called()
    run_cycle.assert_not_called()


def test_stale_workspace_resume_generation_cannot_start_queued_tick(db):
    org, role = _running_role(db, suffix="generation")
    org.agent_workspace_control_version = 7
    db.commit()

    with (
        patch("app.tasks.agent_tasks._auto_enqueue_scoring") as auto_score,
        patch("app.agent_runtime.orchestrator.run_cycle") as run_cycle,
    ):
        result = agent_cohort_tick_role.run(
            int(role.id),
            dispatch_workspace_version=6,
        )

    assert result == {
        "status": "skipped",
        "reason": "stale_workspace_control",
        "role_id": int(role.id),
        "dispatch_workspace_version": 6,
        "workspace_control_version": 7,
    }
    auto_score.assert_not_called()
    run_cycle.assert_not_called()


def test_workspace_pause_closes_native_intake_and_paid_ats_automation(db):
    org, role = _running_role(db, suffix="intake")
    _pause_workspace(db, org)

    intake = native_intake_state(role, db=db)

    assert intake == {"ready": False, "reason": INTAKE_WORKSPACE_PAUSED}
    assert role_allows_new_paid_ats_work(role, db=db) is False
    # The role-local desired state remains untouched under the overlay.
    assert role.agentic_mode_enabled is True
    assert role.agent_paused_at is None


def test_role_turn_on_under_workspace_overlay_saves_local_state_without_dispatch(
    client,
    db,
):
    headers, email = auth_headers(client)
    owner = db.query(User).filter(User.email == email).one()
    org = db.get(Organization, int(owner.organization_id))
    _pause_workspace(db, org)

    created = client.post(
        "/api/v1/roles",
        json={"name": "Overlay activation role"},
        headers=headers,
    )
    assert created.status_code in {200, 201}, created.text
    role = created.json()

    with (
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=None,
        ),
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as dispatch,
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agentic_mode_enabled"] is True
    assert body["agent_bootstrap_status"] == "starting"
    dispatch.assert_not_called()

