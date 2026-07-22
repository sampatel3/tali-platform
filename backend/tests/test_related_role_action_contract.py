"""Regression contract for independent roles with optional shared ATS links.

Related roles own their membership, stage, outcome, decisions, and history.
The shared ATS application is an external-write transport and restriction
boundary only; it never couples the roles' local funnel state.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from app.agent_chat import tools as agent_chat_tools
from app.domains.assessments_runtime.applications_routes import (
    move_application_in_active_ats,
    update_application_outcome,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.job_hiring_team import TEAM_ROLE_RECRUITER, JobHiringTeam
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.schemas.role import ApplicationOutcomeUpdate, WorkableMoveStageRequest
from app.services.workable_actions_service import WorkableWritebackError
from app.services.sister_role_service import text_fingerprint
from tests.conftest import auth_headers


def _family(client, db, *, related_count: int = 1):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner = Role(
        organization_id=user.organization_id,
        name="Shared ATS owner",
        source="workable",
        workable_job_id="SHARED-CONTRACT",
        workable_job_data={"state": "published"},
        job_spec_text="Original role with a complete production job specification.",
        agentic_mode_enabled=False,
        monthly_usd_budget_cents=5_000,
        auto_reject=False,
        auto_reject_pre_screen=False,
        auto_send_assessment=False,
        auto_resend_assessment=False,
        auto_advance=False,
        auto_skip_assessment=True,
    )
    candidate = Candidate(
        organization_id=user.organization_id,
        email="shared-contract-candidate@example.com",
        full_name="Shared Contract Candidate",
        cv_text="Production Python, ML systems, and platform ownership.",
    )
    db.add_all([owner, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=user.organization_id,
        candidate_id=candidate.id,
        role_id=owner.id,
        source="manual",
        pipeline_stage="review",
        application_outcome="open",
        cv_text=candidate.cv_text,
    )
    db.add(application)
    db.flush()

    related_roles: list[Role] = []
    for index in range(related_count):
        related = Role(
            organization_id=user.organization_id,
            name=f"Independent related role {index + 1}",
            source="sister",
            role_kind=ROLE_KIND_SISTER,
            ats_owner_role_id=owner.id,
            job_spec_text=(
                "Independent related-role specification covering production "
                "engineering, delivery, and measurable operating outcomes."
            ),
            agentic_mode_enabled=False,
            monthly_usd_budget_cents=5_000,
            auto_reject=False,
            auto_reject_pre_screen=False,
            auto_send_assessment=False,
            auto_resend_assessment=False,
            auto_advance=False,
            auto_skip_assessment=True,
        )
        db.add(related)
        db.flush()
        db.add(
            SisterRoleEvaluation(
                organization_id=user.organization_id,
                role_id=related.id,
                candidate_id=candidate.id,
                source_application_id=application.id,
                ats_application_id=application.id,
                status="done",
                pipeline_stage="review",
                spec_fingerprint=text_fingerprint(related.job_spec_text),
                cv_fingerprint=text_fingerprint(candidate.cv_text),
                role_fit_score=80 + index,
            )
        )
        related_roles.append(related)
    db.commit()
    return headers, user, owner, related_roles, application


def _direct_related_application(
    db,
    *,
    related: Role,
    ats_application: CandidateApplication,
) -> tuple[CandidateApplication, SisterRoleEvaluation]:
    """Make the role-local application distinct from its explicit ATS transport."""

    direct_application = CandidateApplication(
        organization_id=int(ats_application.organization_id),
        candidate_id=int(ats_application.candidate_id),
        role_id=int(related.id),
        source="manual",
        pipeline_stage="review",
        application_outcome="open",
        cv_text=ats_application.cv_text,
    )
    db.add(direct_application)
    db.flush()
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(related.id))
        .one()
    )
    evaluation.source_application_id = int(direct_application.id)
    evaluation.candidate_id = int(direct_application.candidate_id)
    evaluation.ats_application_id = int(ats_application.id)
    evaluation.pipeline_stage = "review"
    evaluation.application_outcome = "open"
    db.flush()
    return direct_application, evaluation


def _related_decision(
    db,
    *,
    role: Role,
    application: CandidateApplication,
    status: str,
):
    from app.models.agent_decision import AgentDecision

    decision = AgentDecision(
        organization_id=int(application.organization_id),
        role_id=int(role.id),
        application_id=int(application.id),
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status=status,
        reasoning="Grounded role-local evidence supports progression.",
        evidence={"related_role_id": int(role.id), "role_fit_score": 84},
        confidence=0.84,
        model_version="test",
        prompt_version="test",
        idempotency_key=(
            f"related-contract:{role.id}:{application.id}:{status}"
        ),
    )
    db.add(decision)
    db.flush()
    return decision


def _role_only_recruiter(db, *, owner: Role, role: Role) -> User:
    recruiter = User(
        organization_id=int(owner.organization_id),
        email=f"role-only-{role.id}@example.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        role="member",
    )
    db.add(recruiter)
    db.flush()
    db.add(
        JobHiringTeam(
            organization_id=int(owner.organization_id),
            role_id=int(role.id),
            user_id=int(recruiter.id),
            team_role=TEAM_ROLE_RECRUITER,
        )
    )
    db.commit()
    return recruiter


@pytest.mark.parametrize("member", ["owner", "related"])
@pytest.mark.parametrize("field", ["auto_reject", "auto_reject_pre_screen"])
def test_role_api_persists_automatic_reject_for_only_the_acting_role(
    client, db, member, field
):
    headers, _user, owner, related_roles, _application = _family(
        client, db, related_count=2
    )
    role = owner if member == "owner" else related_roles[0]

    response = client.patch(
        f"/api/v1/roles/{role.id}",
        headers=headers,
        json={"expected_version": int(role.version or 1), field: True},
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert getattr(db.get(Role, role.id), field) is True
    untouched_ids = {
        int(candidate_role.id)
        for candidate_role in [owner, *related_roles]
        if int(candidate_role.id) != int(role.id)
    }
    assert all(
        getattr(db.get(Role, role_id), field) is False
        for role_id in untouched_ids
    )


@pytest.mark.parametrize("member", ["owner", "related"])
@pytest.mark.parametrize("field", ["auto_reject", "auto_reject_pre_screen"])
def test_job_chat_persists_automatic_reject_for_only_the_acting_role(
    client, db, member, field
):
    _headers, user, owner, related_roles, _application = _family(
        client, db, related_count=2
    )
    role = owner if member == "owner" else related_roles[0]

    result = agent_chat_tools.dispatch_tool(
        "adjust_agent_settings",
        {field: True},
        db=db,
        role=role,
        user=user,
    )

    assert result["ok"] is True
    assert field in result["changed"]
    db.expire_all()
    assert getattr(db.get(Role, role.id), field) is True
    untouched_ids = {
        int(candidate_role.id)
        for candidate_role in [owner, *related_roles]
        if int(candidate_role.id) != int(role.id)
    }
    assert all(
        getattr(db.get(Role, role_id), field) is False
        for role_id in untouched_ids
    )


def test_related_pre_screen_auto_reject_is_local_and_never_calls_an_ats_provider(
    client, db
):
    from app.services import application_automation_service as automation

    _headers, _user, owner, related_roles, application = _family(
        client, db, related_count=2
    )
    acting_role, sibling_role = related_roles
    acting_role.auto_reject_pre_screen = True
    db.commit()

    verdict = {
        "should_trigger": True,
        "auto_disqualify_eligible": True,
        "state": "eligible",
        "reason": "Deterministic pre-screen score is below this role's threshold",
        "snapshot": {"pre_screen_score": 10},
        "config": {"threshold_100": 50},
    }
    with patch.object(
        automation, "evaluate_auto_reject_decision", return_value=verdict
    ), patch.object(automation, "try_bullhorn_reject") as bullhorn_reject, patch.object(
        automation, "disqualify_candidate_in_workable"
    ) as workable_reject:
        result = automation.run_auto_reject_if_needed(
            db=db,
            org=db.get(Organization, owner.organization_id),
            app=application,
            role=acting_role,
            actor_type="system",
        )
        db.commit()

    db.expire_all()
    evaluations = {
        row.role_id: row
        for row in db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.source_application_id == application.id)
        .all()
    }
    assert result["performed"] is True
    assert result["role_local"] is True
    assert result["workable_synced"] is False
    assert evaluations[acting_role.id].application_outcome == "rejected"
    assert evaluations[sibling_role.id].application_outcome == "open"
    assert db.get(CandidateApplication, application.id).application_outcome == "open"
    bullhorn_reject.assert_not_called()
    workable_reject.assert_not_called()


def test_owner_pre_screen_automation_remains_enabled_when_related_roles_exist(
    client, db
):
    from app.services import application_automation_service as automation

    _headers, _user, owner, related_roles, application = _family(
        client, db, related_count=2
    )
    owner.agentic_mode_enabled = True
    owner.auto_reject_pre_screen = True
    application.source = "careers"
    db.commit()

    verdict = {
        "should_trigger": True,
        "auto_disqualify_eligible": True,
        "state": "eligible",
        "reason": "Deterministic pre-screen score is below the owner's threshold",
        "snapshot": {"pre_screen_score": 10},
        "config": {"threshold_100": 50},
    }
    with patch.object(
        automation, "evaluate_auto_reject_decision", return_value=verdict
    ), patch.object(automation, "try_bullhorn_reject") as bullhorn_reject, patch.object(
        automation, "disqualify_candidate_in_workable"
    ) as workable_reject:
        result = automation.run_auto_reject_if_needed(
            db=db,
            org=db.get(Organization, owner.organization_id),
            app=application,
            role=owner,
            actor_type="system",
        )
        db.commit()

    db.expire_all()
    assert result["performed"] is True
    assert db.get(CandidateApplication, application.id).application_outcome == "rejected"
    assert {
        row.role_id: row.application_outcome
        for row in db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.source_application_id == application.id)
        .all()
    } == {role.id: "open" for role in related_roles}
    bullhorn_reject.assert_not_called()
    workable_reject.assert_not_called()


def test_related_role_api_persists_positive_settings_and_activation(client, db):
    headers, _user, _owner, related_roles, _application = _family(client, db)
    related = related_roles[0]
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(related.id))
        .one()
    )
    evaluation.status = "retry_wait"
    db.commit()

    with patch(
        "app.services.agent_activation_readiness.activation_readiness",
        return_value={"ready": True, "checks": []},
    ), patch(
        "app.services.role_agent_dispatch.dispatch_role_agent_cycle"
    ) as dispatch:
        response = client.patch(
            f"/api/v1/roles/{related.id}",
            headers=headers,
            json={
                "expected_version": int(related.version or 1),
                "agentic_mode_enabled": True,
                "auto_send_assessment": True,
                "auto_resend_assessment": True,
                "auto_skip_assessment": True,
                "auto_advance": True,
            },
        )

    assert response.status_code == 200, response.text
    db.expire_all()
    persisted = db.get(Role, related.id)
    assert persisted.agentic_mode_enabled is True
    assert persisted.auto_send_assessment is True
    assert persisted.auto_resend_assessment is True
    assert persisted.auto_skip_assessment is True
    assert persisted.auto_advance is True
    assert persisted.auto_reject is False
    assert persisted.auto_reject_pre_screen is False
    assert db.get(SisterRoleEvaluation, evaluation.id).status == "pending"
    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0].id == related.id
    assert "release_sister_retries" not in dispatch.call_args.kwargs


def test_related_role_job_chat_persists_positive_settings_and_activation(client, db):
    _headers, user, _owner, related_roles, _application = _family(client, db)
    related = related_roles[0]
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(related.id))
        .one()
    )
    evaluation.status = "retry_wait"
    db.commit()

    settings = agent_chat_tools.dispatch_tool(
        "adjust_agent_settings",
        {
            "auto_send_assessment": True,
            "auto_resend_assessment": True,
            "auto_skip_assessment": True,
            "auto_advance": True,
        },
        db=db,
        role=related,
        user=user,
    )
    with patch(
        "app.services.agent_activation_readiness.activation_readiness",
        return_value={"ready": True, "checks": []},
    ), patch(
        "app.services.role_agent_dispatch.dispatch_role_agent_cycle"
    ) as dispatch:
        activation = agent_chat_tools.dispatch_tool(
            "set_agent_state",
            {"action": "activate"},
            db=db,
            role=related,
            user=user,
        )

    assert settings["ok"] is True
    assert activation["ok"] is True
    db.expire_all()
    persisted = db.get(Role, related.id)
    assert persisted.agentic_mode_enabled is True
    assert persisted.auto_send_assessment is True
    assert persisted.auto_resend_assessment is True
    assert persisted.auto_skip_assessment is True
    assert persisted.auto_advance is True
    assert persisted.auto_reject is False
    assert persisted.auto_reject_pre_screen is False
    assert db.get(SisterRoleEvaluation, evaluation.id).status == "pending"
    dispatch.assert_called_once_with(
        related,
        activation=True,
        role_version=int(related.version or 1),
    )


@pytest.mark.parametrize(
    ("sweep_name", "generic_task_name"),
    [
        ("agent_daily_review_sweep", "agent_daily_review_role"),
        ("agent_cohort_tick_sweep", "agent_cohort_tick_role"),
    ],
)
def test_scheduled_sweeps_route_related_roles_without_generic_owner_work(
    db, sweep_name, generic_task_name
):
    from app.tasks import agent_tasks

    org = Organization(
        name="Related sweep contract org", slug=f"related-sweep-{id(db)}"
    )
    db.add(org)
    db.flush()
    owner = Role(
        organization_id=org.id,
        name="Disabled shared owner",
        source="workable",
        workable_job_id="SWEEP-OWNER",
        agentic_mode_enabled=False,
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=org.id,
        name="Enabled related sweep role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5_000,
        auto_skip_assessment=True,
    )
    db.add(related)
    db.commit()

    sweep = getattr(agent_tasks, sweep_name)
    generic_task = getattr(agent_tasks, generic_task_name)
    with patch(
        "app.services.role_agent_dispatch.dispatch_role_agent_cycle"
    ) as dispatch, patch.object(generic_task, "delay") as generic_delay:
        result = sweep.run()

    assert result["status"] == "ok"
    assert result["role_ids"] == [related.id]
    generic_delay.assert_not_called()
    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0].id == related.id


def test_owner_manual_reject_does_not_close_independent_related_roles(client, db):
    headers, _user, _owner, related_roles, application = _family(
        client, db, related_count=2
    )

    response = client.patch(
        f"/api/v1/applications/{application.id}/outcome",
        headers=headers,
        json={
            "application_outcome": "rejected",
            "reason": "Recruiter confirmed the shared rejection",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["application_outcome"] == "rejected"
    db.expire_all()
    assert db.get(CandidateApplication, application.id).application_outcome == "rejected"
    evaluations = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.source_application_id == application.id)
        .all()
    )
    assert {row.role_id for row in evaluations} == {
        role.id for role in related_roles
    }
    assert {row.status for row in evaluations} == {"done"}
    assert {row.application_outcome for row in evaluations} == {"open"}


def test_related_only_recruiter_can_reject_via_related_role_not_owner_or_sibling(
    client, db
):
    _headers, _owner_user, owner, related_roles, application = _family(
        client, db, related_count=2
    )
    recruiter = _role_only_recruiter(
        db, owner=owner, role=related_roles[0]
    )

    for acting_role_id in (None, int(related_roles[1].id)):
        with pytest.raises(HTTPException) as exc_info:
            update_application_outcome(
                application_id=int(application.id),
                data=ApplicationOutcomeUpdate(
                    application_outcome="rejected",
                    acting_role_id=acting_role_id,
                ),
                db=db,
                current_user=recruiter,
            )
        assert exc_info.value.status_code == 403

    response = update_application_outcome(
        application_id=int(application.id),
        data=ApplicationOutcomeUpdate(
            application_outcome="rejected",
            acting_role_id=int(related_roles[0].id),
            reason="Related recruiter rejected the candidate for this role",
        ),
        db=db,
        current_user=recruiter,
    )

    assert response.application_outcome == "rejected"
    db.expire_all()
    assert db.get(CandidateApplication, application.id).application_outcome == "open"
    evaluations = {
        row.role_id: row
        for row in db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.source_application_id == application.id)
        .all()
    }
    assert evaluations[related_roles[0].id].application_outcome == "rejected"
    assert evaluations[related_roles[1].id].application_outcome == "open"


def test_related_only_recruiter_can_move_ats_via_own_related_role_only(client, db):
    _headers, _owner_user, owner, related_roles, application = _family(
        client, db, related_count=2
    )
    recruiter = _role_only_recruiter(
        db, owner=owner, role=related_roles[0]
    )
    application.workable_candidate_id = "related-only-workable-candidate"
    db.commit()

    with patch(
        "app.components.integrations.resolver.resolve_application_ats_provider",
        return_value=SimpleNamespace(ats="workable"),
    ), patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=913
    ) as enqueue:
        for acting_role_id in (None, int(related_roles[1].id)):
            with pytest.raises(HTTPException) as exc_info:
                move_application_in_active_ats(
                    application_id=int(application.id),
                    data=WorkableMoveStageRequest(
                        target_stage="final-interview",
                        acting_role_id=acting_role_id,
                    ),
                    db=db,
                    current_user=recruiter,
                )
            assert exc_info.value.status_code == 403

        response = move_application_in_active_ats(
            application_id=int(application.id),
            data=WorkableMoveStageRequest(
                target_stage="final-interview",
                acting_role_id=int(related_roles[0].id),
            ),
            db=db,
            current_user=recruiter,
        )

    assert response.ats_writeback_status == "queued"
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["acting_role_id"] == int(related_roles[0].id)
    assert payload["application_id"] == int(application.id)


def test_owner_only_recruiter_cannot_act_through_unassigned_related_role(client, db):
    _headers, _owner_user, owner, related_roles, application = _family(client, db)
    related = related_roles[0]
    recruiter = _role_only_recruiter(db, owner=owner, role=owner)
    application.workable_candidate_id = "owner-only-workable-candidate"
    db.commit()

    with patch(
        "app.components.integrations.resolver.resolve_application_ats_provider",
        return_value=SimpleNamespace(ats="workable"),
    ), patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=914
    ):
        with pytest.raises(HTTPException) as move_denied:
            move_application_in_active_ats(
                application_id=int(application.id),
                data=WorkableMoveStageRequest(
                    target_stage="final-interview",
                    acting_role_id=int(related.id),
                ),
                db=db,
                current_user=recruiter,
            )
        assert move_denied.value.status_code == 403

        owner_move = move_application_in_active_ats(
            application_id=int(application.id),
            data=WorkableMoveStageRequest(target_stage="final-interview"),
            db=db,
            current_user=recruiter,
        )
    assert owner_move.ats_writeback_status == "queued"

    application.workable_candidate_id = None
    db.commit()
    with pytest.raises(HTTPException) as outcome_denied:
        update_application_outcome(
            application_id=int(application.id),
            data=ApplicationOutcomeUpdate(
                application_outcome="rejected",
                acting_role_id=int(related.id),
            ),
            db=db,
            current_user=recruiter,
        )
    assert outcome_denied.value.status_code == 403

    owner_outcome = update_application_outcome(
        application_id=int(application.id),
        data=ApplicationOutcomeUpdate(application_outcome="rejected"),
        db=db,
        current_user=recruiter,
    )
    assert owner_outcome.application_outcome == "rejected"


def test_related_role_manual_advance_targets_and_updates_the_shared_ats_application(
    client, db
):
    from app.services import workable_op_runner

    headers, _user, owner, related_roles, application = _family(client, db)
    related = related_roles[0]
    application.source = "workable"
    application.workable_candidate_id = "workable-shared-candidate"
    db.commit()

    with patch(
        "app.components.integrations.resolver.resolve_application_ats_provider",
        return_value=SimpleNamespace(ats="workable"),
    ), patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=812
    ) as enqueue:
        response = client.post(
            f"/api/v1/applications/{application.id}/ats/move-stage",
            headers=headers,
            json={
                "target_stage": "final-interview",
                "reason": "Recruiter confirmed the shared ATS move",
                "acting_role_id": related.id,
            },
        )

    assert response.status_code == 200, response.text
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["application_id"] == application.id
    assert payload["acting_role_id"] == related.id
    assert db.get(CandidateApplication, application.id).role_id == owner.id
    org = db.get(Organization, int(owner.organization_id))
    org.workable_connected = True
    org.workable_access_token = "workable-token"
    org.workable_subdomain = "shared-contract"
    org.workable_config = {
        "workable_writeback": True,
        "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
        "workable_actor_member_id": "member-1",
    }
    db.commit()

    with patch.object(
        workable_op_runner, "_route_bullhorn_op", return_value=None
    ), patch(
        "app.services.workable_actions_service.move_candidate_in_workable"
    ) as provider_move, patch(
        "app.domains.integrations_notifications.adapters.build_workable_adapter"
    ) as adapter_factory:
        provider_move.return_value = {"success": True, "code": "ok"}
        adapter_factory.return_value.post_candidate_comment.return_value = {
            "success": True
        }
        result = workable_op_runner._op_move_stage(
            db, int(owner.organization_id), payload
        )

    assert result == {"status": "ok", "application_id": application.id}
    provider_move.assert_called_once()
    db.expire_all()
    assert db.get(CandidateApplication, application.id).workable_stage == "final-interview"
    body = adapter_factory.return_value.post_candidate_comment.call_args.kwargs[
        "body"
    ]
    assert body == (
        "TAALI · Candidate advanced for a related role\n"
        f"Role: {related.name}\n"
        f"Original ATS role: {owner.name}\n"
        "Reason: The candidate met the advance criteria for the related role."
    )
    assert f"#{related.id}" not in body
    assert f"#{owner.id}" not in body

    projected = client.get(
        f"/api/v1/roles/{related.id}/applications", headers=headers
    )
    assert projected.status_code == 200, projected.text
    assert projected.json()[0]["workable_stage"] == "final-interview"
    db.expire_all()
    assert db.get(CandidateApplication, application.id).pipeline_stage == "review"
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == related.id,
            SisterRoleEvaluation.source_application_id == application.id,
        )
        .one()
    )
    assert evaluation.pipeline_stage == "advanced"


def test_related_role_note_failure_does_not_replay_confirmed_workable_move(
    client, db
):
    from app.models.candidate_application_event import CandidateApplicationEvent
    from app.services import workable_op_runner

    _headers, _user, owner, related_roles, application = _family(client, db)
    related = related_roles[0]
    application.source = "workable"
    application.workable_candidate_id = "workable-shared-candidate"
    db.commit()
    payload = {
        "application_id": int(application.id),
        "target_stage": "final-interview",
        "reason": "Recruiter confirmed the shared ATS move",
        "acting_role_id": int(related.id),
    }

    with patch.object(
        workable_op_runner, "_route_bullhorn_op", return_value=None
    ), patch(
        "app.services.workable_actions_service.move_candidate_in_workable"
    ) as provider_move, patch.object(
        workable_op_runner,
        "_post_confirmed_related_role_workable_note",
        side_effect=WorkableWritebackError(
            action="note",
            code="api_error",
            message="rate limited",
            retriable=True,
        ),
    ):
        provider_move.return_value = {"success": True, "code": "ok"}
        result = workable_op_runner._op_move_stage(
            db, int(owner.organization_id), payload
        )

    assert result == {"status": "ok", "application_id": application.id}
    provider_move.assert_called_once()
    db.expire_all()
    moved = db.get(CandidateApplication, application.id)
    assert moved.workable_stage == "final-interview"
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application.id,
            CandidateApplicationEvent.event_type
            == "workable_movement_note_failed",
        )
        .count()
        == 1
    )


def test_direct_related_membership_uses_ats_only_as_transport(client, db):
    from app.models.candidate_application_event import CandidateApplicationEvent
    from app.services import workable_op_runner

    _headers, user, owner, related_roles, ats_application = _family(client, db)
    related = related_roles[0]
    direct_application = CandidateApplication(
        organization_id=int(owner.organization_id),
        candidate_id=int(ats_application.candidate_id),
        role_id=int(related.id),
        source="manual",
        pipeline_stage="review",
        application_outcome="open",
        cv_text=ats_application.cv_text,
    )
    db.add(direct_application)
    db.flush()
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == related.id)
        .one()
    )
    evaluation.source_application_id = int(direct_application.id)
    evaluation.candidate_id = int(direct_application.candidate_id)
    evaluation.ats_application_id = int(ats_application.id)
    owner.workable_stages = [
        {
            "id": "final-interview",
            "slug": "final-interview",
            "name": "Final Interview",
            "kind": "interview",
        }
    ]
    ats_application.source = "workable"
    ats_application.workable_candidate_id = "direct-related-ats-candidate"
    db.commit()
    db.expire_all()

    with patch(
        "app.components.integrations.resolver.resolve_application_ats_provider",
        return_value=SimpleNamespace(ats="workable"),
    ), patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=915
    ) as enqueue:
        response = move_application_in_active_ats(
            application_id=int(direct_application.id),
            data=WorkableMoveStageRequest(
                target_stage="final-interview",
                acting_role_id=int(related.id),
            ),
            db=db,
            current_user=user,
        )

    assert response.id == int(direct_application.id)
    assert response.role_id == int(related.id)
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["application_id"] == int(ats_application.id)
    assert payload["role_application_id"] == int(direct_application.id)
    assert payload["acting_role_id"] == int(related.id)

    with patch.object(
        workable_op_runner, "_route_bullhorn_op", return_value=None
    ), patch(
        "app.services.workable_actions_service.move_candidate_in_workable",
        return_value={"success": True, "code": "ok"},
    ), patch.object(
        workable_op_runner,
        "_post_confirmed_related_role_workable_note",
        return_value={"status": "ok"},
    ):
        result = workable_op_runner._op_move_stage(
            db, int(owner.organization_id), payload
        )

    assert result == {"status": "ok", "application_id": int(ats_application.id)}
    db.expire_all()
    assert db.get(CandidateApplication, ats_application.id).pipeline_stage == "review"
    assert db.get(CandidateApplication, direct_application.id).pipeline_stage == "review"
    evaluation = db.get(SisterRoleEvaluation, evaluation.id)
    assert evaluation.pipeline_stage == "advanced"
    role_events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == direct_application.id,
            CandidateApplicationEvent.role_id == related.id,
        )
        .all()
    )
    assert {event.event_type for event in role_events} >= {
        "workable_moved",
        "role_pipeline_stage_changed",
    }


def test_related_role_exact_workable_target_alias_is_silent_noop(client, db):
    from app.services import workable_op_runner

    _headers, _user, owner, related_roles, application = _family(client, db)
    related = related_roles[0]
    owner.workable_stages = [
        {
            "id": "stage-42",
            "slug": "final-interview",
            "name": "Final Interview",
            "kind": "interview",
        }
    ]
    application.source = "workable"
    application.workable_candidate_id = "workable-shared-candidate"
    application.workable_stage = "Final Interview"
    db.commit()

    with patch.object(
        workable_op_runner, "_route_bullhorn_op", return_value=None
    ), patch(
        "app.services.workable_actions_service.move_candidate_in_workable"
    ) as provider_move, patch.object(
        workable_op_runner, "_post_confirmed_related_role_workable_note"
    ) as post_note:
        result = workable_op_runner._op_move_stage(
            db,
            int(owner.organization_id),
            {
                "application_id": int(application.id),
                "target_stage": "final-interview",
                "acting_role_id": int(related.id),
            },
        )

    assert result == {
        "status": "skipped",
        "reason": "already_at_target",
        "application_id": int(application.id),
    }
    provider_move.assert_not_called()
    post_note.assert_not_called()
    db.expire_all()
    assert db.get(CandidateApplication, application.id).pipeline_stage == "review"
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == related.id,
            SisterRoleEvaluation.source_application_id == application.id,
        )
        .one()
    )
    assert evaluation.pipeline_stage == "advanced"


@pytest.mark.parametrize(
    "target_stage", ["applied", "invited", "in_assessment", "review"]
)
def test_related_role_workable_note_requires_outbound_advanced_move(
    client, db, target_stage
):
    from app.services import workable_op_runner

    _headers, _user, owner, related_roles, application = _family(client, db)
    related = related_roles[0]
    application.source = "workable"
    application.workable_candidate_id = "workable-shared-candidate"
    application.workable_stage = "sourced"
    db.commit()

    with patch.object(
        workable_op_runner, "_route_bullhorn_op", return_value=None
    ), patch(
        "app.services.workable_actions_service.move_candidate_in_workable",
        return_value={"success": True, "code": "ok"},
    ) as provider_move, patch.object(
        workable_op_runner, "_post_confirmed_related_role_workable_note"
    ) as post_note:
        result = workable_op_runner._op_move_stage(
            db,
            int(owner.organization_id),
            {
                "application_id": int(application.id),
                "target_stage": target_stage,
                "acting_role_id": int(related.id),
            },
        )

    assert result == {"status": "ok", "application_id": int(application.id)}
    provider_move.assert_called_once()
    post_note.assert_not_called()


def test_workable_note_failure_audit_error_cannot_replay_confirmed_move(
    client, db
):
    from app.domains.assessments_runtime import pipeline_service
    from app.services import workable_op_runner

    _headers, _user, owner, related_roles, application = _family(client, db)
    related = related_roles[0]
    application.source = "workable"
    application.workable_candidate_id = "workable-shared-candidate"
    db.commit()
    original_append = pipeline_service.append_application_event
    original_rollback = db.rollback
    rollback_calls: list[bool] = []

    def _append(*args, **kwargs):
        if kwargs.get("event_type") == "workable_movement_note_failed":
            raise RuntimeError("audit store unavailable")
        return original_append(*args, **kwargs)

    def _rollback():
        rollback_calls.append(True)
        return original_rollback()

    with patch.object(
        workable_op_runner, "_route_bullhorn_op", return_value=None
    ), patch(
        "app.services.workable_actions_service.move_candidate_in_workable",
        return_value={"success": True, "code": "ok"},
    ) as provider_move, patch.object(
        workable_op_runner,
        "_post_confirmed_related_role_workable_note",
        side_effect=RuntimeError("note endpoint unavailable"),
    ), patch.object(
        pipeline_service, "append_application_event", side_effect=_append
    ), patch.object(db, "rollback", side_effect=_rollback):
        result = workable_op_runner._op_move_stage(
            db,
            int(owner.organization_id),
            {
                "application_id": int(application.id),
                "target_stage": "final-interview",
                "acting_role_id": int(related.id),
            },
        )

    assert result == {"status": "ok", "application_id": int(application.id)}
    provider_move.assert_called_once()
    assert len(rollback_calls) >= 2
    db.expire_all()
    assert db.get(CandidateApplication, application.id).workable_stage == "final-interview"


def test_owner_terminal_state_does_not_block_open_related_decision(client, db):
    from datetime import datetime, timezone

    from app.services.decision_approval_guard import (
        enforce_decision_approval_eligibility,
    )
    from app.services.decision_role_context import (
        load_related_evaluation,
        load_related_evaluation_map,
    )

    _headers, _user, _owner, related_roles, application = _family(client, db)
    related = related_roles[0]
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(related.id))
        .one()
    )
    application.pipeline_stage = "advanced"
    application.application_outcome = "rejected"
    evaluation.pipeline_stage = "review"
    evaluation.application_outcome = "open"
    decision = _related_decision(
        db,
        role=related,
        application=application,
        status="pending",
    )
    freshness = SimpleNamespace(is_stale=False, reasons=[])

    with patch(
        "app.services.decision_approval_guard.enforce_decision_approval_freshness",
        return_value=freshness,
    ):
        assert (
            enforce_decision_approval_eligibility(
                db,
                decision,
                allow_engine_outdated=False,
                application=application,
            )
            is freshness
        )

    evaluation.deleted_at = datetime.now(timezone.utc)
    db.flush()
    assert (
        load_related_evaluation(
            db,
            decision=decision,
            application=application,
        )
        is None
    )
    assert load_related_evaluation_map(
        db,
        decisions=[decision],
        applications_by_id={int(application.id): application},
    ) == {}


def test_related_queue_serializes_with_role_local_terminal_transition(
    client, db
):
    from app.actions import queue_decision
    from app.actions.types import Actor
    from app.models.agent_run import AgentRun
    from app.services.related_role_action_service import (
        lock_related_role_membership as real_membership_lock,
        transition_related_role_outcome_action,
    )
    from app.services.role_execution_guard import lock_live_role as real_role_lock

    _headers, _user, _owner, related_roles, application = _family(client, db)
    related = related_roles[0]
    run = AgentRun(
        id=90_000_000 + int(related.id),
        organization_id=int(related.organization_id),
        role_id=int(related.id),
        trigger="manual",
        status="running",
        model_version="offline-test",
        prompt_version="related-role-lock-contract.v1",
    )
    db.add(run)
    db.flush()

    lock_order: list[str] = []

    def lock_role(*args, **kwargs):
        lock_order.append("role")
        return real_role_lock(*args, **kwargs)

    def lock_membership(*args, **kwargs):
        lock_order.append("membership")
        return real_membership_lock(*args, **kwargs)

    with patch(
        "app.services.role_execution_guard.lock_live_role",
        side_effect=lock_role,
    ), patch(
        "app.services.related_role_action_service.lock_related_role_membership",
        side_effect=lock_membership,
    ):
        decision = queue_decision.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(related.organization_id),
            role_id=int(related.id),
            application_id=int(application.id),
            decision_type="send_assessment",
            reasoning="Role-local evidence supports an assessment.",
            evidence={"related_role_id": int(related.id), "role_fit_score": 80},
            confidence=0.8,
            model_version="offline-test",
            prompt_version="related-role-lock-contract.v1",
            skip_episode=True,
        )

    assert lock_order == ["role", "membership"]
    assert decision.status == "pending"

    transitioned = transition_related_role_outcome_action(
        db,
        application=application,
        acting_role_id=int(related.id),
        to_outcome="rejected",
        source="recruiter",
        actor_type="recruiter",
        reason="Role-local terminal transition won after queue admission",
    )
    assert transitioned is not None
    db.flush()
    db.refresh(decision)
    assert decision.status == "discarded"

    with pytest.raises(HTTPException) as terminal_first:
        queue_decision.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(related.organization_id),
            role_id=int(related.id),
            application_id=int(application.id),
            decision_type="advance_to_interview",
            reasoning="This must not queue after the role-local terminal state.",
            confidence=0.8,
            model_version="offline-test",
            prompt_version="related-role-lock-contract.v1",
            skip_episode=True,
        )
    assert terminal_first.value.status_code == 422
    assert "resolved" in str(terminal_first.value.detail)


def test_direct_related_decision_uses_workable_transport_but_logical_history(
    client, db, monkeypatch
):
    from app.actions._decision_side_effects import apply_decision_side_effects
    from app.actions.types import Actor
    from app.models.candidate_application_event import CandidateApplicationEvent
    from app.platform import config as platform_config

    _headers, _user, owner, related_roles, ats_application = _family(client, db)
    related = related_roles[0]
    direct_application, _evaluation = _direct_related_application(
        db,
        related=related,
        ats_application=ats_application,
    )
    org = db.get(Organization, int(owner.organization_id))
    owner.workable_stages = [
        {
            "id": "final-interview",
            "slug": "final-interview",
            "name": "Final Interview",
            "kind": "interview",
        }
    ]
    ats_application.source = "workable"
    ats_application.workable_candidate_id = "explicit-workable-transport"
    org.workable_connected = True
    org.workable_access_token = "test-token"
    org.workable_subdomain = "test-workspace"
    org.workable_config = {
        "workable_writeback": True,
        "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
        "workable_actor_member_id": "member-1",
    }
    decision = _related_decision(
        db,
        role=related,
        application=direct_application,
        status="approved",
    )
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)
    adapter = SimpleNamespace(
        post_candidate_comment=Mock(return_value={"success": True})
    )

    with patch(
        "app.components.integrations.resolver.resolve_application_ats_provider",
        return_value=SimpleNamespace(ats="workable"),
    ), patch(
        "app.services.workable_actions_service.move_candidate_in_workable",
        return_value={
            "success": True,
            "code": "ok",
            "config": {"actor_member_id": "member-1"},
        },
    ) as move, patch(
        "app.actions._workable_decision_summary.build_workable_adapter",
        return_value=adapter,
    ), patch(
        "app.candidate_graph.episode_outbox.enqueue_recruiter_action",
        return_value=None,
    ):
        apply_decision_side_effects(
            db,
            Actor.system(),
            decision=decision,
            app=direct_application,
            org=org,
            role=related,
            disposition="approved",
            workable_target_stage="final-interview",
        )

    assert move.call_args.kwargs["candidate_id"] == "explicit-workable-transport"
    assert move.call_args.kwargs["role"].id == owner.id
    assert (
        adapter.post_candidate_comment.call_args.kwargs["candidate_id"]
        == "explicit-workable-transport"
    )
    assert related.name in adapter.post_candidate_comment.call_args.kwargs["body"]
    assert ats_application.workable_stage == "final-interview"
    assert direct_application.workable_stage is None

    event_types = {"workable_moved", "workable_decision_note_posted"}
    logical_events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(direct_application.id),
            CandidateApplicationEvent.event_type.in_(event_types),
        )
        .all()
    )
    assert {event.event_type for event in logical_events} == event_types
    assert all(event.role_id == related.id for event in logical_events)
    assert all(event.agent_decision_id == decision.id for event in logical_events)
    assert all(event.effect_status == "confirmed" for event in logical_events)
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(ats_application.id),
            CandidateApplicationEvent.event_type.in_(event_types),
        )
        .count()
        == 0
    )


def test_direct_related_decision_uses_bullhorn_transport_but_logical_history(
    client, db
):
    from app.actions._decision_side_effects import apply_decision_side_effects
    from app.actions.types import Actor
    from app.components.integrations.bullhorn.provider import BullhornProvider
    from app.models.candidate_application_event import CandidateApplicationEvent

    _headers, _user, owner, related_roles, ats_application = _family(client, db)
    related = related_roles[0]
    direct_application, _evaluation = _direct_related_application(
        db,
        related=related,
        ats_application=ats_application,
    )
    org = db.get(Organization, int(owner.organization_id))
    owner.source = "bullhorn"
    owner.bullhorn_job_order_id = "bullhorn-owner-job"
    ats_application.source = "bullhorn"
    ats_application.bullhorn_job_submission_id = "explicit-bullhorn-transport"
    ats_application.candidate.bullhorn_candidate_id = "explicit-bullhorn-candidate"
    decision = _related_decision(
        db,
        role=related,
        application=direct_application,
        status="approved",
    )
    provider = BullhornProvider(org, db)
    db.flush()

    with patch(
        "app.components.integrations.resolver.resolve_application_ats_provider",
        return_value=provider,
    ), patch.object(
        provider,
        "move_application",
        return_value={
            "success": True,
            "code": "ok",
            "config": {"remote_status": "Interview Scheduled"},
        },
    ) as move, patch.object(
        provider,
        "post_note",
        return_value={"success": True, "code": "ok", "config": {}},
    ) as post_note, patch(
        "app.candidate_graph.episode_outbox.enqueue_recruiter_action",
        return_value=None,
    ):
        apply_decision_side_effects(
            db,
            Actor.system(),
            decision=decision,
            app=direct_application,
            org=org,
            role=related,
            disposition="approved",
            workable_target_stage="technical-interview",
        )

    assert move.call_args.kwargs["candidate_id"] == "explicit-bullhorn-transport"
    assert move.call_args.kwargs["role"].id == owner.id
    assert post_note.call_args.kwargs["candidate_id"] == "explicit-bullhorn-candidate"
    assert post_note.call_args.kwargs["role"].id == owner.id
    assert related.name in post_note.call_args.kwargs["body"]

    event_types = {"bullhorn_moved", "bullhorn_decision_note_posted"}
    logical_events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(direct_application.id),
            CandidateApplicationEvent.event_type.in_(event_types),
        )
        .all()
    )
    assert {event.event_type for event in logical_events} == event_types
    assert all(event.role_id == related.id for event in logical_events)
    assert all(event.agent_decision_id == decision.id for event in logical_events)
    assert all(event.effect_status == "confirmed" for event in logical_events)
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(ats_application.id),
            CandidateApplicationEvent.event_type.in_(event_types),
        )
        .count()
        == 0
    )
