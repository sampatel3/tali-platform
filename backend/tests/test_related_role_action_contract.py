"""Regression contract for roles that share one ATS application.

Related roles remain full Taali roles.  Only automatic rejection is forbidden
because rejection closes the shared ATS application.  Positive settings stay
role-owned, scheduled work must use the role-aware dispatcher, and explicit
recruiter reject/advance actions continue to operate on the shared application.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

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
                source_application_id=application.id,
                status="done",
                pipeline_stage="review",
                spec_fingerprint=f"contract-{index}",
                role_fit_score=80 + index,
            )
        )
        related_roles.append(related)
    db.commit()
    return headers, user, owner, related_roles, application


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
def test_role_api_blocks_both_automatic_rejects_for_every_family_member(
    client, db, member, field
):
    headers, _user, owner, related_roles, _application = _family(client, db)
    role = owner if member == "owner" else related_roles[0]

    response = client.patch(
        f"/api/v1/roles/{role.id}",
        headers=headers,
        json={"expected_version": int(role.version or 1), field: True},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "ATS application" in detail
    assert "every linked role" in detail
    db.expire_all()
    assert getattr(db.get(Role, role.id), field) is False


@pytest.mark.parametrize("member", ["owner", "related"])
@pytest.mark.parametrize("field", ["auto_reject", "auto_reject_pre_screen"])
def test_job_chat_blocks_both_automatic_rejects_for_every_family_member(
    client, db, member, field
):
    _headers, user, owner, related_roles, _application = _family(client, db)
    role = owner if member == "owner" else related_roles[0]

    result = agent_chat_tools.dispatch_tool(
        "adjust_agent_settings",
        {field: True},
        db=db,
        role=role,
        user=user,
    )

    assert result["ok"] is False
    assert result["reason"] == "related_role_reject_requires_confirmation"
    assert "ATS application" in result["message"]
    assert "every linked role" in result["message"]
    assert getattr(role, field) is False


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


def test_manual_reject_remains_allowed_and_closes_the_whole_family(client, db):
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
    assert {row.status for row in evaluations} == {"excluded"}


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
            reason="Related recruiter confirmed the shared rejection",
        ),
        db=db,
        current_user=recruiter,
    )

    assert response.application_outcome == "rejected"
    db.expire_all()
    assert db.get(CandidateApplication, application.id).application_outcome == "rejected"


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

    with patch.object(
        workable_op_runner, "_route_bullhorn_op", return_value=None
    ), patch(
        "app.services.workable_actions_service.move_candidate_in_workable"
    ) as provider_move, patch.object(
        workable_op_runner, "_op_post_note"
    ) as post_note:
        result = workable_op_runner._op_move_stage(
            db, int(owner.organization_id), payload
        )

    assert result == {"status": "ok", "application_id": application.id}
    provider_move.assert_called_once()
    db.expire_all()
    assert db.get(CandidateApplication, application.id).workable_stage == "final-interview"
    note_payload = post_note.call_args.args[2]
    assert f"{related.name} #{related.id}" in note_payload["body"]
    assert f"{owner.name} #{owner.id}" in note_payload["body"]

    projected = client.get(
        f"/api/v1/roles/{related.id}/applications", headers=headers
    )
    assert projected.status_code == 200, projected.text
    assert projected.json()[0]["workable_stage"] == "final-interview"
