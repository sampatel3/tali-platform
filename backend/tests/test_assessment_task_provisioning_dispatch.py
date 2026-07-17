"""Durability/retry coverage for automatic JD -> assessment generation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from sqlalchemy.orm import sessionmaker

from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.schemas.task import TaskResponse
from app.services.task_provisioning_service import (
    PROVISIONING_FAILED,
    PROVISIONING_PENDING,
    PROVISIONING_RUNNING,
    TaskProvisioningRetryableError,
    claim_assessment_task_provisioning,
    finish_assessment_task_provisioning,
    request_assessment_task_provisioning,
)
from app.tasks.assessment_tasks import (
    battle_test_generated_task,
    generate_assessment_task_for_role,
    repair_generated_task_after_battle_failure,
    sweep_assessment_task_provisioning,
)
from app.services.task_battle_test import (
    apply_battle_test_repair,
    initialize_battle_test_provisioning,
)
from app.services.task_spec_generator import GeneratedSpecResult


def _role(db, *, suffix: str, state: dict | None = None) -> Role:
    org = Organization(name=f"Provisioning {suffix}", slug=f"provisioning-{suffix}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Platform Engineer",
        description=(
            "Build and operate reliable distributed services, own production "
            "quality, incident response, automated delivery, observability, "
            "security, and measurable platform improvements across teams."
        ),
        assessment_task_provisioning=state,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def _generated_task(db, *, suffix: str) -> Task:
    org = Organization(name=f"Battle {suffix}", slug=f"battle-{suffix}")
    db.add(org)
    db.flush()
    extra = initialize_battle_test_provisioning(
        {"generated": True, "needs_review": True}
    )
    task = Task(
        organization_id=org.id,
        name="Generated platform exercise",
        task_key=f"generated_{suffix}",
        is_template=False,
        is_active=False,
        extra_data=extra,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_duplicate_worker_delivery_collapses_to_one_running_claim(db):
    role = _role(db, suffix="claim")
    request_assessment_task_provisioning(role, reason="requisition_publish")
    db.commit()

    first = claim_assessment_task_provisioning(
        db, role_id=role.id, organization_id=role.organization_id
    )
    second = claim_assessment_task_provisioning(
        db, role_id=role.id, organization_id=role.organization_id
    )

    assert first.status == "claimed"
    assert first.claim_token
    assert second.status == "already_running"
    db.refresh(role)
    assert role.assessment_task_provisioning["status"] == PROVISIONING_RUNNING
    assert role.assessment_task_provisioning["attempts"] == 1


def test_old_claim_cannot_overwrite_a_new_publish_request(db):
    role = _role(db, suffix="superseded")
    request_assessment_task_provisioning(role, reason="first_publish")
    db.commit()
    claim = claim_assessment_task_provisioning(
        db, role_id=role.id, organization_id=role.organization_id
    )

    db.refresh(role)
    request_assessment_task_provisioning(role, reason="republish")
    db.commit()
    finished = finish_assessment_task_provisioning(
        db,
        role_id=role.id,
        organization_id=role.organization_id,
        claim_token=claim.claim_token or "",
        status="succeeded",
        task_id=77,
    )

    assert finished is False
    db.refresh(role)
    assert role.assessment_task_provisioning["status"] == PROVISIONING_PENDING
    assert role.assessment_task_provisioning["reason"] == "republish"


def test_sweep_dispatches_due_intent_but_not_future_retry(db):
    pending = _role(db, suffix="pending")
    request_assessment_task_provisioning(pending, reason="requisition_publish")
    future = _role(
        db,
        suffix="future",
        state={
            "status": "retry_wait",
            "next_attempt_at": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    db.commit()

    with (
        patch("app.tasks.assessment_tasks.settings.AUTO_GENERATE_ASSESSMENT_TASKS", True),
        patch.object(generate_assessment_task_for_role, "delay") as dispatch,
    ):
        summary = sweep_assessment_task_provisioning.run(limit=20)

    assert summary["due"] == 1
    assert summary["dispatched"] == 1
    dispatch.assert_called_once_with(pending.id, pending.organization_id)
    assert future.assessment_task_provisioning["status"] == "retry_wait"


def test_generator_delivery_leaves_paid_intent_pending_under_workspace_pause(db):
    role = _role(db, suffix="workspace-held-generation")
    request_assessment_task_provisioning(role, reason="agent_turn_on")
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    db.commit()

    with patch(
        "app.services.task_provisioning_service.generate_and_link_task_for_role"
    ) as generate:
        result = generate_assessment_task_for_role.run(
            role.id, role.organization_id
        )

    assert result == {"status": "deferred", "reason": "workspace_paused"}
    generate.assert_not_called()
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    assert persisted.assessment_task_provisioning["status"] == PROVISIONING_PENDING
    assert persisted.assessment_task_provisioning["attempts"] == 0


def test_generator_failure_exhausts_bounded_chain_into_recoverable_state(db):
    role = _role(db, suffix="retry")
    request_assessment_task_provisioning(role, reason="requisition_publish")
    db.commit()

    with (
        patch("app.tasks.assessment_tasks.settings.ANTHROPIC_API_KEY", "sk-test"),
        patch(
            "app.services.task_provisioning_service.generate_and_link_task_for_role",
            side_effect=TaskProvisioningRetryableError("provider unavailable"),
        ) as generate,
    ):
        # Exercise the retrying state once, then the exhausted boundary. Celery
        # eager mode recursively follows Retry signatures, so explicit request
        # contexts make the boundary deterministic without sleeping.
        generate_assessment_task_for_role.push_request(retries=0)
        try:
            with patch.object(
                generate_assessment_task_for_role,
                "retry",
                side_effect=Retry("retry"),
            ) as retry:
                with pytest.raises(Retry):
                    generate_assessment_task_for_role.run(
                        role.id, role.organization_id
                    )
                retry.assert_called_once()
        finally:
            generate_assessment_task_for_role.pop_request()

        generate_assessment_task_for_role.push_request(retries=3)
        try:
            result = generate_assessment_task_for_role.run(
                role.id, role.organization_id
            )
        finally:
            generate_assessment_task_for_role.pop_request()

    assert result["status"] == "failed"
    assert generate_assessment_task_for_role.max_retries == 3
    assert generate.call_count == 2
    db.expire_all()
    persisted = db.query(Role).filter(Role.id == role.id).one()
    state = persisted.assessment_task_provisioning
    assert state["status"] == PROVISIONING_FAILED
    assert state["attempts"] == 2
    assert state["last_error"] == "assessment_task_generation_failed"
    assert "provider unavailable" not in state["last_error"]
    assert result["reason"] == "assessment_task_generation_failed"
    assert state["next_attempt_at"]


def test_task_response_sanitizes_legacy_battle_test_exceptions(db):
    secret = "sdk-token=private-value"
    task = _generated_task(db, suffix="safe-response")
    task.extra_data = {
        **dict(task.extra_data or {}),
        "battle_test": {"verdict": "error", "error": f"RuntimeError: {secret}"},
        "battle_test_history": [
            {"verdict": "error", "error": f"Earlier RuntimeError: {secret}"}
        ],
        "battle_test_provisioning": {
            "status": "retry_wait",
            "last_error": f"RuntimeError: {secret}",
        },
    }
    db.commit()

    payload = TaskResponse.model_validate(task).model_dump(mode="json")

    assert payload["extra_data"]["battle_test"]["error"] == (
        "assessment_task_battle_test_failed"
    )
    assert payload["extra_data"]["battle_test_history"][0]["error"] == (
        "assessment_task_battle_test_failed"
    )
    assert payload["extra_data"]["battle_test_provisioning"]["last_error"] == (
        "assessment_task_processing_failed"
    )
    assert secret not in str(payload)
    assert secret in str(task.extra_data)


def test_battle_repair_preserves_lineage_but_invalidates_exact_content_proof():
    failed_report = {"verdict": "fail", "run_id": "current-failure"}
    task = Task(
        id=77,
        organization_id=8,
        name="Broken generated task",
        task_key="repair-lineage",
        is_active=True,
        extra_data={
            "generated": True,
            "needs_review": False,
            "approved_by_user_id": 41,
            "approved_at": "2026-07-15T12:00:00+00:00",
            "repository_ready": {
                "verified_at": "2026-07-15T12:00:00+00:00",
                "repo_url": "https://example.test/old-content",
            },
            "provenance": {"source": "jd-generator", "request_id": "req-9"},
            "provenance_signature": "signed-origin",
            "generation_model": "generator-v3",
            "generated_request_id": "generated-9",
            "battle_test": failed_report,
            "battle_test_history": [
                {"verdict": "fail", "run_id": f"older-{index}"}
                for index in range(6)
            ],
        },
    )
    repaired_spec = {
        "task_id": "repair-lineage",
        "name": "Repaired generated task",
        "scenario": "Use the repaired candidate repository.",
        "repo_structure": {"files": {"README.md": "repaired"}},
        "evaluation_rubric": {},
        "decision_points": [{"id": "d1"}, {"id": "d2"}],
        "approved_by_user_id": 999,
        "approved_at": "forged",
        "repository_ready": {"repo_url": "forged"},
        "battle_test": {"verdict": "pass"},
    }

    apply_battle_test_repair(
        task,
        repaired_spec,
        feedback="Fix the repository",
        failed_report=failed_report,
        repair_attempts=1,
    )

    extra = task.extra_data
    assert task.is_active is False
    assert extra["generated"] is True
    assert extra["needs_review"] is True
    assert extra["provenance"]["request_id"] == "req-9"
    assert extra["provenance_signature"] == "signed-origin"
    assert extra["generation_model"] == "generator-v3"
    assert extra["generated_request_id"] == "generated-9"
    assert extra["decision_points"] == [{"id": "d1"}, {"id": "d2"}]
    assert "approved_by_user_id" not in extra
    assert "approved_at" not in extra
    assert "repository_ready" not in extra
    assert "battle_test" not in extra
    assert [report["run_id"] for report in extra["battle_test_history"]] == [
        "older-2",
        "older-3",
        "older-4",
        "older-5",
        "current-failure",
    ]
    assert extra["battle_test_provisioning"]["status"] == "pending"


def test_sweep_recovers_generated_task_with_missing_battle_report(db):
    task = _generated_task(db, suffix="lost-kick")

    with (
        patch("app.tasks.assessment_tasks.settings.AUTO_GENERATE_ASSESSMENT_TASKS", True),
        patch.object(generate_assessment_task_for_role, "delay"),
        patch.object(battle_test_generated_task, "delay") as battle_dispatch,
    ):
        summary = sweep_assessment_task_provisioning.run(limit=20)

    assert summary["battle_due"] == 1
    assert summary["battle_dispatched"] == 1
    battle_dispatch.assert_called_once_with(task.id, task.organization_id)


def test_sweep_recovers_lost_automatic_repair_kick(db):
    task = _generated_task(db, suffix="lost-repair-kick")
    extra = dict(task.extra_data)
    extra["battle_test"] = {
        "verdict": "fail",
        "error": None,
        "checks": [{"id": "tests_collect", "ok": False, "detail": "0 tests"}],
    }
    state = dict(extra["battle_test_provisioning"])
    state["status"] = "repair_pending"
    extra["battle_test_provisioning"] = state
    task.extra_data = extra
    db.commit()

    with (
        patch("app.tasks.assessment_tasks.settings.AUTO_GENERATE_ASSESSMENT_TASKS", True),
        patch.object(generate_assessment_task_for_role, "delay"),
        patch.object(battle_test_generated_task, "delay"),
        patch.object(
            repair_generated_task_after_battle_failure, "delay"
        ) as repair_dispatch,
    ):
        summary = sweep_assessment_task_provisioning.run(limit=20)

    assert summary["repair_due"] == 1
    assert summary["repair_dispatched"] == 1
    repair_dispatch.assert_called_once_with(task.id, task.organization_id)


def test_battle_test_worker_persists_report_and_terminal_state(db):
    task = _generated_task(db, suffix="success")
    report = {
        "verdict": "pass",
        "error": None,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
    }

    with patch(
        "app.services.task_battle_test.run_battle_test", return_value=report
    ) as run:
        result = battle_test_generated_task.run(task.id, task.organization_id)

    assert result["status"] == "done"
    assert result["verdict"] == "pass"
    run.assert_called_once()
    db.expire_all()
    persisted = db.query(Task).filter(Task.id == task.id).one()
    assert persisted.extra_data["battle_test"] == report
    assert (
        persisted.extra_data["battle_test_provisioning"]["status"]
        == "succeeded"
    )


def test_battle_test_infrastructure_failure_is_retryable_and_sweep_recoverable(db):
    task = _generated_task(db, suffix="retry")
    report = {
        "verdict": "fail",
        "error": "sandbox provider unavailable",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
    }

    with patch("app.services.task_battle_test.run_battle_test", return_value=report):
        battle_test_generated_task.push_request(retries=0)
        try:
            with patch.object(
                battle_test_generated_task,
                "retry",
                side_effect=Retry("retry"),
            ):
                with pytest.raises(Retry):
                    battle_test_generated_task.run(task.id, task.organization_id)
        finally:
            battle_test_generated_task.pop_request()

        # Move the durable cooldown to the past, then exercise the exhausted
        # boundary without waiting for wall-clock time.
        db.expire_all()
        persisted = db.query(Task).filter(Task.id == task.id).one()
        extra = dict(persisted.extra_data)
        state = dict(extra["battle_test_provisioning"])
        state["next_attempt_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        extra["battle_test_provisioning"] = state
        persisted.extra_data = extra
        db.commit()

        battle_test_generated_task.push_request(retries=3)
        try:
            result = battle_test_generated_task.run(task.id, task.organization_id)
        finally:
            battle_test_generated_task.pop_request()

    assert result["status"] == "failed"
    assert battle_test_generated_task.max_retries == 3
    db.expire_all()
    persisted = db.query(Task).filter(Task.id == task.id).one()
    state = persisted.extra_data["battle_test_provisioning"]
    assert state["status"] == "failed"
    assert state["attempts"] == 2
    assert state["next_attempt_at"]


def test_deterministic_battle_failure_queues_bounded_automatic_repair(db):
    task = _generated_task(db, suffix="structural-fail")
    report = {
        "verdict": "fail",
        "error": None,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "baseline": {"passed": 0, "failed": 0, "total": 0, "parse_error": True},
        "checks": [
            {"id": "tests_collect", "ok": False, "detail": "0 tests collected"}
        ],
    }

    with (
        patch("app.services.task_battle_test.run_battle_test", return_value=report),
        patch.object(
            repair_generated_task_after_battle_failure, "delay"
        ) as repair_dispatch,
    ):
        result = battle_test_generated_task.run(task.id, task.organization_id)

    assert result["status"] == "repair_queued"
    repair_dispatch.assert_called_once_with(task.id, task.organization_id)
    db.expire_all()
    persisted = db.query(Task).filter(Task.id == task.id).one()
    state = persisted.extra_data["battle_test_provisioning"]
    assert state["status"] == "repair_pending"
    assert "tests_collect" in state["last_error"]
    assert persisted.extra_data["battle_test"] == report


def test_battle_failure_does_not_kick_paid_repair_under_workspace_pause(db):
    task = _generated_task(db, suffix="workspace-held-battle")
    org = db.query(Organization).filter(Organization.id == task.organization_id).one()
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    db.commit()
    report = {
        "verdict": "fail",
        "error": None,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "checks": [{"id": "tests_collect", "ok": False, "detail": "0 tests"}],
    }

    with (
        patch("app.services.task_battle_test.run_battle_test", return_value=report),
        patch.object(
            repair_generated_task_after_battle_failure, "delay"
        ) as repair_dispatch,
    ):
        result = battle_test_generated_task.run(task.id, task.organization_id)

    assert result["status"] == "repair_deferred"
    assert result["reason"] == "workspace_paused"
    repair_dispatch.assert_not_called()
    db.expire_all()
    persisted = db.query(Task).filter(Task.id == task.id).one()
    assert persisted.extra_data["battle_test_provisioning"]["status"] == "repair_pending"


def test_paid_repair_delivery_is_deferred_under_workspace_pause(db):
    task = _generated_task(db, suffix="workspace-held-repair")
    extra = dict(task.extra_data)
    extra["battle_test"] = {
        "verdict": "fail",
        "error": None,
        "checks": [{"id": "tests_collect", "ok": False, "detail": "0 tests"}],
    }
    state = dict(extra["battle_test_provisioning"])
    state["status"] = "repair_pending"
    extra["battle_test_provisioning"] = state
    task.extra_data = extra
    org = db.query(Organization).filter(Organization.id == task.organization_id).one()
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    db.commit()

    with patch("app.services.task_spec_generator.revise_task_spec") as revise:
        result = repair_generated_task_after_battle_failure.run(
            task.id, task.organization_id
        )

    assert result == {"status": "deferred", "reason": "workspace_paused"}
    revise.assert_not_called()
    db.expire_all()
    persisted = db.query(Task).filter(Task.id == task.id).one()
    assert persisted.extra_data["battle_test_provisioning"]["status"] == "repair_pending"


def test_automatic_repair_reauthors_in_place_then_requests_retest(db):
    role = _role(db, suffix="auto-repair")
    failed_report = {
        "verdict": "fail",
        "error": None,
        "baseline": {"passed": 0, "failed": 0, "total": 0},
        "checks": [
            {"id": "tests_collect", "ok": False, "detail": "no tests"}
        ],
    }
    extra = initialize_battle_test_provisioning(
        {"generated": True, "needs_review": True}
    )
    extra["battle_test"] = failed_report
    state = dict(extra["battle_test_provisioning"])
    state["status"] = "repair_pending"
    extra["battle_test_provisioning"] = state
    task = Task(
        organization_id=role.organization_id,
        name="Broken draft",
        task_key="auto_repair_task",
        role="platform_engineer",
        duration_minutes=30,
        is_template=False,
        is_active=False,
        extra_data=extra,
    )
    role.tasks.append(task)
    db.commit()
    repaired_spec = {
        "task_id": "auto_repair_task",
        "name": "Repaired draft",
        "role": "platform_engineer",
        "duration_minutes": 30,
        "scenario": "A repaired production scenario.",
        "calibration_prompt": "How would you begin?",
        "repo_structure": {"name": "auto-repair-task", "files": {}},
        "evaluation_rubric": {},
    }
    provider_sessions = []
    provider_session_factory = sessionmaker(bind=db.get_bind())

    def tracked_session():
        session = provider_session_factory()
        provider_sessions.append(session)
        return session

    def revise_without_transaction(**_kwargs):
        assert provider_sessions
        assert provider_sessions[-1].in_transaction() is False
        return GeneratedSpecResult(
            spec=repaired_spec, valid=True, errors=[], attempts=1
        )

    with (
        patch("app.platform.database.SessionLocal", side_effect=tracked_session),
        patch("app.tasks.assessment_tasks.settings.ANTHROPIC_API_KEY", "sk-test"),
        patch(
            "app.services.task_spec_generator.revise_task_spec",
            side_effect=revise_without_transaction,
        ) as revise,
        patch(
            "app.services.task_provisioning_service._provision_repo_best_effort"
        ),
        patch.object(battle_test_generated_task, "delay") as retest,
    ):
        result = repair_generated_task_after_battle_failure.run(
            task.id, task.organization_id
        )

    assert result["status"] == "repaired"
    assert result["repair_attempts"] == 1
    revise.assert_called_once()
    assert "tests_collect" in revise.call_args.kwargs["feedback"]
    retest.assert_called_once_with(task.id, task.organization_id)
    db.expire_all()
    persisted = db.query(Task).filter(Task.id == task.id).one()
    assert persisted.name == "Repaired draft"
    assert persisted.calibration_prompt is None
    assert "battle_test" not in persisted.extra_data
    assert persisted.extra_data["battle_test_provisioning"]["status"] == "pending"
    assert persisted.extra_data["battle_test_provisioning"]["repair_attempts"] == 1
    assert persisted.extra_data["battle_test_history"] == [failed_report]


def test_generated_repository_provider_runs_without_an_orm_transaction(db):
    from app.services.task_provisioning_service import _provision_repo_best_effort

    task = _generated_task(db, suffix="repo-detached")
    provider_boundaries: list[str] = []

    def recreate_without_transaction(_task):
        assert db.in_transaction() is False
        provider_boundaries.append("filesystem")
        return "/tmp/repo-detached"

    def create_without_transaction(_self, _task):
        assert db.in_transaction() is False
        provider_boundaries.append("github")
        return "repo-detached"

    with (
        patch(
            "app.services.task_repo_service.recreate_task_main_repo",
            side_effect=recreate_without_transaction,
        ),
        patch(
            "app.services.assessment_repository_service."
            "AssessmentRepositoryService.create_template_repo",
            side_effect=create_without_transaction,
            autospec=True,
        ),
    ):
        _provision_repo_best_effort(db, task)

    assert provider_boundaries == ["filesystem", "github"]


def test_battle_failure_after_two_reauthor_attempts_becomes_hitl_boundary(db):
    task = _generated_task(db, suffix="repair-cap")
    extra = dict(task.extra_data)
    state = dict(extra["battle_test_provisioning"])
    state["repair_attempts"] = 2
    extra["battle_test_provisioning"] = state
    task.extra_data = extra
    db.commit()
    report = {
        "verdict": "fail",
        "error": None,
        "baseline": {},
        "checks": [{"id": "repo_boots", "ok": False, "detail": "failed"}],
    }

    with (
        patch("app.services.task_battle_test.run_battle_test", return_value=report),
        patch.object(
            repair_generated_task_after_battle_failure, "delay"
        ) as repair_dispatch,
    ):
        result = battle_test_generated_task.run(task.id, task.organization_id)

    assert result["status"] == "repair_exhausted"
    repair_dispatch.assert_not_called()
    db.expire_all()
    persisted = db.query(Task).filter(Task.id == task.id).one()
    assert (
        persisted.extra_data["battle_test_provisioning"]["status"]
        == "repair_exhausted"
    )
