from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.actions._workable_decision_summary import try_workable_advance
from app.actions.types import Actor
from app.components.integrations.bullhorn import sync_jobs as bullhorn_sync_jobs
from app.components.integrations.workable.service import WorkableService
from app.components.integrations.workable.sync_service import WorkableSyncService
from app.domains.workable_provider import service as workable_provider_service
from app.domains.identity_access.organization_serialization import (
    merge_ai_tooling_config,
    resolved_ai_tooling_config,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.org_criterion import BUCKET_MUST, OrganizationCriterion
from app.models.organization import Organization
from app.models.role import Role
from app.schemas.organization import AgentDefaults, OrgUpdate
from app.services.agent_policy_settings import (
    activation_policy_values,
    apply_workspace_agent_defaults,
    automation_enabled_for_decision,
    effective_agent_policy,
)
from app.services.role_brief_service import (
    create_brief,
    materialize_brief_to_role,
    update_brief_fields,
)
from tests.conftest import auth_headers


def _org(db, *, suffix: str) -> Organization:
    org = Organization(name=f"Org {suffix}", slug=f"agent-policy-{suffix}")
    db.add(org)
    db.flush()
    return org


def _application(db, *, org: Organization, role: Role, source: str) -> CandidateApplication:
    candidate = Candidate(
        organization_id=org.id,
        email=f"{source}-{role.id}@example.test",
        full_name="Candidate",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        source=source,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="system",
        application_outcome="open",
        cv_text="Relevant experience",
    )
    db.add(app)
    db.flush()
    return app


def test_workspace_defaults_seed_role_but_never_turn_it_on(db):
    org = _org(db, suffix="seed")
    org.default_role_budget_cents = 8_500
    org.default_score_threshold = 71
    org.ai_tooling_config = {
        "agent_defaults": {
            "enabled": True,
            "auto_send_assessment": True,
            "auto_resend_assessment": False,
            "auto_advance": True,
            "auto_reject_pre_screen": True,
            "auto_skip_assessment": False,
        }
    }
    role = Role(organization_id=org.id, name="Seeded")

    apply_workspace_agent_defaults(role, org)

    assert role.agentic_mode_enabled is None or role.agentic_mode_enabled is False
    assert role.monthly_usd_budget_cents == 8_500
    assert role.score_threshold == 71
    assert role.auto_reject_threshold_mode == "manual"
    assert role.auto_send_assessment is True
    assert role.auto_resend_assessment is False
    assert role.auto_advance is True
    assert role.auto_reject_pre_screen is True
    assert effective_agent_policy(role)["auto_reject"] is False
    assert effective_agent_policy(role)["fixed_human_review"] == [
        "assessment_reject",
        "llm_reject",
        "interview",
        "offer",
        "hire",
    ]


def test_unsaved_workspace_uses_concrete_safe_platform_defaults(db):
    org = _org(db, suffix="platform-defaults")
    assert org.ai_tooling_config is None
    role = Role(organization_id=org.id, name="Untouched workspace")

    apply_workspace_agent_defaults(role, org)

    assert role.agentic_mode_enabled is None or role.agentic_mode_enabled is False
    assert role.monthly_usd_budget_cents == 5_000
    assert role.auto_send_assessment is False
    assert role.auto_resend_assessment is False
    assert role.auto_advance is False
    assert role.auto_promote is False
    assert role.auto_reject_pre_screen is True
    assert role.auto_skip_assessment is True
    effective = effective_agent_policy(role)
    assert effective["auto_send_assessment"] is False
    assert effective["auto_resend_assessment"] is False
    assert effective["auto_advance"] is False
    assert effective["auto_reject_pre_screen"] is True


def test_legacy_zero_workspace_budget_normalizes_to_activatable_default(db):
    org = _org(db, suffix="legacy-zero-budget")
    org.default_role_budget_cents = 0
    org.ai_tooling_config = {"agent_defaults": {"budget_cents": 0}}
    role = Role(organization_id=org.id, name="Legacy zero budget")

    apply_workspace_agent_defaults(role, org)

    assert role.monthly_usd_budget_cents == 5_000
    assert resolved_ai_tooling_config(org)["agent_defaults"]["budget_cents"] == 5_000


def test_legacy_default_block_resolves_to_same_safe_platform_policy(db):
    org = _org(db, suffix="legacy-default-block")
    org.ai_tooling_config = {"agent_defaults": AgentDefaults().model_dump()}
    role = Role(organization_id=org.id, name="Legacy defaults")

    apply_workspace_agent_defaults(role, org)

    assert role.auto_send_assessment is False
    assert role.auto_resend_assessment is False
    assert role.auto_advance is False
    assert role.auto_promote is False
    assert role.auto_reject_pre_screen is True
    assert role.auto_skip_assessment is True


def test_granular_runtime_uses_legacy_fallback_and_concrete_override():
    role = Role(organization_id=1, name="Compatibility", auto_promote=True)
    assert automation_enabled_for_decision(role, "send_assessment") is True
    assert automation_enabled_for_decision(role, "advance_to_interview") is True

    role.auto_send_assessment = False
    role.auto_advance = False
    assert automation_enabled_for_decision(role, "send_assessment") is False
    assert automation_enabled_for_decision(role, "resend_assessment_invite") is True
    assert automation_enabled_for_decision(role, "advance_to_interview") is False


def test_activation_preserves_concrete_role_choices():
    role = Role(
        organization_id=1,
        name="Mixed",
        auto_promote=True,
        auto_send_assessment=True,
        auto_resend_assessment=False,
        auto_advance=False,
    )
    policy = activation_policy_values(role, {"auto_promote": True})
    assert policy == {
        "auto_send_assessment": True,
        "auto_resend_assessment": False,
        "auto_advance": False,
        "auto_promote": False,
    }


def test_workspace_agent_defaults_patch_deep_merges_existing_choices(db):
    org = _org(db, suffix="merge")
    org.ai_tooling_config = {
        "agent_defaults": {
            "auto_send_assessment": True,
            "auto_resend_assessment": True,
            "auto_advance": True,
            "auto_reject_pre_screen": False,
        }
    }
    patch = OrgUpdate(
        ai_tooling_config={
            "agent_defaults": {"auto_send_assessment": False}
        }
    )

    merged = merge_ai_tooling_config(org, patch)["agent_defaults"]

    assert merged["auto_send_assessment"] is False
    assert merged["auto_resend_assessment"] is True
    assert merged["auto_advance"] is True
    assert merged["auto_reject_pre_screen"] is False


def test_requisition_role_inherits_workspace_criteria_and_policy(db):
    org = _org(db, suffix="requisition")
    org.ai_tooling_config = {
        "agent_defaults": {
            "auto_send_assessment": True,
            "auto_resend_assessment": False,
            "auto_advance": True,
        }
    }
    workspace = OrganizationCriterion(
        organization_id=org.id,
        text="Customer-facing ownership",
        bucket=BUCKET_MUST,
        ordering=0,
        weight=1.0,
    )
    db.add(workspace)
    db.flush()
    brief = create_brief(db, organization_id=org.id)
    update_brief_fields(db, brief, title="Platform lead", must_haves=["Python"])

    role = materialize_brief_to_role(db, brief, mark_applied=False)
    rows = (
        db.query(OrganizationCriterion,)
        .filter(OrganizationCriterion.organization_id == org.id)
        .all()
    )
    copied = [
        criterion
        for criterion in role.criteria
        if criterion.deleted_at is None
        and criterion.org_criterion_id == workspace.id
    ]

    assert rows
    assert len(copied) == 1
    assert copied[0].text == "Customer-facing ownership"
    assert role.auto_send_assessment is True
    assert role.auto_resend_assessment is False
    assert role.auto_advance is True
    assert role.agentic_mode_enabled is False


def _saved_constructor_policy(org: Organization) -> None:
    org.ai_tooling_config = {
        "agent_defaults": {
            "auto_send_assessment": False,
            "auto_resend_assessment": True,
            "auto_advance": False,
            "auto_reject_pre_screen": True,
            "auto_skip_assessment": False,
        }
    }


def _assert_constructor_policy(role: Role) -> None:
    assert role.agentic_mode_enabled is False
    assert role.auto_send_assessment is False
    assert role.auto_resend_assessment is True
    assert role.auto_advance is False
    assert role.auto_reject_pre_screen is True
    # Constructors have no linked task yet, so assessment skipping is forced
    # until the first active task is assigned.
    assert role.auto_skip_assessment is True


def test_workable_sync_applies_defaults_only_when_creating_role(db):
    org = _org(db, suffix="workable-constructor")
    _saved_constructor_policy(org)
    service = WorkableSyncService(
        WorkableService(access_token="token", subdomain="example")
    )
    job = {"shortcode": "AGENT-DEFAULTS", "title": "Platform Engineer"}

    with (
        patch.object(service, "_job_details_for_role", return_value={}),
        patch.object(service, "_refresh_role_stages", return_value=None),
        patch(
            "app.components.integrations.workable.sync_service._format_job_spec_from_api",
            return_value="",
        ),
        patch(
            "app.components.integrations.workable.sync_service.build_role_interview_pack_templates",
            return_value={"screening": {}, "tech_stage_2": {}},
        ),
    ):
        role, created = service._upsert_role(db, org, job)
        assert created is True
        _assert_constructor_policy(role)
        role.auto_send_assessment = True
        db.flush()
        same_role, created = service._upsert_role(db, org, job)

    assert created is False
    assert same_role.id == role.id
    assert same_role.auto_send_assessment is True


def test_bullhorn_sync_applies_defaults_only_when_creating_role(db, monkeypatch):
    org = _org(db, suffix="bullhorn-constructor")
    _saved_constructor_policy(org)
    monkeypatch.setattr(bullhorn_sync_jobs, "_store_job_spec_attachment", lambda role: None)
    job = {"id": 901, "title": "Data Engineer", "description": "Own data."}

    role, created = bullhorn_sync_jobs.upsert_role_from_job_order(db, org, job)
    assert created is True
    assert role is not None
    _assert_constructor_policy(role)
    role.auto_send_assessment = True
    db.flush()
    same_role, created = bullhorn_sync_jobs.upsert_role_from_job_order(db, org, job)

    assert created is False
    assert same_role is not None and same_role.id == role.id
    assert same_role.auto_send_assessment is True


def test_workable_marketplace_applies_defaults_only_when_creating_role(db):
    org = _org(db, suffix="marketplace-constructor")
    _saved_constructor_policy(org)

    role = workable_provider_service._resolve_or_provision_role(
        db,
        org.id,
        job_shortcode="MARKET-1",
        job_title="Marketplace Engineer",
    )
    _assert_constructor_policy(role)
    role.auto_send_assessment = True
    db.flush()
    same_role = workable_provider_service._resolve_or_provision_role(
        db,
        org.id,
        job_shortcode="MARKET-1",
        job_title="Renamed upstream",
    )

    assert same_role.id == role.id
    assert same_role.auto_send_assessment is True


def test_native_deterministic_pre_screen_rejects_locally_when_opted_in(
    db, monkeypatch
):
    from app.services import application_automation_service as automation

    org = _org(db, suffix="native-reject")
    role = Role(
        organization_id=org.id,
        name="Native role",
        source="requisition",
        agentic_mode_enabled=True,
        auto_reject_pre_screen=True,
    )
    db.add(role)
    db.flush()
    app = _application(db, org=org, role=role, source="careers")
    monkeypatch.setattr(automation, "refresh_pre_screening_fields", lambda app: None)
    monkeypatch.setattr(
        automation,
        "evaluate_auto_reject_decision",
        lambda *args, **kwargs: {
            "should_trigger": True,
            "state": "eligible",
            "reason": "Below deterministic pre-screen threshold",
            "auto_disqualify_eligible": True,
            "snapshot": {"pre_screen_score": 35},
            "config": {"threshold_100": 60},
        },
    )
    monkeypatch.setattr(
        automation, "finalize_pre_screen_bullhorn_reject", lambda *args, **kwargs: None
    )

    result = automation.run_auto_reject_if_needed(
        db=db,
        org=org,
        app=app,
        role=role,
        actor_type="system",
    )
    db.flush()

    assert result["performed"] is True
    assert result["workable_synced"] is False
    assert app.application_outcome == "rejected"
    event = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "auto_rejected",
        )
        .one()
    )
    assert event.event_metadata["source"] == "native_public_apply"
    assert event.event_metadata["ats_provider"] == "standalone"


def test_role_family_pre_screen_reject_is_diverted_for_confirmation(
    db, monkeypatch
):
    from app.services import application_automation_service as automation

    org = _org(db, suffix="shared-reject")
    owner = Role(
        organization_id=org.id,
        name="Original ATS role",
        source="workable",
        agentic_mode_enabled=True,
        auto_reject_pre_screen=True,
    )
    db.add(owner)
    db.flush()
    db.add(
        Role(
            organization_id=org.id,
            name="Related role",
            source="sister",
            role_kind="sister",
            ats_owner_role_id=owner.id,
        )
    )
    db.flush()
    app = _application(db, org=org, role=owner, source="workable")
    monkeypatch.setattr(automation, "refresh_pre_screening_fields", lambda app: None)
    monkeypatch.setattr(
        automation,
        "evaluate_auto_reject_decision",
        lambda *args, **kwargs: {
            "should_trigger": True,
            "state": "eligible",
            "reason": "Below deterministic pre-screen threshold",
            "auto_disqualify_eligible": True,
            "snapshot": {"pre_screen_score": 35},
            "config": {"threshold_100": 60},
        },
    )
    diverted = MagicMock(return_value={"performed": False, "state": "pending"})
    monkeypatch.setattr(automation, "_divert_pre_screen_reject_to_card", diverted)

    result = automation.run_auto_reject_if_needed(
        db=db,
        org=org,
        app=app,
        role=owner,
        actor_type="system",
    )

    assert result == {"performed": False, "state": "pending"}
    assert "ATS application is shared" in diverted.call_args.kwargs["carded_reason"]


def test_autonomous_workable_advance_uses_configured_interview_stage(
    db, monkeypatch
):
    from app.actions import _workable_decision_summary as summary

    org = _org(db, suffix="workable-stage")
    org.workable_config = {"interview_stage_name": "Technical Interview"}
    role = Role(organization_id=org.id, name="Workable role")
    db.add(role)
    db.flush()
    app = _application(db, org=org, role=role, source="workable")
    app.workable_candidate_id = "candidate-1"
    move = MagicMock(return_value={"success": True, "config": {}})
    monkeypatch.setattr(summary, "_try_bullhorn_advance", lambda *args, **kwargs: None)
    monkeypatch.setattr(summary, "_workable_writeback_ready", lambda **kwargs: True)
    monkeypatch.setattr(
        "app.services.workable_actions_service.move_candidate_in_workable", move
    )

    ok = try_workable_advance(
        db,
        Actor.system(),
        app=app,
        org=org,
        role=role,
        target_stage=None,
    )

    assert ok is True
    assert move.call_args.kwargs["target_stage"] == "Technical Interview"

    move.reset_mock()
    app.workable_stage = None
    ok = try_workable_advance(
        db,
        Actor.system(),
        app=app,
        org=org,
        role=role,
        target_stage="Hiring Manager Interview",
    )
    assert ok is True
    assert move.call_args.kwargs["target_stage"] == "Hiring Manager Interview"


def test_organization_settings_flow_into_new_role_api_without_enabling_it(client):
    headers, _ = auth_headers(client)
    saved = client.patch(
        "/api/v1/organizations/me",
        headers=headers,
        json={
            "default_role_budget_cents": 9_000,
            "default_score_threshold": 68,
            "ai_tooling_config": {
                "agent_defaults": {
                    "budget_cents": 9_000,
                    "threshold_mode": "manual",
                    "auto_send_assessment": True,
                    "auto_resend_assessment": False,
                    "auto_advance": True,
                    "auto_reject_pre_screen": True,
                    "auto_skip_assessment": False,
                }
            },
        },
    )
    assert saved.status_code == 200, saved.text

    created = client.post(
        "/api/v1/roles",
        headers=headers,
        json={"name": "Workspace policy role"},
    )
    assert created.status_code == 201, created.text
    role = created.json()

    assert role["agentic_mode_enabled"] is False
    assert role["monthly_usd_budget_cents"] == 9_000
    assert role["score_threshold"] == 68
    assert role["auto_reject_threshold_mode"] == "manual"
    assert role["auto_send_assessment"] is True
    assert role["auto_resend_assessment"] is False
    assert role["auto_advance"] is True
    assert role["auto_reject_pre_screen"] is True
    assert role["agent_effective_policy"]["auto_resend_assessment"] is False
    assert role["agent_effective_policy"]["metering"][
        "llm_and_embedding_usage_metered"
    ] is True
    assert role["agent_effective_policy"]["metering"][
        "operational_provider_costs_estimated_separately"
    ] is True

    # An aggregate field from an older client must not erase a deliberately
    # mixed action-level policy.
    legacy_patch = client.patch(
        f"/api/v1/roles/{role['id']}",
        headers=headers,
        json={"expected_version": role["version"], "auto_promote": True},
    )
    assert legacy_patch.status_code == 200, legacy_patch.text
    preserved = legacy_patch.json()
    assert preserved["auto_send_assessment"] is True
    assert preserved["auto_resend_assessment"] is False
    assert preserved["auto_advance"] is True
    assert preserved["auto_promote"] is False

    granular_patch = client.patch(
        f"/api/v1/roles/{role['id']}",
        headers=headers,
        json={"expected_version": preserved["version"], "auto_resend_assessment": True},
    )
    assert granular_patch.status_code == 200, granular_patch.text
    enabled = granular_patch.json()
    assert enabled["agent_effective_policy"]["auto_resend_assessment"] is True
    assert enabled["auto_promote"] is True


def test_new_role_api_exposes_platform_policy_for_untouched_workspace(client):
    headers, _ = auth_headers(client)

    created = client.post(
        "/api/v1/roles",
        headers=headers,
        json={"name": "Platform-default role"},
    )
    assert created.status_code == 201, created.text
    role = created.json()

    assert role["agentic_mode_enabled"] is False
    assert role["auto_send_assessment"] is False
    assert role["auto_resend_assessment"] is False
    assert role["auto_advance"] is False
    assert role["auto_promote"] is False
    assert role["auto_reject_pre_screen"] is True
    assert role["auto_skip_assessment"] is True
    assert role["agent_effective_policy"]["auto_send_assessment"] is False
    assert role["agent_effective_policy"]["auto_reject_pre_screen"] is True
