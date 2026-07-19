"""Enabling agentic mode on a role auto-stars it for the periodic sync.

Rationale: agent-on roles need the periodic Workable fetch (comments,
activities, questionnaire answers) running so the agent's pre-screen
and scoring see fresh signal. Forcing the recruiter to remember to
click both the agent toggle AND the star is bad UX and easy to miss,
so we tie the two together.

One-way: disabling the agent does NOT unstar (star is sticky, can be
turned off independently).
"""

from __future__ import annotations

from unittest.mock import patch

from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.task import Task
from app.services.task_approval_service import PreparedTaskApproval
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
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        patch_resp = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "activation_assessment_action": "skip_assessment",
            },
            headers=headers,
        )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["agentic_mode_enabled"] is True
    assert body["starred_for_auto_sync"] is True
    assert body["auto_promote"] is False
    assert body["auto_send_assessment"] is False
    assert body["auto_resend_assessment"] is False
    assert body["auto_advance"] is False
    assert body["auto_reject_pre_screen"] is True
    assert body["auto_skip_assessment"] is True
    assert body["agent_bootstrap_status"] == "starting"
    assert body["agent_bootstrap_started_at"] is not None


def test_activation_allows_explicit_positive_action_opt_in(client):
    """A recruiter can still grant positive-action autonomy explicitly."""
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Explicit HITL Target")

    with (
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "auto_send_assessment": True,
                "auto_resend_assessment": True,
                "auto_advance": True,
                "activation_assessment_action": "skip_assessment",
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["auto_promote"] is True


def test_turn_on_can_atomically_skip_assessment(client):
    """The no-assessment choice is part of Turn on, not a hidden pre-step."""
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Inline Skip Target")

    with (
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
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


def _prepared_repository(repo_url: str):
    def _prepare(captured):
        return PreparedTaskApproval(
            fingerprint=captured.fingerprint,
            repo_url=repo_url,
        )

    return _prepare


def test_turn_on_explicitly_approves_validated_generated_task_in_same_patch(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Inline Approval Target")
    task_id = _link_generated_draft(role["id"], verdict="pass")

    with (
        patch(
            "app.domains.assessments_runtime.role_activation_update_preflight.prepare_task_approval",
            side_effect=_prepared_repository("mock://taali-assessments/inline"),
        ) as prepare,
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "activation_assessment_action": "approve_generated_task",
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["agentic_mode_enabled"] is True
    prepare.assert_called_once()
    tasks = client.get(f"/api/v1/roles/{role['id']}/tasks", headers=headers)
    assert tasks.status_code == 200
    approved = next(row for row in tasks.json() if row["id"] == task_id)
    assert approved["is_active"] is True
    assert approved["needs_review"] is False
    assert approved["generated"] is True
    assert approved["battle_test"]["verdict"] == "pass"


def test_direct_turn_on_rejects_task_changed_during_repository_preparation(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Stale task activation")
    task_id = _link_generated_draft(role["id"], verdict="pass")

    def _edit_task_then_prepare(captured):
        from tests.conftest import TestingSessionLocal

        concurrent = TestingSessionLocal()
        try:
            task = concurrent.get(Task, task_id)
            task.scenario = "A newer recruiter-authored scenario."
            concurrent.commit()
        finally:
            concurrent.close()
        return PreparedTaskApproval(
            fingerprint=captured.fingerprint,
            repo_url="mock://taali-assessments/stale-direct",
        )

    with patch(
        "app.domains.assessments_runtime.role_activation_update_preflight.prepare_task_approval",
        side_effect=_edit_task_then_prepare,
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5_000,
                "activation_assessment_action": "approve_generated_task",
            },
            headers=headers,
        )

    assert response.status_code == 409, response.text
    current_role = client.get(f"/api/v1/roles/{role['id']}", headers=headers).json()
    assert current_role["agentic_mode_enabled"] is False
    task = next(
        item
        for item in client.get(
            f"/api/v1/roles/{role['id']}/tasks",
            headers=headers,
        ).json()
        if item["id"] == task_id
    )
    assert task["is_active"] is False
    assert task["scenario"] == "A newer recruiter-authored scenario."


def test_direct_turn_on_provider_phase_holds_no_role_row_lock(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Unlocked preparation")
    _link_generated_draft(role["id"], verdict="pass")

    def _save_newer_role_then_prepare(captured):
        from tests.conftest import TestingSessionLocal

        concurrent = TestingSessionLocal()
        try:
            current = concurrent.get(Role, int(role["id"]))
            current.name = "Newer role save"
            current.version = int(current.version or 1) + 1
            concurrent.commit()
        finally:
            concurrent.close()
        return PreparedTaskApproval(
            fingerprint=captured.fingerprint,
            repo_url="mock://taali-assessments/unlocked-direct",
        )

    with patch(
        "app.domains.assessments_runtime.role_activation_update_preflight.prepare_task_approval",
        side_effect=_save_newer_role_then_prepare,
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5_000,
                "activation_assessment_action": "approve_generated_task",
            },
            headers=headers,
        )

    assert response.status_code == 409, response.text
    latest = client.get(f"/api/v1/roles/{role['id']}", headers=headers).json()
    assert latest["name"] == "Newer role save"
    assert latest["agentic_mode_enabled"] is False


def test_turn_on_never_implicitly_approves_validated_generated_task(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Explicit approval boundary")
    task_id = _link_generated_draft(role["id"], verdict="pass")

    with (
        patch(
            "app.domains.assessments_runtime.role_activation_update_preflight.prepare_task_approval"
        ) as prepare,
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5_000,
            },
            headers=headers,
        )

    assert response.status_code == 409, response.text
    assert "Generate assessment or Skip assessment" in response.text
    prepare.assert_not_called()
    role_state = client.get(
        f"/api/v1/roles/{role['id']}", headers=headers
    ).json()
    assert role_state["agentic_mode_enabled"] is False
    assert role_state["auto_skip_assessment"] is False
    task = next(
        row
        for row in client.get(
            f"/api/v1/roles/{role['id']}/tasks", headers=headers
        ).json()
        if row["id"] == task_id
    )
    assert task["is_active"] is False
    assert task["needs_review"] is True


def test_turn_on_taskless_raw_false_requires_explicit_generate_or_skip(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Taskless choice boundary")

    response = client.patch(
        f"/api/v1/roles/{role['id']}",
        json={
            "expected_version": role["version"],
            "agentic_mode_enabled": True,
            "monthly_usd_budget_cents": 5_000,
        },
        headers=headers,
    )

    assert response.status_code == 409, response.text
    assert "Generate assessment or Skip assessment" in response.text
    fetched = client.get(f"/api/v1/roles/{role['id']}", headers=headers).json()
    assert fetched["agentic_mode_enabled"] is False
    assert fetched["auto_skip_assessment"] is False
    assert fetched["version"] == role["version"]


def test_turn_on_refuses_generated_task_that_failed_battle_test(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Failed Draft Target")
    task_id = _link_generated_draft(role["id"], verdict="fail")

    with patch(
        "app.domains.assessments_runtime.role_activation_update_preflight.prepare_task_approval"
    ) as prepare:
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "activation_assessment_action": "approve_generated_task",
            },
            headers=headers,
        )

    assert response.status_code == 409
    assert "battle test" in response.text.lower()
    prepare.assert_not_called()
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


def test_activation_dispatch_failure_is_fail_closed(client, db):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Broker Failure Target")
    candidate = Candidate(
        organization_id=role["organization_id"],
        email="broker-failure@example.test",
        full_name="Broker Failure",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=role["organization_id"],
        candidate_id=candidate.id,
        role_id=role["id"],
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    decision = AgentDecision(
        organization_id=role["organization_id"],
        role_id=role["id"],
        application_id=application.id,
        decision_type="send_assessment",
        recommendation="send_assessment",
        status="pending",
        reasoning="pre-existing recommendation",
        evidence={},
        model_version="test",
        prompt_version="test",
        idempotency_key=f"broker-failure:{application.id}",
    )
    db.add(decision)
    db.commit()

    with (
        patch(
            "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
            side_effect=RuntimeError("broker down"),
        ),
        patch(
            "app.services.bulk_decision_service."
            "reconcile_pending_positive_decisions"
        ) as reconcile,
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "activation_assessment_action": "skip_assessment",
                "auto_send_assessment": True,
                "auto_resend_assessment": True,
                "auto_advance": True,
                "auto_reject_pre_screen": False,
            },
            headers=headers,
        )

    assert response.status_code == 503
    reconcile.assert_not_called()
    db.expire_all()
    persisted_decision = db.query(AgentDecision).filter(
        AgentDecision.id == decision.id
    ).one()
    assert persisted_decision.status == "pending"
    assert persisted_decision.decision_type == "send_assessment"
    fetched = client.get(f"/api/v1/roles/{role['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["agentic_mode_enabled"] is False
    # Fail-closed restores the pre-activation HITL-safe policy snapshot.
    assert fetched.json()["auto_promote"] is False
    assert fetched.json()["auto_send_assessment"] is False
    assert fetched.json()["auto_resend_assessment"] is False
    assert fetched.json()["auto_advance"] is False
    assert fetched.json()["auto_reject_pre_screen"] is True
    assert fetched.json()["auto_skip_assessment"] is False
    assert fetched.json()["agent_effective_policy"]["auto_skip_assessment"] is True
    assert fetched.json()["agent_effective_policy"]["auto_send_assessment"] is False
    assert fetched.json()["starred_for_auto_sync"] is False
    assert fetched.json()["agent_bootstrap_status"] == "failed"
    assert fetched.json()["agent_bootstrap_error"] == "agent_bootstrap_failed"


def test_prepared_task_is_retained_and_reused_after_bootstrap_broker_failure(
    client,
    db,
):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Prepared Task Retry")
    task_id = _link_generated_draft(role["id"], verdict="pass")
    db.add(
        AgentNeedsInput(
            organization_id=role["organization_id"],
            role_id=role["id"],
            kind="task_assignment_missing",
            prompt="Choose an assessment task.",
        )
    )
    db.commit()

    with (
        patch(
            "app.domains.assessments_runtime.role_activation_update_preflight.prepare_task_approval",
            side_effect=_prepared_repository(
                "https://example.test/prepared-task"
            ),
        ) as prepare,
        patch(
            "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
            side_effect=[RuntimeError("broker down"), None],
        ),
    ):
        failed = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5_000,
                "activation_assessment_action": "approve_generated_task",
            },
            headers=headers,
        )
        assert failed.status_code == 503, failed.text
        assert "assessment was prepared successfully" in failed.text.lower()

        after_failure = client.get(
            f"/api/v1/roles/{role['id']}", headers=headers
        ).json()
        retry = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": after_failure["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5_000,
            },
            headers=headers,
        )

    assert retry.status_code == 200, retry.text
    assert retry.json()["agentic_mode_enabled"] is True
    assert prepare.call_count == 1
    db.expire_all()
    task = db.query(Task).filter(Task.id == task_id).one()
    assert task.is_active is True
    assert task.extra_data["needs_review"] is False
    assert task.extra_data["repository_ready"]["repo_url"].endswith(
        "/prepared-task"
    )
    question = db.query(AgentNeedsInput).filter(
        AgentNeedsInput.role_id == role["id"],
        AgentNeedsInput.kind == "task_assignment_missing",
    ).one()
    assert question.resolved_at is not None


def test_broker_failure_preserves_superseding_role_version_and_prepared_task(
    client,
    db,
):
    """Compensation cannot overwrite a save made after dispatch started."""

    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Concurrent Activation")
    task_id = _link_generated_draft(role["id"], verdict="pass")

    def _supersede_then_fail(*_args, **_kwargs):
        from tests.conftest import TestingSessionLocal

        concurrent_db = TestingSessionLocal()
        try:
            concurrent_role = (
                concurrent_db.query(Role)
                .filter(Role.id == int(role["id"]))
                .one()
            )
            concurrent_role.name = "Concurrent Activation (newer save)"
            concurrent_role.version = int(concurrent_role.version or 1) + 1
            concurrent_db.commit()
        finally:
            concurrent_db.close()
        raise RuntimeError("broker down after a newer save")

    with (
        patch(
            "app.domains.assessments_runtime.role_activation_update_preflight.prepare_task_approval",
            side_effect=_prepared_repository(
                "https://example.test/concurrent-prepared-task"
            ),
        ),
        patch(
            "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
            side_effect=_supersede_then_fail,
        ),
    ):
        response = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5_000,
                "activation_assessment_action": "approve_generated_task",
            },
            headers=headers,
        )

    assert response.status_code == 503, response.text
    assert "newer shared role state was preserved" in response.text.lower()
    fetched = client.get(f"/api/v1/roles/{role['id']}", headers=headers)
    assert fetched.status_code == 200
    current = fetched.json()
    assert current["name"] == "Concurrent Activation (newer save)"
    assert current["agentic_mode_enabled"] is True
    assert current["version"] >= int(role["version"]) + 2

    db.expire_all()
    task = db.query(Task).filter(Task.id == task_id).one()
    assert task.is_active is True
    assert task.extra_data["repository_ready"]["repo_url"].endswith(
        "/concurrent-prepared-task"
    )


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
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "activation_assessment_action": "skip_assessment",
            },
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
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        on = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "activation_assessment_action": "skip_assessment",
            },
            headers=headers,
        )
    assert on.status_code == 200
    assert on.json()["starred_for_auto_sync"] is True

    # Turn off — star must remain (sticky).
    # This test owns sticky-star semantics, not Redis availability. Simulate a
    # healthy ATS fence so Turn-off reaches the state transition under test.
    with patch(
        "app.domains.assessments_runtime.roles_management_routes."
        "require_authorized_agent_control_transaction_fence"
    ):
        off = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": on.json()["version"],
                "agentic_mode_enabled": False,
            },
            headers=headers,
        )
    assert off.status_code == 200, off.text
    body = off.json()
    assert body["agentic_mode_enabled"] is False
    assert body["starred_for_auto_sync"] is True


def test_enabling_agent_on_already_starred_role_is_idempotent(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Pre-starred Target")

    star = client.post(
        f"/api/v1/roles/{role['id']}/star",
        json={"expected_version": role["version"]},
        headers=headers,
    )
    assert star.status_code == 200
    starred_role = star.json()

    with (
        patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay"),
    ):
        patch_resp = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={
                "expected_version": starred_role["version"],
                "agentic_mode_enabled": True,
                "monthly_usd_budget_cents": 5000,
                "activation_assessment_action": "skip_assessment",
            },
            headers=headers,
        )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["agentic_mode_enabled"] is True
    assert body["starred_for_auto_sync"] is True
