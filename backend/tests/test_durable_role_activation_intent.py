from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.organization import Organization
from app.models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, Role
from app.models.task import Task
from app.services.role_activation_intent import (
    complete_role_activation_intent,
    request_role_activation_intent,
)
from app.services.task_approval_service import PreparedTaskApproval, TaskApprovalError
from app.services.task_provisioning_service import (
    PROVISIONING_BLOCKED,
    claim_assessment_task_provisioning,
    finish_assessment_task_provisioning,
    request_assessment_task_provisioning,
)
from app.tasks.assessment_tasks import sweep_assessment_task_provisioning
from tests.conftest import auth_headers


def _role_with_passing_draft(db, *, suffix: str) -> tuple[Role, Task]:
    org = Organization(name=f"Activation {suffix}", slug=f"activation-{suffix}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Platform Engineer",
        source="requisition",
        job_status=JOB_STATUS_DRAFT,
        job_spec_text="Own a production platform and its reliability roadmap.",
    )
    task = Task(
        organization_id=org.id,
        name="Generated platform exercise",
        task_key=f"activation_{suffix}",
        is_template=False,
        is_active=False,
        repo_structure={"name": "exercise", "files": {"README.md": "Repair it"}},
        extra_data={
            "generated": True,
            "needs_review": True,
            "battle_test": {"verdict": "pass"},
            "battle_test_provisioning": {"status": "succeeded"},
        },
    )
    role.tasks.append(task)
    db.add(role)
    db.commit()
    db.refresh(role)
    return role, task


def _fake_prepare(captured):
    return PreparedTaskApproval(
        fingerprint=captured.fingerprint,
        repo_url="https://example.test/prepared-task",
    )


def test_turn_on_command_is_persisted_while_role_stays_off(client):
    headers, _ = auth_headers(client)
    created = client.post(
        "/api/v1/roles", json={"name": "Durable activation"}, headers=headers
    ).json()

    with patch(
        "app.tasks.assessment_tasks.generate_assessment_task_for_role.delay"
    ) as generation:
        response = client.patch(
            f"/api/v1/roles/{created['id']}",
            json={
                "expected_version": created["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 7500,
                "auto_promote": True,
                "activation_assessment_action": "approve_when_ready",
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agentic_mode_enabled"] is False
    assert body["monthly_usd_budget_cents"] == 7500
    intent = body["assessment_task_provisioning"]["activation_intent"]
    assert intent["status"] == "pending"
    assert intent["command"] == "approve_when_ready"
    assert int(intent["requested_by_user_id"]) > 0
    assert intent["monthly_usd_budget_cents"] == 7500
    generation.assert_called_once_with(created["id"], created["organization_id"])


def test_turn_off_cancels_a_pending_activation_intent(client):
    headers, _ = auth_headers(client)
    created = client.post(
        "/api/v1/roles", json={"name": "Cancel durable activation"}, headers=headers
    ).json()
    with patch("app.tasks.assessment_tasks.generate_assessment_task_for_role.delay"):
        queued = client.patch(
            f"/api/v1/roles/{created['id']}",
            json={
                "expected_version": created["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "activation_assessment_action": "approve_when_ready",
            },
            headers=headers,
        )
    assert queued.status_code == 200

    # This test owns activation-intent cancellation, not Redis availability.
    # Simulate a healthy ATS fence so Turn-off reaches the transition under test.
    with patch(
        "app.domains.assessments_runtime.roles_management_routes."
        "require_authorized_agent_control_transaction_fence"
    ):
        cancelled = client.patch(
            f"/api/v1/roles/{created['id']}",
            json={
                "expected_version": queued.json()["version"],
                "agentic_mode_enabled": False,
            },
            headers=headers,
        )
    assert cancelled.status_code == 200
    intent = cancelled.json()["assessment_task_provisioning"]["activation_intent"]
    assert intent["status"] == "cancelled"
    assert cancelled.json()["agentic_mode_enabled"] is False


def test_explicit_skip_resolves_blocked_reconfiguration(client, db):
    headers, _ = auth_headers(client)
    created = client.post(
        "/api/v1/roles",
        json={"name": "Skip blocked reconfiguration"},
        headers=headers,
    ).json()
    role = db.query(Role).filter(Role.id == created["id"]).one()
    role.assessment_task_provisioning = {
        "status": "blocked",
        "activation_intent": {
            "status": "blocked",
            "command": "review_republished_task",
            "request_id": "blocked-skip-request",
            "last_error": "Confirm a replacement or skip assessment.",
        },
        "reconfiguration": {
            "status": "blocked",
            "last_error": "Confirm a replacement or skip assessment.",
        },
    }
    db.commit()

    with patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"):
        response = client.patch(
            f"/api/v1/roles/{role.id}",
            json={
                "expected_version": int(role.version or 1),
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5_000,
                "activation_assessment_action": "skip_assessment",
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agentic_mode_enabled"] is True
    assert body["auto_skip_assessment"] is True
    state = body["assessment_task_provisioning"]
    assert state["activation_intent"]["status"] == "cancelled"
    assert state["reconfiguration"]["status"] == "succeeded"
    assert state["reconfiguration"]["resolution"] == "assessment_skipped_by_user"


def test_failed_skip_activation_restores_blocked_reconfiguration(client, db):
    headers, _ = auth_headers(client)
    created = client.post(
        "/api/v1/roles",
        json={"name": "Compensate blocked skip"},
        headers=headers,
    ).json()
    role = db.query(Role).filter(Role.id == created["id"]).one()
    role.assessment_task_provisioning = {
        "status": "blocked",
        "activation_intent": {
            "status": "blocked",
            "command": "review_republished_task",
            "request_id": "blocked-compensation-request",
            "last_error": "Confirm a replacement or skip assessment.",
        },
        "reconfiguration": {
            "status": "blocked",
            "last_error": "Confirm a replacement or skip assessment.",
        },
    }
    db.commit()

    with patch(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        side_effect=RuntimeError("broker down"),
    ):
        response = client.patch(
            f"/api/v1/roles/{role.id}",
            json={
                "expected_version": int(role.version or 1),
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5_000,
                "activation_assessment_action": "skip_assessment",
            },
            headers=headers,
        )

    assert response.status_code == 503, response.text
    db.expire_all()
    restored = db.query(Role).filter(Role.id == role.id).one()
    assert restored.agentic_mode_enabled is False
    assert restored.auto_skip_assessment is False
    state = restored.assessment_task_provisioning
    assert state["status"] == "blocked"
    assert state["activation_intent"]["status"] == "blocked"
    assert state["activation_intent"]["request_id"] == (
        "blocked-compensation-request"
    )
    assert state["reconfiguration"]["status"] == "blocked"
    assert "resolution" not in state["reconfiguration"]


@pytest.mark.parametrize("intent_status", ["pending", "retry_wait", "blocked"])
def test_role_patch_versions_unfinished_activation_with_latest_policy(
    client, db, intent_status
):
    headers, _ = auth_headers(client)
    with patch(
        "app.tasks.assessment_tasks.generate_assessment_task_for_role.delay"
    ):
        created = client.post(
            "/api/v1/roles",
            json={"name": f"Policy race {intent_status}"},
            headers=headers,
        ).json()
        queued = client.patch(
            f"/api/v1/roles/{created['id']}",
            json={
                "expected_version": created["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 7_500,
                "activation_assessment_action": "approve_when_ready",
            },
            headers=headers,
        )
    assert queued.status_code == 200, queued.text

    role = db.query(Role).filter(Role.id == created["id"]).one()
    provisioning = dict(role.assessment_task_provisioning or {})
    intent = dict(provisioning["activation_intent"])
    intent["status"] = intent_status
    if intent_status == "retry_wait":
        intent["next_attempt_at"] = (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat()
    if intent_status == "blocked":
        intent["last_error"] = "waiting for explicit retry"
        intent["blocked_at"] = datetime.now(timezone.utc).isoformat()
    provisioning["activation_intent"] = intent
    role.assessment_task_provisioning = provisioning
    db.commit()

    updated = client.patch(
        f"/api/v1/roles/{role.id}",
        json={
            "expected_version": queued.json()["version"],
            "monthly_usd_budget_cents": 3_300,
            "auto_send_assessment": False,
            "auto_resend_assessment": False,
            "auto_advance": False,
            "auto_reject": True,
            "auto_reject_pre_screen": True,
            "auto_skip_assessment": True,
            "auto_reject_threshold_mode": "manual",
            "score_threshold": 82,
            "agent_action_allowlist": ["review_candidate"],
            "agent_token_budget_per_cycle": 2_000,
            "agent_decision_budget_per_cycle": 3,
        },
        headers=headers,
    )

    assert updated.status_code == 200, updated.text
    refreshed = updated.json()["assessment_task_provisioning"][
        "activation_intent"
    ]
    assert refreshed["status"] == intent_status
    assert refreshed["policy_revision"] == 2
    assert refreshed["monthly_usd_budget_cents"] == 3_300
    assert refreshed["auto_send_assessment"] is False
    assert refreshed["auto_resend_assessment"] is False
    assert refreshed["auto_advance"] is False
    assert refreshed["auto_reject"] is True
    assert refreshed["auto_reject_pre_screen"] is True
    assert refreshed["auto_skip_assessment"] is True
    assert refreshed["auto_reject_threshold_mode"] == "manual"
    assert refreshed["score_threshold"] == 82
    assert refreshed["agent_action_allowlist"] == ["review_candidate"]
    assert refreshed["agent_token_budget_per_cycle"] == 2_000
    assert refreshed["agent_decision_budget_per_cycle"] == 3


def test_latest_skip_and_restrictions_win_when_pending_activation_completes(
    client, db
):
    headers, _ = auth_headers(client)
    with patch(
        "app.tasks.assessment_tasks.generate_assessment_task_for_role.delay"
    ):
        created = client.post(
            "/api/v1/roles",
            json={"name": "Skip activation race"},
            headers=headers,
        ).json()
        queued = client.patch(
            f"/api/v1/roles/{created['id']}",
            json={
                "expected_version": created["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 8_000,
                "auto_send_assessment": True,
                "auto_resend_assessment": True,
                "auto_advance": True,
                "activation_assessment_action": "approve_when_ready",
            },
            headers=headers,
        )
    assert queued.status_code == 200, queued.text

    tightened = client.patch(
        f"/api/v1/roles/{created['id']}",
        json={
            "expected_version": queued.json()["version"],
            "monthly_usd_budget_cents": 2_500,
            "auto_send_assessment": False,
            "auto_resend_assessment": False,
            "auto_advance": False,
            "auto_skip_assessment": True,
            "auto_reject": False,
            "agent_action_allowlist": ["review_candidate"],
            "agent_token_budget_per_cycle": 2_000,
            "agent_decision_budget_per_cycle": 2,
        },
        headers=headers,
    )
    assert tightened.status_code == 200, tightened.text
    intent = tightened.json()["assessment_task_provisioning"][
        "activation_intent"
    ]

    with (
        patch("app.services.task_approval_service.prepare_task_approval") as approve,
        patch("app.services.application_events.on_role_jd_attached"),
        patch("app.tasks.automation_tasks.regenerate_role_tech_questions.delay"),
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=[],
        ),
    ):
        completed = complete_role_activation_intent(
            db,
            role_id=created["id"],
            request_id=intent["request_id"],
            worker_task_id="worker-skip-race",
        )

    assert completed == {
        "status": "activated",
        "role_id": created["id"],
        "task_id": None,
    }
    approve.assert_not_called()
    db.expire_all()
    role = db.query(Role).filter(Role.id == created["id"]).one()
    assert role.agentic_mode_enabled is True
    assert role.monthly_usd_budget_cents == 2_500
    assert role.auto_send_assessment is False
    assert role.auto_resend_assessment is False
    assert role.auto_advance is False
    assert role.auto_skip_assessment is True
    assert role.agent_action_allowlist == ["review_candidate"]
    assert role.agent_token_budget_per_cycle == 2_000
    assert role.agent_decision_budget_per_cycle == 2
    assert role.assessment_task_provisioning["activation_intent"][
        "status"
    ] == "succeeded"


def test_turn_on_blocks_inactive_manual_task_instead_of_waiting_forever(db):
    role, task = _role_with_passing_draft(db, suffix="inactive-manual")
    task.extra_data = {"generated": False, "needs_review": False}
    db.commit()

    intent = request_role_activation_intent(
        role, user_id=18, monthly_budget_cents=5000
    )
    db.commit()

    assert intent["status"] == "blocked"
    assert intent["task_id"] is None
    assert "inactive" in intent["last_error"].lower()
    assert "press Turn on again" in intent["last_error"]
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    assert persisted.agentic_mode_enabled is False
    assert (
        persisted.assessment_task_provisioning["activation_intent"]["status"]
        == "blocked"
    )


def test_blocked_task_generation_blocks_activation_instead_of_waiting_forever(db):
    org = Organization(name="Blocked activation", slug="blocked-activation")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Thin role",
        description="Too thin",
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    intent = request_role_activation_intent(
        role, user_id=14, monthly_budget_cents=5000
    )
    db.commit()
    claim = claim_assessment_task_provisioning(
        db, role_id=role.id, organization_id=org.id
    )
    assert claim.status == "claimed"

    assert finish_assessment_task_provisioning(
        db,
        role_id=role.id,
        organization_id=org.id,
        claim_token=claim.claim_token or "",
        status=PROVISIONING_BLOCKED,
        error="role JD is too thin to generate an assessment",
    ) is True
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    blocked = persisted.assessment_task_provisioning["activation_intent"]
    assert blocked["request_id"] == intent["request_id"]
    assert blocked["status"] == "blocked"
    assert "too thin" in blocked["last_error"]
    assert blocked["next_attempt_at"] is None


def test_republish_invalidates_pending_activation_without_spending(db):
    role, task = _role_with_passing_draft(db, suffix="republish")
    intent = request_role_activation_intent(
        role, user_id=16, monthly_budget_cents=5000
    )
    db.commit()

    requested = request_assessment_task_provisioning(
        role,
        reason="requisition_publish",
        supersede_generated_drafts=True,
        defer_until_activation=True,
    )
    db.commit()

    assert requested is False
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    state = persisted.assessment_task_provisioning
    assert state["status"] == "awaiting_activation"
    assert state["request_id"] != intent.get("provisioning_request_id")
    assert state["activation_intent"]["status"] == "blocked"
    assert "changed after turn on" in state["activation_intent"]["last_error"].lower()
    assert all(linked.id != task.id for linked in persisted.tasks)


def test_passing_task_is_approved_and_activated_once(db):
    role, task = _role_with_passing_draft(db, suffix="success")
    intent = request_role_activation_intent(
        role, user_id=11, monthly_budget_cents=9000
    )
    db.commit()

    with (
        patch(
            "app.services.task_approval_service.prepare_task_approval",
            side_effect=_fake_prepare,
        ) as approve,
        patch("app.services.application_events.on_role_jd_attached"),
        patch("app.tasks.automation_tasks.regenerate_role_tech_questions.delay"),
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=[],
        ),
    ):
        first = complete_role_activation_intent(
            db,
            role_id=role.id,
            request_id=intent["request_id"],
            worker_task_id="worker-1",
        )
        duplicate = complete_role_activation_intent(
            db,
            role_id=role.id,
            request_id=intent["request_id"],
            worker_task_id="worker-2",
        )

    assert first["status"] == "activated"
    assert duplicate["status"] == "duplicate"
    approve.assert_called_once()
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    persisted_task = db.query(Task).filter(Task.id == task.id).one()
    assert persisted.agentic_mode_enabled is True
    assert persisted.auto_promote is True
    assert persisted.starred_for_auto_sync is True
    assert persisted.job_status == JOB_STATUS_OPEN
    assert persisted.agent_bootstrap_status == "starting"
    assert persisted_task.is_active is True
    assert persisted.assessment_task_provisioning["activation_intent"]["status"] == "succeeded"


def test_selected_task_unlink_blocks_activation_instead_of_waiting_forever(db):
    role, task = _role_with_passing_draft(db, suffix="selected-unlinked")
    intent = request_role_activation_intent(
        role,
        user_id=19,
        monthly_budget_cents=7_000,
    )
    db.commit()
    role.tasks = [linked for linked in role.tasks if int(linked.id) != int(task.id)]
    db.commit()

    result = complete_role_activation_intent(
        db,
        role_id=int(role.id),
        request_id=str(intent["request_id"]),
        worker_task_id="worker-selected-unlinked",
    )

    assert result["status"] == "blocked"
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == int(role.id)).one()
    blocked = persisted.assessment_task_provisioning["activation_intent"]
    assert blocked["status"] == "blocked"
    assert int(blocked["task_id"]) == int(task.id)
    assert "no longer linked and active" in blocked["last_error"]
    assert persisted.agentic_mode_enabled is False


def test_malformed_explicit_activation_task_id_never_falls_back(db):
    role, _task = _role_with_passing_draft(db, suffix="malformed-task-id")
    intent = request_role_activation_intent(
        role,
        user_id=21,
        monthly_budget_cents=7_000,
    )
    provisioning = dict(role.assessment_task_provisioning or {})
    malformed = dict(provisioning["activation_intent"])
    malformed["task_id"] = ""
    provisioning["activation_intent"] = malformed
    role.assessment_task_provisioning = provisioning
    db.commit()

    with patch("app.services.task_approval_service.prepare_task_approval") as approve:
        result = complete_role_activation_intent(
            db,
            role_id=int(role.id),
            request_id=str(intent["request_id"]),
            worker_task_id="worker-malformed-task-id",
        )

    assert result["status"] == "blocked"
    approve.assert_not_called()
    db.expire_all()
    persisted = db.get(Role, int(role.id))
    assert persisted.agentic_mode_enabled is False
    assert "invalid identifier" in persisted.assessment_task_provisioning[
        "activation_intent"
    ]["last_error"]


def test_durable_activation_revalidates_locked_task_provenance(db):
    role, task = _role_with_passing_draft(db, suffix="task-provenance-race")
    intent = request_role_activation_intent(
        role,
        user_id=22,
        monthly_budget_cents=7_000,
    )
    db.commit()
    task.extra_data = {
        **dict(task.extra_data or {}),
        "generated": False,
    }
    db.commit()

    with patch("app.services.task_approval_service.prepare_task_approval") as approve:
        result = complete_role_activation_intent(
            db,
            role_id=int(role.id),
            request_id=str(intent["request_id"]),
            worker_task_id="worker-task-provenance-race",
        )

    assert result["status"] == "blocked"
    approve.assert_not_called()
    db.expire_all()
    assert db.get(Task, int(task.id)).is_active is False
    assert db.get(Role, int(role.id)).agentic_mode_enabled is False


def test_durable_activation_rejects_task_changed_during_repository_prepare(db):
    role, task = _role_with_passing_draft(db, suffix="task-content-race")
    intent = request_role_activation_intent(
        role,
        user_id=24,
        monthly_budget_cents=7_000,
    )
    db.commit()

    def concurrent_edit(captured):
        edited = db.get(Task, int(task.id))
        edited.name = "Edited while repository preparation was in flight"
        db.commit()
        return _fake_prepare(captured)

    with patch(
        "app.services.task_approval_service.prepare_task_approval",
        side_effect=concurrent_edit,
    ):
        result = complete_role_activation_intent(
            db,
            role_id=int(role.id),
            request_id=str(intent["request_id"]),
            worker_task_id="worker-task-content-race",
        )

    assert result["status"] == "retry_wait"
    db.expire_all()
    persisted_task = db.get(Task, int(task.id))
    persisted_role = db.get(Role, int(role.id))
    assert persisted_task.is_active is False
    assert persisted_role.agentic_mode_enabled is False
    assert "changed" in persisted_role.assessment_task_provisioning[
        "activation_intent"
    ]["last_error"].lower()


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        ("task_repository_definition_missing", "blocked"),
        ("task_repository_unavailable", "retry_wait"),
    ],
)
def test_activation_classifies_task_approval_failures(
    db,
    code,
    expected_status,
):
    role, _task = _role_with_passing_draft(db, suffix=f"approval-{code}")
    intent = request_role_activation_intent(
        role,
        user_id=23,
        monthly_budget_cents=7_000,
    )
    db.commit()
    error = TaskApprovalError("approval failed", code=code)

    with patch(
        "app.services.task_approval_service.prepare_task_approval",
        side_effect=error,
    ):
        result = complete_role_activation_intent(
            db,
            role_id=int(role.id),
            request_id=str(intent["request_id"]),
            worker_task_id=f"worker-{code}",
        )

    assert result["status"] == expected_status
    db.expire_all()
    persisted = db.get(Role, int(role.id))
    assert persisted.agentic_mode_enabled is False
    assert persisted.assessment_task_provisioning["activation_intent"][
        "status"
    ] == expected_status


def test_workspace_pause_prevents_deferred_activation_after_worker_acceptance(db):
    role, task = _role_with_passing_draft(db, suffix="workspace-race")
    intent = request_role_activation_intent(
        role,
        user_id=20,
        monthly_budget_cents=7_000,
    )
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    db.commit()

    with patch("app.services.task_approval_service.prepare_task_approval") as approve:
        result = complete_role_activation_intent(
            db,
            role_id=int(role.id),
            request_id=str(intent["request_id"]),
            worker_task_id="worker-workspace-race",
        )

    assert result == {"status": "workspace_paused"}
    approve.assert_not_called()
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == int(role.id)).one()
    persisted_task = db.query(Task).filter(Task.id == int(task.id)).one()
    assert persisted.agentic_mode_enabled is False
    assert persisted_task.is_active is False
    assert persisted.assessment_task_provisioning["activation_intent"][
        "status"
    ] == "pending"


def test_explicit_turn_on_activates_preserved_active_manual_task_without_battle(db):
    role, task = _role_with_passing_draft(db, suffix="preserved-manual")
    task.is_active = True
    task.extra_data = {"generated": False, "needs_review": False}
    db.commit()
    intent = request_role_activation_intent(
        role, user_id=17, monthly_budget_cents=6000
    )
    db.commit()

    with (
        patch("app.services.task_approval_service.prepare_task_approval") as approve,
        patch("app.services.application_events.on_role_jd_attached"),
        patch("app.tasks.automation_tasks.regenerate_role_tech_questions.delay"),
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=[],
        ),
    ):
        result = complete_role_activation_intent(
            db,
            role_id=role.id,
            request_id=intent["request_id"],
            worker_task_id="worker-preserved-manual",
        )

    assert result["status"] == "activated"
    approve.assert_not_called()
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    assert persisted.agentic_mode_enabled is True
    assert persisted.job_status == JOB_STATUS_OPEN
    assert persisted.assessment_task_provisioning["activation_intent"]["task_id"] == task.id


def test_readiness_failure_rolls_back_and_remains_retryable(db):
    role, task = _role_with_passing_draft(db, suffix="readiness")
    intent = request_role_activation_intent(
        role, user_id=12, monthly_budget_cents=8000
    )
    db.commit()
    started = datetime.now(timezone.utc)

    with (
        patch(
            "app.services.task_approval_service.prepare_task_approval",
            side_effect=_fake_prepare,
        ),
        patch(
            "app.services.agent_activation_readiness.activation_readiness",
            return_value={
                "ready": False,
                "reasons": [{"code": "worker_unready", "detail": "broker down"}],
            },
        ),
    ):
        result = complete_role_activation_intent(
            db,
            role_id=role.id,
            request_id=intent["request_id"],
            worker_task_id="worker-readiness",
            now=started,
        )

    assert result["status"] == "retry_wait"
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    persisted_task = db.query(Task).filter(Task.id == task.id).one()
    assert persisted.agentic_mode_enabled is False
    assert persisted_task.is_active is False
    retry = persisted.assessment_task_provisioning["activation_intent"]
    assert retry["status"] == "retry_wait"
    assert "worker_unready" in retry["last_error"]

    with (
        patch(
            "app.services.task_approval_service.prepare_task_approval",
            side_effect=_fake_prepare,
        ),
        patch("app.services.application_events.on_role_jd_attached"),
        patch("app.tasks.automation_tasks.regenerate_role_tech_questions.delay"),
        patch(
            "app.services.agent_activation_checklist.surface_activation_questions",
            return_value=[],
        ),
    ):
        recovered = complete_role_activation_intent(
            db,
            role_id=role.id,
            request_id=intent["request_id"],
            worker_task_id="worker-recovered",
            now=started + timedelta(minutes=6),
        )
    assert recovered["status"] == "activated"


def test_unexpected_activation_failure_is_logged_as_stable_code(db):
    role, _task = _role_with_passing_draft(db, suffix="safe-error")
    intent = request_role_activation_intent(
        role, user_id=14, monthly_budget_cents=8000
    )
    db.commit()
    secret = "sdk-token=private-value"

    with patch(
        "app.services.task_approval_service.prepare_task_approval",
        side_effect=RuntimeError(secret),
    ):
        result = complete_role_activation_intent(
            db,
            role_id=role.id,
            request_id=intent["request_id"],
            worker_task_id="worker-safe-error",
        )

    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    retry = persisted.assessment_task_provisioning["activation_intent"]
    assert result["reason"] == "activation_failed"
    assert retry["last_error"] == "activation_failed"
    assert secret not in str(result)
    assert secret not in str(retry)


def test_role_response_sanitizes_legacy_errors_but_keeps_blocked_guidance(
    client, db
):
    headers, _ = auth_headers(client)
    created = client.post(
        "/api/v1/roles", json={"name": "Safe role state"}, headers=headers
    ).json()
    role = db.query(Role).filter(Role.id == created["id"]).one()
    secret = "sdk-token=private-value"
    role.agent_bootstrap_status = "failed"
    role.agent_bootstrap_error = f"RuntimeError: {secret}"
    role.assessment_task_provisioning = {
        "status": "retry_wait",
        "last_error": f"RuntimeError: {secret}",
        "activation_intent": {
            "status": "retry_wait",
            "last_error": f"RuntimeError: {secret}",
        },
        "interview_focus_provisioning": {
            "status": "retry_wait",
            "last_error": f"RuntimeError: {secret}",
        },
        "tech_questions_provisioning": {
            "status": "retry_wait",
            "last_error": f"RuntimeError: {secret}",
        },
    }
    db.commit()

    response = client.get(f"/api/v1/roles/{role.id}", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    state = payload["assessment_task_provisioning"]
    assert payload["agent_bootstrap_error"] == "agent_bootstrap_failed"
    assert state["last_error"] == "assessment_task_generation_failed"
    assert state["activation_intent"]["last_error"] == "activation_failed"
    assert state["interview_focus_provisioning"]["last_error"] == (
        "interview_focus_generation_failed"
    )
    assert state["tech_questions_provisioning"]["last_error"] == (
        "tech_question_generation_failed"
    )
    assert secret not in str(payload)

    role.assessment_task_provisioning = {
        "status": "blocked",
        "last_error": "The job description is too thin; add role outcomes.",
    }
    role.agent_bootstrap_error = "The job description is too thin; add role outcomes."
    db.commit()
    blocked = client.get(f"/api/v1/roles/{role.id}", headers=headers).json()
    assert blocked["assessment_task_provisioning"]["last_error"] == (
        "The job description is too thin; add role outcomes."
    )
    assert blocked["agent_bootstrap_error"] == (
        "The job description is too thin; add role outcomes."
    )


def test_sweep_broker_failure_leaves_role_off_and_intent_due(db):
    role, _ = _role_with_passing_draft(db, suffix="broker")
    intent = request_role_activation_intent(
        role, user_id=13, monthly_budget_cents=7000
    )
    db.commit()

    with (
        patch("app.tasks.assessment_tasks.settings.AUTO_GENERATE_ASSESSMENT_TASKS", True),
        patch(
            "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
            side_effect=RuntimeError("broker unavailable"),
        ),
    ):
        summary = sweep_assessment_task_provisioning.run(limit=50)

    assert summary["activation_due"] == 1
    assert summary["activation_failed"] == 1
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    assert persisted.agentic_mode_enabled is False
    assert persisted.assessment_task_provisioning["activation_intent"]["request_id"] == intent["request_id"]
    assert persisted.assessment_task_provisioning["activation_intent"]["status"] == "pending"


def test_sweep_leaves_activation_intent_pending_under_workspace_pause(db):
    role, _ = _role_with_passing_draft(db, suffix="workspace-held")
    intent = request_role_activation_intent(
        role, user_id=14, monthly_budget_cents=7000
    )
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    db.commit()

    with (
        patch("app.tasks.assessment_tasks.settings.AUTO_GENERATE_ASSESSMENT_TASKS", True),
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as activation,
    ):
        summary = sweep_assessment_task_provisioning.run(limit=50)

    assert summary["activation_due"] == 0
    activation.assert_not_called()
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    assert persisted.agentic_mode_enabled is False
    held = persisted.assessment_task_provisioning["activation_intent"]
    assert held["request_id"] == intent["request_id"]
    assert held["status"] == "pending"


def test_sweep_surfaces_repair_exhaustion_without_dispatching_activation(db):
    role, task = _role_with_passing_draft(db, suffix="repair-exhausted")
    extra = dict(task.extra_data or {})
    extra["battle_test"] = {"verdict": "fail", "checks": []}
    extra["battle_test_provisioning"] = {
        "status": "repair_exhausted",
        "repair_attempts": 2,
    }
    task.extra_data = extra
    intent = request_role_activation_intent(
        role, user_id=15, monthly_budget_cents=7000
    )
    db.commit()

    with (
        patch("app.tasks.assessment_tasks.settings.AUTO_GENERATE_ASSESSMENT_TASKS", True),
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as activation,
    ):
        summary = sweep_assessment_task_provisioning.run(limit=50)

    assert summary["activation_blocked"] == 1
    assert summary["activation_due"] == 0
    activation.assert_not_called()
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    blocked = persisted.assessment_task_provisioning["activation_intent"]
    assert blocked["request_id"] == intent["request_id"]
    assert blocked["status"] == "blocked"
    assert "repair was exhausted" in blocked["last_error"].lower()
    assert persisted.agentic_mode_enabled is False


def test_sweep_applies_limit_after_activation_readiness_filter(db):
    for index in range(3):
        org = Organization(
            name=f"Waiting activation {index}",
            slug=f"waiting-activation-{index}",
        )
        db.add(org)
        db.flush()
        db.add(
            Role(
                organization_id=org.id,
                name=f"Waiting role {index}",
                agentic_mode_enabled=False,
                assessment_task_provisioning={
                    "activation_intent": {
                        "status": "pending",
                        "request_id": f"waiting-{index}",
                    }
                },
            )
        )
    db.commit()
    ready, _ = _role_with_passing_draft(db, suffix="after-waiting-rows")
    intent = request_role_activation_intent(
        ready,
        user_id=16,
        monthly_budget_cents=7000,
    )
    db.commit()

    with patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as activation:
        summary = sweep_assessment_task_provisioning.run(limit=1)

    assert set(summary) == {
        "status",
        "scanned",
        "due",
        "dispatched",
        "failed",
        "generation_enabled",
        "battle_scanned",
        "battle_due",
        "battle_dispatched",
        "battle_failed",
        "repair_due",
        "repair_dispatched",
        "repair_failed",
        "activation_due",
        "activation_dispatched",
        "activation_failed",
        "activation_blocked",
        "activation_scanned",
        "interview_focus_due",
        "interview_focus_scanned",
        "interview_focus_dispatched",
        "interview_focus_failed",
        "tech_questions_due",
        "tech_questions_scanned",
        "tech_questions_dispatched",
        "tech_questions_failed",
    }
    assert summary["activation_due"] == 1
    assert summary["activation_scanned"] == 1
    activation.assert_called_once_with(
        int(ready.id),
        activation=True,
        activation_intent_id=intent["request_id"],
    )


def test_sweep_zero_limit_does_not_dispatch_ready_activation(db):
    ready, _ = _role_with_passing_draft(db, suffix="zero-cap")
    request_role_activation_intent(
        ready,
        user_id=160,
        monthly_budget_cents=7000,
    )
    db.commit()

    with patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as activation:
        summary = sweep_assessment_task_provisioning.run(limit=0)

    assert summary["activation_due"] == 0
    assert summary["activation_scanned"] == 0
    activation.assert_not_called()


def test_sweep_rotates_still_pending_ready_intents_fairly(db):
    first, _ = _role_with_passing_draft(db, suffix="fair-first")
    first_intent = request_role_activation_intent(
        first,
        user_id=17,
        monthly_budget_cents=7000,
    )
    second, _ = _role_with_passing_draft(db, suffix="fair-second")
    second_intent = request_role_activation_intent(
        second,
        user_id=18,
        monthly_budget_cents=7000,
    )
    db.commit()

    with patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as activation:
        first_summary = sweep_assessment_task_provisioning.run(limit=1)
        second_summary = sweep_assessment_task_provisioning.run(limit=1)

    assert first_summary["activation_due"] == 1
    assert second_summary["activation_due"] == 1
    dispatched = [call.args[0] for call in activation.call_args_list]
    assert dispatched == [int(first.id), int(second.id)]
    assert {
        call.kwargs["activation_intent_id"] for call in activation.call_args_list
    } == {first_intent["request_id"], second_intent["request_id"]}
