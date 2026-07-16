"""Provider-neutral ATS hand-back route and Workable compatibility coverage."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.job_hiring_team import TEAM_ROLE_RECRUITER, JobHiringTeam
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.platform.config import settings
from app.domains.assessments_runtime.applications_routes import (
    ApplicationOutcomeUpdate,
    WorkableMoveStageRequest,
    move_application_in_active_ats,
    move_application_in_workable,
    update_application_outcome,
)
from app.domains.assessments_runtime.related_role_capability_routes import (
    related_ats_transition_capability,
)
from app.services.workable_op_runner import AtsJobRunPersistenceError
from tests.conftest import TestingSessionLocal, auth_headers


def _application(client, db):
    headers, email = auth_headers(
        client,
        email="ats-move@example.com",
        organization_name="ATS Move Org",
    )
    role_response = client.post(
        "/api/v1/roles",
        headers=headers,
        json={"name": "Backend Engineer", "description": "Build services"},
    )
    assert role_response.status_code == 201, role_response.text
    role_id = int(role_response.json()["id"])
    app_response = client.post(
        f"/api/v1/roles/{role_id}/applications",
        headers=headers,
        json={
            "candidate_email": "candidate.ats.move@example.com",
            "candidate_name": "ATS Candidate",
        },
    )
    assert app_response.status_code == 201, app_response.text
    app_id = int(app_response.json()["id"])
    user = db.query(User).filter(User.email == email).one()
    org = db.query(Organization).filter(Organization.id == user.organization_id).one()
    role = db.query(Role).filter(Role.id == role_id).one()
    app = db.query(CandidateApplication).filter(CandidateApplication.id == app_id).one()
    return headers, org, role, app


def test_generic_move_stage_routes_bullhorn_intent_through_shared_runner(
    client, db, monkeypatch
):
    headers, org, role, app = _application(client, db)
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    # Application linkage, not org-level Workable precedence, owns a scoped
    # write in a workspace that is intentionally connected to both ATSes.
    org.workable_connected = True
    org.workable_access_token = "workable-token"
    org.workable_subdomain = "deep-light"
    role.source = "bullhorn"
    role.bullhorn_job_order_id = "job-42"
    app.source = "bullhorn"
    app.bullhorn_job_submission_id = "submission-9"
    app.candidate.bullhorn_candidate_id = "candidate-7"
    db.commit()

    with patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=123
    ) as enqueue:
        response = client.post(
            f"/api/v1/applications/{app.id}/ats/move-stage",
            headers=headers,
            json={"target_stage": "advanced", "reason": "Ready for interview"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["ats_writeback_status"] == "queued"
    assert response.json()["ats_writeback_job_run_id"] == 123
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["application_id"] == app.id
    assert payload["target_stage"] == "advanced"
    assert payload["target_intent"] == "advanced"


def test_related_role_attribution_is_propagated_to_durable_move_payload(client, db):
    headers, org, owner, app = _application(client, db)
    user = db.query(User).filter(User.organization_id == org.id).one()
    org.workable_connected = True
    org.workable_access_token = "workable-token"
    org.workable_subdomain = "deep-light"
    org.workable_config = {
        "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
        "workable_writeback": True,
        "workable_actor_member_id": "member-1",
    }
    owner.source = "workable"
    owner.workable_job_id = "job-related-attribution"
    app.source = "workable"
    app.workable_candidate_id = "candidate-related-attribution"
    related = Role(
        organization_id=org.id,
        name="Related backend role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    db.add_all(
        [
            JobHiringTeam(
                organization_id=org.id,
                role_id=related.id,
                user_id=user.id,
                team_role=TEAM_ROLE_RECRUITER,
            ),
            SisterRoleEvaluation(
                organization_id=org.id,
                role_id=related.id,
                source_application_id=app.id,
                status="done",
                spec_fingerprint="related-attribution",
            ),
        ]
    )
    db.commit()

    capability = client.get(
        f"/api/v1/roles/{related.id}/related-ats-transition-capability",
        headers=headers,
    )
    assert capability.status_code == 200, capability.text
    assert capability.json() == {
        "protocol_version": 1,
        "provider_confirmation_managed": True,
        "related_stage_projection_managed": True,
    }

    with patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=124
    ) as enqueue:
        response = client.post(
            f"/api/v1/roles/{related.id}/applications/{app.id}"
            "/ats/managed-move-stage-v1",
            headers=headers,
            json={"target_stage": "Technical Interview", "acting_role_id": related.id},
        )

    assert response.status_code == 200, response.text
    assert response.json()["ats_writeback_job_run_id"] == 124
    assert response.json()["ats_related_transition_protocol"] == 1
    assert response.json()["ats_related_stage_managed"] is True
    assert enqueue.call_args.kwargs["payload"]["acting_role_id"] == related.id

    mismatch = client.post(
        f"/api/v1/roles/{related.id}/applications/{app.id}"
        "/ats/managed-move-stage-v1",
        headers=headers,
        json={
            "target_stage": "Technical Interview",
            "acting_role_id": related.id + 1,
        },
    )
    assert mismatch.status_code == 422, mismatch.text
    assert enqueue.call_count == 1


def _legacy_related_role_actor(
    db, *, org, owner, app, related_access: bool, source_access: bool = True
):
    actor = User(
        email=f"legacy-related-{'allowed' if related_access else 'denied'}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        organization_id=org.id,
        role="member",
    )
    related = Role(
        organization_id=org.id,
        name="Legacy Workable related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add_all([actor, related])
    db.flush()
    if source_access:
        db.add(
            JobHiringTeam(
                organization_id=org.id,
                role_id=owner.id,
                user_id=actor.id,
                team_role=TEAM_ROLE_RECRUITER,
            )
        )
    if related_access:
        db.add(
            JobHiringTeam(
                organization_id=org.id,
                role_id=related.id,
                user_id=actor.id,
                team_role=TEAM_ROLE_RECRUITER,
            )
        )
    app.workable_candidate_id = "legacy-related-candidate"
    db.add(
        SisterRoleEvaluation(
            organization_id=org.id,
            role_id=related.id,
            source_application_id=app.id,
            status="done",
            spec_fingerprint="legacy-related-authorization",
        )
    )
    db.commit()
    return actor, related


def test_legacy_workable_related_move_requires_related_role_edit_access(client, db):
    _headers, org, owner, app = _application(client, db)
    actor, related = _legacy_related_role_actor(
        db,
        org=org,
        owner=owner,
        app=app,
        related_access=False,
    )

    with (
        patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue,
        pytest.raises(HTTPException) as exc_info,
    ):
        move_application_in_workable(
            application_id=int(app.id),
            data=WorkableMoveStageRequest(
                target_stage="final-interview",
                acting_role_id=int(related.id),
            ),
            db=db,
            current_user=actor,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"
    enqueue.assert_not_called()


def test_related_role_capability_requires_related_role_edit_access(client, db):
    _headers, org, owner, app = _application(client, db)
    actor, related = _legacy_related_role_actor(
        db,
        org=org,
        owner=owner,
        app=app,
        related_access=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        related_ats_transition_capability(
            role_id=int(related.id),
            db=db,
            current_user=actor,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"


def test_legacy_workable_related_move_allows_related_role_recruiter(client, db):
    _headers, org, owner, app = _application(client, db)
    actor, related = _legacy_related_role_actor(
        db,
        org=org,
        owner=owner,
        app=app,
        related_access=True,
        source_access=False,
    )

    with patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=125
    ) as enqueue:
        response = move_application_in_workable(
            application_id=int(app.id),
            data=WorkableMoveStageRequest(
                target_stage="final-interview",
                acting_role_id=int(related.id),
            ),
            db=db,
            current_user=actor,
        )

    assert response.ats_writeback_status == "queued"
    assert response.ats_writeback_job_run_id == 125
    assert enqueue.call_args.kwargs["payload"]["acting_role_id"] == related.id


def test_related_role_recruiter_can_reject_shared_application_without_source_assignment(
    client, db
):
    _headers, org, owner, app = _application(client, db)
    actor, related = _legacy_related_role_actor(
        db,
        org=org,
        owner=owner,
        app=app,
        related_access=True,
        source_access=False,
    )

    with patch(
        "app.domains.assessments_runtime.applications_routes._sync_workable_outcome_change",
        return_value={"status": "succeeded"},
    ):
        response = update_application_outcome(
            application_id=int(app.id),
            data=ApplicationOutcomeUpdate(
                application_outcome="rejected",
                reason="Rejected from related role",
                acting_role_id=int(related.id),
            ),
            db=db,
            current_user=actor,
        )

    assert response.application_outcome == "rejected"
    db.refresh(app)
    assert app.application_outcome == "rejected"
    outcome_event = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(app.id),
            CandidateApplicationEvent.event_type == "application_outcome_changed",
        )
        .one()
    )
    assert outcome_event.event_metadata["acting_role_id"] == int(related.id)


def test_related_role_outcome_requires_related_role_edit_access(client, db):
    _headers, org, owner, app = _application(client, db)
    actor, related = _legacy_related_role_actor(
        db,
        org=org,
        owner=owner,
        app=app,
        related_access=False,
        source_access=True,
    )

    with (
        patch(
            "app.domains.assessments_runtime.applications_routes._sync_workable_outcome_change"
        ) as sync_outcome,
        pytest.raises(HTTPException) as exc_info,
    ):
        update_application_outcome(
            application_id=int(app.id),
            data=ApplicationOutcomeUpdate(
                application_outcome="rejected",
                acting_role_id=int(related.id),
            ),
            db=db,
            current_user=actor,
        )

    assert exc_info.value.status_code == 403
    sync_outcome.assert_not_called()
    db.rollback()
    db.refresh(app)
    assert app.application_outcome == "open"


def test_deleted_related_role_cannot_authorize_shared_outcome(client, db):
    _headers, org, owner, app = _application(client, db)
    actor, related = _legacy_related_role_actor(
        db,
        org=org,
        owner=owner,
        app=app,
        related_access=True,
        source_access=False,
    )
    related.deleted_at = datetime.now(timezone.utc)
    db.commit()

    with (
        patch(
            "app.domains.assessments_runtime.applications_routes._sync_workable_outcome_change"
        ) as sync_outcome,
        pytest.raises(HTTPException) as exc_info,
    ):
        update_application_outcome(
            application_id=int(app.id),
            data=ApplicationOutcomeUpdate(
                application_outcome="rejected",
                acting_role_id=int(related.id),
            ),
            db=db,
            current_user=actor,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"
    sync_outcome.assert_not_called()
    db.rollback()
    db.refresh(app)
    assert app.application_outcome == "open"


def test_related_role_actions_require_candidate_in_the_related_roster(client, db):
    _headers, org, owner, app = _application(client, db)
    actor, related = _legacy_related_role_actor(
        db,
        org=org,
        owner=owner,
        app=app,
        related_access=True,
        source_access=False,
    )
    db.query(SisterRoleEvaluation).filter(
        SisterRoleEvaluation.role_id == int(related.id),
        SisterRoleEvaluation.source_application_id == int(app.id),
    ).delete(synchronize_session=False)
    db.commit()

    with (
        patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue,
        pytest.raises(HTTPException) as move_exc,
    ):
        move_application_in_workable(
            application_id=int(app.id),
            data=WorkableMoveStageRequest(
                target_stage="final-interview",
                acting_role_id=int(related.id),
            ),
            db=db,
            current_user=actor,
        )
    assert move_exc.value.status_code == 409
    enqueue.assert_not_called()

    db.rollback()
    with (
        patch(
            "app.domains.assessments_runtime.applications_routes._sync_workable_outcome_change"
        ) as sync_outcome,
        pytest.raises(HTTPException) as outcome_exc,
    ):
        update_application_outcome(
            application_id=int(app.id),
            data=ApplicationOutcomeUpdate(
                application_outcome="rejected",
                acting_role_id=int(related.id),
            ),
            db=db,
            current_user=actor,
        )
    assert outcome_exc.value.status_code == 409
    sync_outcome.assert_not_called()
    db.rollback()
    db.refresh(app)
    assert app.application_outcome == "open"


def test_closed_application_is_rejected_before_move_is_queued(client, db):
    headers, _org, _role, app = _application(client, db)
    app.application_outcome = "withdrawn"
    app.workable_candidate_id = "closed-workable-candidate"
    db.commit()

    with patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue:
        response = client.post(
            f"/api/v1/applications/{app.id}/workable/move-stage",
            headers=headers,
            json={"target_stage": "Technical Interview"},
        )

    assert response.status_code == 409, response.text
    assert "closed or disqualified" in response.json()["detail"]
    enqueue.assert_not_called()


@pytest.mark.parametrize("team_role", [None, "interviewer", "coordinator"])
def test_generic_move_stage_denies_non_editors(client, db, team_role):
    _headers, org, role, app = _application(client, db)
    actor = User(
        email=f"ats-denied-{team_role or 'unassigned'}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        organization_id=org.id,
        role="member",
    )
    db.add(actor)
    db.flush()
    if team_role is not None:
        db.add(
            JobHiringTeam(
                organization_id=org.id,
                role_id=role.id,
                user_id=actor.id,
                team_role=team_role,
            )
        )
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        move_application_in_active_ats(
            application_id=int(app.id),
            data=WorkableMoveStageRequest(target_stage="advanced"),
            db=db,
            current_user=actor,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"


def test_manual_bullhorn_reject_queues_while_role_agent_is_paused(
    client, db, monkeypatch
):
    headers, org, role, app = _application(client, db)
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    org.workable_connected = True
    org.workable_access_token = "workable-token"
    org.workable_subdomain = "deep-light"
    role.source = "bullhorn"
    role.bullhorn_job_order_id = "job-42"
    role.agentic_mode_enabled = False
    role.agent_paused_at = datetime.now(timezone.utc)
    app.source = "bullhorn"
    app.bullhorn_job_submission_id = "submission-9"
    db.commit()

    outcome_seen_by_publisher: list[str] = []

    def _enqueue_after_commit(**_kwargs):
        check = TestingSessionLocal()
        try:
            persisted = check.query(CandidateApplication).filter_by(id=app.id).one()
            outcome_seen_by_publisher.append(persisted.application_outcome)
        finally:
            check.close()
        return 789

    with patch(
        "app.services.workable_op_runner.enqueue_workable_op",
        side_effect=_enqueue_after_commit,
    ) as enqueue:
        response = client.patch(
            f"/api/v1/applications/{app.id}/outcome",
            headers=headers,
            json={"application_outcome": "rejected", "reason": "Not a match"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["application_outcome"] == "rejected"
    assert response.json()["ats_writeback_status"] == "queued"
    assert response.json()["ats_writeback_job_run_id"] == 789
    receipt = response.json()["integration_sync_state"]["outcome_writeback"]
    assert receipt["provider"] == "bullhorn"
    assert receipt["status"] == "queued"
    assert receipt["target_outcome"] == "rejected"
    assert outcome_seen_by_publisher == ["rejected"]
    assert enqueue.call_args.kwargs["op_type"] == "manual_outcome"
    assert enqueue.call_args.kwargs["payload"] == {
        "application_id": app.id,
        "user_id": enqueue.call_args.kwargs["payload"]["user_id"],
        "target_outcome": "rejected",
        "reason": "Not a match",
    }
    db.expire_all()
    persisted = db.get(CandidateApplication, int(app.id))
    assert persisted.integration_sync_state["outcome_writeback"]["status"] == "queued"


def test_manual_bullhorn_outcome_fails_closed_when_integration_is_off(
    client, db, monkeypatch
):
    headers, org, role, app = _application(client, db)
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", False)
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    role.source = "bullhorn"
    role.bullhorn_job_order_id = "job-42"
    app.source = "bullhorn"
    app.bullhorn_job_submission_id = "submission-9"
    db.commit()

    with patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue:
        response = client.patch(
            f"/api/v1/applications/{app.id}/outcome",
            headers=headers,
            json={"application_outcome": "rejected"},
        )

    assert response.status_code == 409, response.text
    assert "disabled or disconnected" in response.json()["detail"]
    enqueue.assert_not_called()
    db.expire_all()
    persisted = db.query(CandidateApplication).filter_by(id=app.id).one()
    assert persisted.application_outcome == "open"


def test_manual_bullhorn_outcome_reports_failed_when_tracking_is_unavailable(
    client, db, monkeypatch
):
    headers, org, role, app = _application(client, db)
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    role.source = "bullhorn"
    role.bullhorn_job_order_id = "job-tracking-failure"
    app.source = "bullhorn"
    app.bullhorn_job_submission_id = "submission-tracking-failure"
    db.commit()

    with patch("app.services.background_job_runs.create_run", return_value=None):
        response = client.patch(
            f"/api/v1/applications/{app.id}/outcome",
            headers=headers,
            json={"application_outcome": "rejected", "reason": "Not a match"},
        )

    assert response.status_code == 503, response.text
    db.expire_all()
    persisted = db.get(CandidateApplication, int(app.id))
    assert persisted.application_outcome == "rejected"
    receipt = persisted.integration_sync_state["outcome_writeback"]
    assert receipt["provider"] == "bullhorn"
    assert receipt["status"] == "failed"
    assert receipt["target_outcome"] == "rejected"


def test_move_stage_returns_503_when_tracking_is_unavailable(client, db):
    headers, _org, _role, app = _application(client, db)
    app.workable_candidate_id = "workable-candidate-tracking-failure"
    db.commit()

    with patch(
        "app.services.workable_op_runner.enqueue_workable_op",
        side_effect=AtsJobRunPersistenceError("move_stage"),
    ):
        response = client.post(
            f"/api/v1/applications/{app.id}/workable/move-stage",
            headers=headers,
            json={"target_stage": "interview"},
        )

    assert response.status_code == 503, response.text
    assert "No provider update was sent" in response.json()["detail"]


def test_manual_workable_reject_persists_confirmed_provider_receipt(client, db):
    headers, _org, _role, app = _application(client, db)
    app.source = "workable"
    app.workable_candidate_id = "workable-candidate-9"
    db.commit()

    with patch(
        "app.domains.assessments_runtime.applications_routes._sync_workable_outcome_change",
        return_value={
            "success": True,
            "action": "disqualify",
            "code": "ok",
            "message": "Candidate disqualified in Workable",
            "config": {},
        },
    ):
        response = client.patch(
            f"/api/v1/applications/{app.id}/outcome",
            headers=headers,
            json={"application_outcome": "rejected", "reason": "Not a match"},
        )

    assert response.status_code == 200, response.text
    receipt = response.json()["integration_sync_state"]["outcome_writeback"]
    assert receipt["provider"] == "workable"
    assert receipt["status"] == "confirmed"
    assert receipt["target_outcome"] == "rejected"


def test_manual_bullhorn_outcome_recovers_a_lost_broker_kick(client, db, monkeypatch):
    from app.models.background_job_run import BackgroundJobRun, JOB_KIND_WORKABLE_OP
    from app.tasks.workable_tasks import (
        recover_dispatching_workable_ops,
        run_workable_op_task,
    )

    headers, org, role, app = _application(client, db)
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    role.source = "bullhorn"
    role.bullhorn_job_order_id = "job-recover"
    app.source = "bullhorn"
    app.bullhorn_job_submission_id = "submission-recover"
    db.commit()

    with patch.object(
        run_workable_op_task,
        "apply_async",
        side_effect=RuntimeError("broker unavailable"),
    ):
        response = client.patch(
            f"/api/v1/applications/{app.id}/outcome",
            headers=headers,
            json={"application_outcome": "rejected", "reason": "Not a match"},
        )

    assert response.status_code == 200, response.text
    db.expire_all()
    run = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.organization_id == org.id,
            BackgroundJobRun.kind == JOB_KIND_WORKABLE_OP,
        )
        .order_by(BackgroundJobRun.id.desc())
        .first()
    )
    assert run is not None
    assert run.status == "dispatching"
    assert "recovery_payload" in (run.counters or {})

    with patch.object(run_workable_op_task, "apply_async") as replay:
        recovered = recover_dispatching_workable_ops.run(limit=10, older_than_seconds=0)

    assert recovered == {"scanned": 1, "recovered": 1, "failed": 0}
    replay_payload = replay.call_args.kwargs["kwargs"]
    assert replay_payload["job_run_id"] == run.id
    assert replay_payload["op_type"] == "manual_outcome"
    assert replay_payload["payload"]["application_id"] == app.id
    assert replay_payload["payload"]["target_outcome"] == "rejected"


@pytest.mark.parametrize("stranded_status", ["queued", "running"])
def test_replay_safe_ats_op_recovers_after_accepted_delivery_or_worker_loss(
    client, db, stranded_status
):
    from app.models.background_job_run import BackgroundJobRun
    from app.services.workable_op_runner import OP_MANUAL_OUTCOME, enqueue_workable_op
    from app.tasks.workable_tasks import (
        recover_dispatching_workable_ops,
        run_workable_op_task,
    )

    _headers, org, _role, app = _application(client, db)
    db.commit()
    with patch.object(run_workable_op_task, "apply_async"):
        run_id = enqueue_workable_op(
            organization_id=int(org.id),
            op_type=OP_MANUAL_OUTCOME,
            payload={
                "application_id": int(app.id),
                "target_outcome": "rejected",
                "reason": "Durability regression",
            },
        )
    assert run_id is not None

    db.expire_all()
    run = db.get(BackgroundJobRun, int(run_id))
    assert run is not None
    run.status = stranded_status
    counters = dict(run.counters or {})
    if stranded_status == "running":
        counters["last_started_at"] = "2000-01-01T00:00:00+00:00"
    else:
        counters["last_dispatched_at"] = "2000-01-01T00:00:00+00:00"
    run.counters = counters
    db.commit()

    with patch.object(run_workable_op_task, "apply_async") as replay:
        recovered = recover_dispatching_workable_ops.run(
            limit=10,
            older_than_seconds=0,
            running_older_than_seconds=0,
        )

    assert recovered == {"scanned": 1, "recovered": 1, "failed": 0}
    replay.assert_called_once()
    replay_kwargs = replay.call_args.kwargs["kwargs"]
    assert replay_kwargs["job_run_id"] == run_id
    assert replay_kwargs["op_type"] == OP_MANUAL_OUTCOME
    assert replay_kwargs["payload"]["application_id"] == app.id
    db.expire_all()
    assert db.get(BackgroundJobRun, int(run_id)).status == "queued"


def test_manual_bullhorn_outcome_commit_failure_never_publishes(db, monkeypatch):
    from fastapi import HTTPException

    from app.domains.assessments_runtime.applications_routes import (
        update_application_outcome,
    )
    from app.schemas.role import ApplicationOutcomeUpdate

    org = Organization(name="Commit Guard", slug="commit-guard")
    db.add(org)
    db.flush()
    user = User(
        organization_id=org.id,
        email="commit-guard@example.com",
        hashed_password="not-used",
        full_name="Commit Guard",
        role="owner",
        is_active=True,
    )
    role = Role(
        organization_id=org.id,
        name="Bullhorn Commit Guard",
        source="bullhorn",
        bullhorn_job_order_id="job-commit",
    )
    db.add_all([user, role])
    db.flush()
    from app.models.candidate import Candidate

    candidate = Candidate(
        organization_id=org.id,
        email="candidate.commit@example.com",
        full_name="Candidate Commit",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        source="bullhorn",
        bullhorn_job_submission_id="submission-commit",
        pipeline_stage="review",
        application_outcome="open",
    )
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    db.add(app)
    db.commit()
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)

    with (
        patch.object(db, "commit", side_effect=RuntimeError("forced commit failure")),
        patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue,
        pytest.raises(HTTPException) as exc,
    ):
        update_application_outcome(
            int(app.id),
            ApplicationOutcomeUpdate(application_outcome="rejected"),
            db,
            user,
        )

    assert exc.value.status_code == 500
    enqueue.assert_not_called()
    db.expire_all()
    assert (
        db.query(CandidateApplication).filter_by(id=app.id).one().application_outcome
        == "open"
    )


def test_legacy_workable_move_stage_endpoint_is_preserved(client, db):
    headers, _org, _role, app = _application(client, db)
    app.workable_candidate_id = "workable-candidate-4"
    db.commit()

    with patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=456
    ) as enqueue:
        response = client.post(
            f"/api/v1/applications/{app.id}/workable/move-stage",
            headers=headers,
            json={"target_stage": "final-interview"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["ats_writeback_status"] == "queued"
    assert response.json()["ats_writeback_job_run_id"] == 456
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["target_stage"] == "final-interview"
    assert payload["target_intent"] is None
