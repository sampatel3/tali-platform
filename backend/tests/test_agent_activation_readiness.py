from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.assessment_experiment import (
    EXPERIMENT_STATUS_ACTIVE,
    AssessmentExperiment,
    AssessmentExperimentArm,
)
from app.models.ats_stage_map import AtsStageMap
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
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
        "BULLHORN_ENABLED": False,
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
        ),
    )

    assert result["ready"] is False
    assert {reason["code"] for reason in result["reasons"]} == {
        "native_apply_disabled",
        "model_unconfigured",
        "assessment_execution_unconfigured",
        "assessment_email_unconfigured",
    }


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_production_readiness_passes_when_used_path_is_wired(_beat, db):
    assert activation_readiness(
        _role(db, source="requisition", active_task=True),
        settings_obj=_settings(GITHUB_TOKEN="", GITHUB_MOCK_MODE=True),
    )["ready"] is True


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_bullhorn_linked_requisition_does_not_require_native_public_apply(_beat, db):
    role = _role(db, source="requisition")
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    role.bullhorn_job_order_id = "job-42"
    db.flush()

    result = activation_readiness(
        role,
        settings_obj=_settings(
            ATS_PUBLIC_APPLY_ENABLED=False,
            BULLHORN_ENABLED=True,
        ),
        auto_skip_assessment=True,
        auto_send_assessment=False,
        auto_resend_assessment=False,
        auto_advance=False,
        auto_reject=False,
        auto_reject_pre_screen=False,
    )

    assert result["ready"] is True
    assert "native_apply_disabled" not in {
        reason["code"] for reason in result["reasons"]
    }


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_checks_the_selected_task_snapshot(_beat, db, monkeypatch):
    role = _role(db, source="requisition", active_task=True)
    task = role.tasks[0]
    task.repo_structure = {"files": {"README.md": "assessment"}}
    monkeypatch.setattr(
        "app.services.agent_activation_readiness.task_repository_readiness",
        lambda selected, *, settings_obj: (
            False,
            "workspace manifest is unsafe",
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
    assert "manifest is unsafe" in reason["detail"]


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_uses_same_patch_assessment_skip_override(_beat, db):
    role = _role(db, source="requisition", active_task=True)
    result = activation_readiness(
        role,
        settings_obj=_settings(RESEND_API_KEY=""),
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


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_uses_incoming_monthly_cap_without_mutating_role(_beat, db):
    role = _role(db, active_task=True)
    role.monthly_usd_budget_cents = 1
    db.flush()

    result = activation_readiness(
        role,
        settings_obj=_settings(),
        monthly_usd_budget_cents=5_000,
    )

    assert result["ready"] is True
    assert role.monthly_usd_budget_cents == 1


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_readiness_uses_incoming_auto_advance_for_workable_writeback(_beat, db):
    role = _role(db, active_task=True)
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "ready"
    org.workable_config = {"workable_writeback": True}
    role.workable_job_id = "workable-role"
    role.auto_advance = True
    db.flush()

    stale_policy = activation_readiness(role, settings_obj=_settings())
    incoming_policy = activation_readiness(
        role,
        settings_obj=_settings(),
        auto_advance=False,
    )

    assert "workable_interview_stage_missing" in {
        reason["code"] for reason in stale_policy["reasons"]
    }
    assert incoming_policy["ready"] is True
    assert role.auto_advance is True


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_workable_readiness_uses_sole_cached_interview_kind_stage(_beat, db):
    role = _role(db, active_task=True)
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "ready"
    org.workable_config = {"workable_writeback": True}
    role.workable_job_id = "workable-role"
    role.workable_stages = [
        {"slug": "applied", "name": "Applied", "kind": "sourced"},
        {
            "slug": "final-interview",
            "name": "Final interview",
            "kind": "interview",
        },
    ]
    role.auto_advance = True
    db.flush()

    assert activation_readiness(role, settings_obj=_settings())["ready"] is True


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_related_auto_advance_uses_owner_workable_stage_mapping(_beat, db):
    owner = _role(db)
    org = db.query(Organization).filter(
        Organization.id == owner.organization_id
    ).one()
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "ready"
    org.workable_config = {"workable_writeback": True}
    owner.workable_job_id = "workable-owner"
    owner.workable_stages = [
        {"slug": "applied", "name": "Applied", "kind": "sourced"},
        {
            "slug": "final-interview",
            "name": "Final interview",
            "kind": "interview",
        },
    ]
    related = Role(
        organization_id=org.id,
        name="Related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        auto_advance=True,
    )
    db.add(related)
    db.flush()

    result = activation_readiness(
        related,
        settings_obj=_settings(),
        auto_skip_assessment=True,
    )

    assert result["ready"] is True
    assert not any(
        reason["code"] == "workable_interview_stage_missing"
        for reason in result["reasons"]
    )


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_workable_readiness_requires_inbound_connection_when_writes_are_off(
    _beat, db
):
    role = _role(db)
    role.workable_job_id = "workable-role"
    db.flush()

    result = activation_readiness(
        role,
        settings_obj=_settings(),
        auto_skip_assessment=True,
        auto_send_assessment=False,
        auto_resend_assessment=False,
        auto_advance=False,
        auto_reject=False,
        auto_reject_pre_screen=False,
    )

    assert "workable_connection_required" in {
        reason["code"] for reason in result["reasons"]
    }


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_workable_readiness_surfaces_ambiguous_cached_interview_stages(_beat, db):
    role = _role(db, active_task=True)
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "ready"
    org.workable_config = {"workable_writeback": True}
    role.workable_job_id = "workable-role"
    role.workable_stages = [
        {"slug": "technical", "name": "Technical", "kind": "interview"},
        {"slug": "final", "name": "Final", "kind": "interview"},
    ]
    role.auto_advance = True
    db.flush()

    result = activation_readiness(role, settings_obj=_settings())

    reason = next(
        item
        for item in result["reasons"]
        if item["code"] == "workable_interview_stage_missing"
    )
    assert "Multiple cached Workable stages" in reason["detail"]
    assert "Choose the intended" in reason["detail"]


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_workable_readiness_requires_unique_assessment_invite_stage(_beat, db):
    role = _role(db, active_task=True)
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "ready"
    org.workable_config = {"workable_writeback": True}
    role.workable_job_id = "workable-role"
    db.flush()

    missing = activation_readiness(
        role,
        settings_obj=_settings(),
        auto_send_assessment=True,
        auto_resend_assessment=False,
        auto_advance=False,
    )
    assert "workable_invite_stage_missing" in {
        reason["code"] for reason in missing["reasons"]
    }

    role.workable_stages = [
        {"slug": "assessment", "name": "Assessment", "kind": "assessment"}
    ]
    db.flush()
    assert activation_readiness(
        role,
        settings_obj=_settings(),
        auto_send_assessment=True,
        auto_resend_assessment=False,
        auto_advance=False,
    )["ready"] is True


def _connect_bullhorn(org, role) -> None:
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    role.source = "bullhorn"
    role.bullhorn_job_order_id = "job-42"


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_bullhorn_readiness_requires_every_enabled_action_mapping(_beat, db):
    role = _role(db, source="bullhorn", active_task=True)
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    _connect_bullhorn(org, role)
    role.auto_send_assessment = True
    role.auto_resend_assessment = True
    role.auto_advance = True
    role.auto_reject_pre_screen = True
    db.flush()

    result = activation_readiness(
        role,
        settings_obj=_settings(BULLHORN_ENABLED=True),
    )

    codes = {reason["code"] for reason in result["reasons"]}
    assert {
        "bullhorn_assessment_stage_mapping_required",
        "bullhorn_advance_stage_mapping_required",
        "bullhorn_reject_stage_mapping_required",
    }.issubset(codes)
    assert all(
        "Settings → Integrations → Bullhorn" in reason["detail"]
        for reason in result["reasons"]
        if reason["code"].startswith("bullhorn_")
    )


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_bullhorn_readiness_passes_with_connection_and_action_maps(_beat, db):
    role = _role(db, source="bullhorn", active_task=True)
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    _connect_bullhorn(org, role)
    role.auto_send_assessment = True
    role.auto_resend_assessment = True
    role.auto_advance = True
    role.auto_reject_pre_screen = True
    db.add_all(
        [
            AtsStageMap(
                org_id=org.id,
                ats="bullhorn",
                remote_status="Assessment Sent",
                taali_stage="invited",
                is_reject=False,
            ),
            AtsStageMap(
                org_id=org.id,
                ats="bullhorn",
                remote_status="Client Interview",
                taali_stage="advanced",
                is_reject=False,
            ),
            AtsStageMap(
                org_id=org.id,
                ats="bullhorn",
                remote_status="Rejected",
                taali_stage="review",
                is_reject=True,
            ),
        ]
    )
    db.flush()

    assert activation_readiness(
        role,
        settings_obj=_settings(BULLHORN_ENABLED=True),
    )["ready"] is True


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_bullhorn_readiness_surfaces_feature_and_connection_hitl(_beat, db):
    role = _role(db, source="bullhorn", active_task=True)
    role.bullhorn_job_order_id = "job-42"
    result = activation_readiness(role, settings_obj=_settings())

    codes = {reason["code"] for reason in result["reasons"]}
    assert "bullhorn_feature_disabled" in codes
    assert "bullhorn_connection_required" in codes


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_dual_linked_role_uses_workable_precedence_only(_beat, db):
    role = _role(db, source="bullhorn")
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "ready"
    org.workable_config = {
        "workable_writeback": True,
        "interview_stage_name": "final-interview",
    }
    role.workable_job_id = "workable-live"
    role.bullhorn_job_order_id = "bullhorn-stale"
    role.auto_advance = True
    db.flush()

    result = activation_readiness(
        role,
        settings_obj=_settings(BULLHORN_ENABLED=False),
        auto_skip_assessment=True,
        auto_send_assessment=False,
        auto_resend_assessment=False,
    )

    assert result["ready"] is True
    assert not any(
        reason["code"].startswith("bullhorn_") for reason in result["reasons"]
    )


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_bullhorn_readiness_uses_incoming_reject_policy(_beat, db):
    role = _role(db, source="bullhorn")
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    _connect_bullhorn(org, role)
    role.auto_reject_pre_screen = False
    db.flush()

    result = activation_readiness(
        role,
        settings_obj=_settings(BULLHORN_ENABLED=True),
        auto_skip_assessment=True,
        auto_send_assessment=False,
        auto_resend_assessment=False,
        auto_advance=False,
        auto_reject_pre_screen=True,
    )

    assert "bullhorn_reject_stage_mapping_required" in {
        reason["code"] for reason in result["reasons"]
    }
    assert role.auto_reject_pre_screen is False


@patch(
    "app.services.agent_worker_health.worker_beat_status",
    return_value={"ready": True, "reason": None},
)
def test_bullhorn_readiness_rejects_ambiguous_reject_write_target(_beat, db):
    role = _role(db, source="bullhorn")
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    _connect_bullhorn(org, role)
    db.add_all(
        [
            AtsStageMap(
                org_id=org.id,
                ats="bullhorn",
                remote_status="Rejected A",
                taali_stage="review",
                is_reject=True,
            ),
            AtsStageMap(
                org_id=org.id,
                ats="bullhorn",
                remote_status="Rejected B",
                taali_stage="review",
                is_reject=True,
            ),
        ]
    )
    db.flush()

    result = activation_readiness(
        role,
        settings_obj=_settings(BULLHORN_ENABLED=True),
        auto_skip_assessment=True,
        auto_send_assessment=False,
        auto_resend_assessment=False,
        auto_advance=False,
        auto_reject=True,
    )

    assert "bullhorn_reject_stage_mapping_required" in {
        reason["code"] for reason in result["reasons"]
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
                    "anthropic_probe_ok": True,
                    "usage_meter_live": True,
                    "bullhorn_enabled": False,
                },
            },
            "scoring": {
                "ready": True,
                "capabilities": {
                    "anthropic_configured": True,
                    "anthropic_probe_ok": True,
                    "usage_meter_live": True,
                },
            },
        },
    },
)
def test_bullhorn_readiness_requires_worker_feature_flag(_beat, db):
    role = _role(db, source="bullhorn")
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    _connect_bullhorn(org, role)
    db.flush()

    result = activation_readiness(
        role,
        settings_obj=_settings(BULLHORN_ENABLED=True),
        auto_skip_assessment=True,
        auto_send_assessment=False,
        auto_resend_assessment=False,
        auto_advance=False,
    )

    assert "bullhorn_worker_feature_disabled" in {
        reason["code"] for reason in result["reasons"]
    }


def test_local_readiness_does_not_require_external_services(db):
    local = _settings(
        DEPLOYMENT_ENV="development",
        FRONTEND_URL="http://localhost:5173",
        ANTHROPIC_API_KEY="",
        RESEND_API_KEY="",
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
                    "anthropic_probe_ok": True,
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
    assessment_worker = next(
        reason
        for reason in result["reasons"]
        if reason["code"] == "assessment_worker_unconfigured"
    )
    assert "GitHub" not in assessment_worker["detail"]


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
                    "anthropic_probe_ok": True,
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
