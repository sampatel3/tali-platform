from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.assessment_experiment import (
    EXPERIMENT_STATUS_ACTIVE,
    AssessmentExperiment,
    AssessmentExperimentArm,
)
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.services.agent_activation_readiness import activation_readiness


@pytest.fixture(autouse=True)
def _task_repositories_ready(monkeypatch):
    """Most tests isolate other readiness rails; make repo state explicit."""
    monkeypatch.setattr(
        "app.services.agent_activation_readiness.task_repository_readiness",
        lambda _task, *, settings_obj: (True, None),
    )


def _settings(**overrides):
    values = {
        "DEPLOYMENT_ENV": "production",
        "FRONTEND_URL": "https://app.taali.test",
        "SENTRY_DSN": None,
        "ATS_PUBLIC_APPLY_ENABLED": True,
        "USAGE_METER_LIVE": True,
        "ANTHROPIC_API_KEY": "anthropic-live",
        "ANTHROPIC_ADMIN_API_KEY": "",
        "ANTHROPIC_WORKSPACE_KEYS_ENABLED": False,
        "RESEND_API_KEY": "resend-live",
        "E2B_API_KEY": "e2b-live",
        "GITHUB_TOKEN": "github-live",
        "GITHUB_MOCK_MODE": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _role(db, *, source="manual", active_task=False):
    org = Organization(
        name="Ready Org",
        slug=f"ready-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Role", source=source)
    db.add(role)
    db.flush()
    if active_task:
        task = Task(organization_id=org.id, name="Task", is_active=True)
        db.add(task)
        db.flush()
        role.tasks.append(task)
        db.flush()
    return role


def _add_active_task(db, role, *, name: str) -> Task:
    task = Task(
        organization_id=role.organization_id,
        name=name,
        is_active=True,
    )
    db.add(task)
    db.flush()
    role.tasks.append(task)
    db.flush()
    return task


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_production_readiness_checks_role_specific_dependencies(_beat, db):
    role = _role(db, source="requisition", active_task=True)
    result = activation_readiness(
        role,
        settings_obj=_settings(
            ATS_PUBLIC_APPLY_ENABLED=False,
            ANTHROPIC_API_KEY="",
            RESEND_API_KEY="",
            E2B_API_KEY="",
            GITHUB_TOKEN="",
            GITHUB_MOCK_MODE=True,
        ),
    )

    assert result["ready"] is False
    assert {reason["code"] for reason in result["reasons"]} == {
        "native_apply_disabled",
        "model_unconfigured",
        "assessment_execution_unconfigured",
        "assessment_email_unconfigured",
        "assessment_repository_unconfigured",
    }


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_production_readiness_passes_when_used_path_is_wired(_beat, db):
    assert activation_readiness(
        _role(db, source="requisition", active_task=True),
        settings_obj=_settings(),
    )["ready"] is True


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_checks_the_selected_task_repository(_beat, db, monkeypatch):
    role = _role(db, source="requisition", active_task=True)
    task = role.tasks[0]
    task.repo_structure = {"files": {"README.md": "assessment"}}
    monkeypatch.setattr(
        "app.services.agent_activation_readiness.task_repository_readiness",
        lambda selected, *, settings_obj: (
            False,
            "template repository has no main branch",
        ) if selected.id == task.id else (True, None),
    )

    result = activation_readiness(role, settings_obj=_settings())

    assert result["ready"] is False
    reason = next(
        item
        for item in result["reasons"]
        if item["code"] == "assessment_task_repository_unready"
    )
    assert f"id={task.id}" in reason["detail"]
    assert "no main branch" in reason["detail"]


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_uses_same_patch_assessment_skip_override(_beat, db):
    role = _role(db, source="requisition", active_task=True)
    result = activation_readiness(
        role,
        settings_obj=_settings(RESEND_API_KEY="", GITHUB_TOKEN=""),
        auto_skip_assessment=True,
    )
    assert result["ready"] is True


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_requires_the_shared_key_used_by_scoring_gate(_beat, db):
    result = activation_readiness(
        _role(db),
        settings_obj=_settings(
            ANTHROPIC_API_KEY="",
            ANTHROPIC_ADMIN_API_KEY="admin-only",
            ANTHROPIC_WORKSPACE_KEYS_ENABLED=True,
        ),
        auto_skip_assessment=True,
    )
    assert result["ready"] is False
    assert {reason["code"] for reason in result["reasons"]} == {
        "model_unconfigured"
    }


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_refuses_shadow_meter_even_with_emergency_boot_override(_beat, db):
    result = activation_readiness(
        _role(db),
        settings_obj=_settings(
            USAGE_METER_LIVE=False,
            USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY=True,
        ),
        auto_skip_assessment=True,
    )

    assert result["ready"] is False
    assert {reason["code"] for reason in result["reasons"]} == {
        "usage_meter_not_live"
    }


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_requires_task_approval_or_explicit_assessment_skip(_beat, db):
    role = _role(db, source="requisition", active_task=False)

    blocked = activation_readiness(role, settings_obj=_settings())
    skipped = activation_readiness(
        role,
        settings_obj=_settings(),
        auto_skip_assessment=True,
    )

    assert blocked["ready"] is False
    assert {reason["code"] for reason in blocked["reasons"]} == {
        "assessment_task_approval_required"
    }
    assert skipped["ready"] is True


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_rejects_ambiguous_active_tasks_without_experiment(_beat, db):
    role = _role(db, active_task=True)
    _add_active_task(db, role, name="Second task")

    result = activation_readiness(role, settings_obj=_settings())

    assert result["ready"] is False
    reason = next(
        item
        for item in result["reasons"]
        if item["code"] == "assessment_task_ambiguous"
    )
    assert "2 active linked tasks" in reason["detail"]
    assert "no active experiment" in reason["detail"]


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_accepts_multiple_tasks_resolved_by_valid_experiment(_beat, db):
    role = _role(db, active_task=True)
    first = role.tasks[0]
    second = _add_active_task(db, role, name="Second task")
    experiment = AssessmentExperiment(
        organization_id=role.organization_id,
        role_id=role.id,
        key=f"task-ab-{role.id}",
        name="Task A/B",
        status=EXPERIMENT_STATUS_ACTIVE,
        salt="stable-test-salt",
    )
    db.add(experiment)
    db.flush()
    db.add_all(
        [
            AssessmentExperimentArm(
                experiment_id=experiment.id,
                arm_key="A",
                task_id=first.id,
                weight=1,
                is_active=True,
            ),
            AssessmentExperimentArm(
                experiment_id=experiment.id,
                arm_key="B",
                task_id=second.id,
                weight=1,
                is_active=True,
            ),
        ]
    )
    db.flush()

    assert activation_readiness(role, settings_obj=_settings())["ready"] is True


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_blocks_an_unfunded_agent_before_turn_on(_beat, db):
    role = _role(db, active_task=True)
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.credits_balance = 0
    db.flush()

    result = activation_readiness(role, settings_obj=_settings())

    assert result["ready"] is False
    reason = next(
        item for item in result["reasons"]
        if item["code"] == "billing_credits_insufficient"
    )
    assert "0 are available" in reason["detail"]


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_requires_room_under_role_monthly_cap(_beat, db):
    role = _role(db, active_task=True)
    role.monthly_usd_budget_cents = 1  # 10K credits, below one funnel pass
    db.flush()

    result = activation_readiness(role, settings_obj=_settings())

    assert result["ready"] is False
    reason = next(
        item
        for item in result["reasons"]
        if item["code"] == "role_monthly_budget_insufficient"
    )
    assert "under this role's monthly cap" in reason["detail"]


def test_local_readiness_does_not_require_external_services(db):
    local = _settings(
        DEPLOYMENT_ENV="development",
        FRONTEND_URL="http://localhost:5173",
        ANTHROPIC_API_KEY="",
        RESEND_API_KEY="",
        GITHUB_TOKEN="",
    )
    assert activation_readiness(_role(db), settings_obj=local) == {
        "ready": True,
        "production": False,
        "reasons": [],
    }


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={
        "ready": False,
        "reason": "heartbeat_missing",
        "failed_queues": ["scoring"],
        "queues": {
            "celery": {"ready": True},
            "scoring": {"ready": False, "reason": "heartbeat_missing"},
        },
    },
)
def test_production_readiness_identifies_missing_required_queue(_beat, db):
    result = activation_readiness(
        _role(db),
        settings_obj=_settings(),
        auto_skip_assessment=True,
    )

    assert result["ready"] is False
    assert result["reasons"] == [
        {
            "code": "worker_unready",
            "detail": "heartbeat_missing (queues: scoring)",
        }
    ]
    assert result["worker"]["failed_queues"] == ["scoring"]


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={
        "ready": True,
        "reason": None,
        "capability_reporting": True,
        "queues": {
            "celery": {
                "ready": True,
                "capabilities": {
                    "anthropic_configured": True,
                    "usage_meter_live": True,
                    "e2b_configured": False,
                    "resend_configured": True,
                    "resend_probe_ok": False,
                    "github_configured": True,
                    "github_mock_mode": False,
                    "anthropic_probe_ok": True,
                    "github_probe_ok": False,
                },
            },
            "scoring": {
                "ready": True,
                "capabilities": {
                    "anthropic_configured": False,
                    "usage_meter_live": False,
                    "anthropic_probe_ok": False,
                },
            },
        },
    },
)
def test_readiness_uses_worker_process_capabilities_not_only_web_env(_beat, db):
    result = activation_readiness(
        _role(db, active_task=True),
        settings_obj=_settings(),
    )

    assert result["ready"] is False
    assert {reason["code"] for reason in result["reasons"]} >= {
        "worker_model_unconfigured",
        "worker_usage_meter_not_live",
        "worker_model_probe_failed",
        "assessment_worker_unconfigured",
    }


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={
        "ready": True,
        "reason": None,
        "capability_reporting": True,
        "queues": {
            "celery": {
                "ready": True,
                "capabilities": {
                    "anthropic_configured": True,
                    "usage_meter_live": True,
                    "e2b_configured": True,
                    "resend_configured": True,
                    "resend_probe_ok": False,
                    "github_configured": True,
                    "github_mock_mode": False,
                    "anthropic_probe_ok": True,
                    "github_probe_ok": True,
                },
            },
            "scoring": {
                "ready": True,
                "capabilities": {
                    "anthropic_configured": True,
                    "usage_meter_live": True,
                    "anthropic_probe_ok": True,
                },
            },
        },
    },
)
def test_readiness_requires_verified_resend_delivery_on_assessment_path(_beat, db):
    result = activation_readiness(
        _role(db, active_task=True),
        settings_obj=_settings(),
    )

    assert result["ready"] is False
    reason = next(
        item
        for item in result["reasons"]
        if item["code"] == "assessment_worker_unconfigured"
    )
    assert "verified Resend delivery access" in reason["detail"]
