"""Truthful, durable assessment invite delivery and ATS handoff tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query

from app.models.assessment import Assessment
from app.models.agent_needs_input import AgentNeedsInput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task


def _capture_postgres_lock_sql(monkeypatch) -> list[str]:
    """Compile production ORM lock queries with PostgreSQL, not test SQLite."""
    compiled: list[str] = []
    original = Query.with_for_update

    def _record(query, *args, **kwargs):
        locked = original(query, *args, **kwargs)
        compiled.append(
            str(locked.statement.compile(dialect=postgresql.dialect()))
        )
        return locked

    monkeypatch.setattr(Query, "with_for_update", _record)
    return compiled


def _make_org(
    db,
    *,
    workable_connected: bool = False,
    invite_stage_name: str = "",
    workable_writeback: bool = False,
) -> Organization:
    org = Organization(
        name="Acme",
        slug=f"org-{id(db)}",
        workable_connected=workable_connected,
        workable_access_token=("tk-1" if workable_connected else None),
        workable_subdomain=("acme" if workable_connected else None),
        workable_config={
            "workable_writeback": workable_writeback,
            "workflow_mode": "workable_hybrid",
            "invite_stage_name": invite_stage_name,
            "granted_scopes": ["r_candidates", "r_jobs", "w_candidates"],
            "workable_actor_member_id": "member-x",
        },
    )
    db.add(org)
    db.flush()
    return org


def _make_assessment(
    db, *, org: Organization, workable_candidate_id: str | None = None
) -> Assessment:
    role = Role(organization_id=org.id, name="Backend", source="manual")
    task = Task(
        name="Test Task",
        task_key=f"task-{id(db)}",
        organization_id=org.id,
        is_active=True,
    )
    db.add_all([role, task])
    db.flush()
    candidate = Candidate(
        organization_id=org.id, email="alice@x.test", full_name="Alice"
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="review",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        workable_candidate_id=workable_candidate_id,
    )
    db.add(application)
    db.flush()
    assessment = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        role_id=role.id,
        application_id=application.id,
        token="tok-abc",
        duration_minutes=60,
        expires_at=datetime.now(timezone.utc),
        workable_candidate_id=workable_candidate_id,
    )
    db.add(assessment)
    db.flush()
    return assessment


def _commit_invite_intent(db, assessment, org, *, actor_type="agent"):
    from app.components.notifications import tasks as notification_tasks
    from app.domains.integrations_notifications.invite_flow import (
        dispatch_assessment_invite,
    )

    with patch.object(notification_tasks.dispatch_pending_assessment_invite, "delay"):
        dispatch_assessment_invite(
            assessment=assessment,
            org=org,
            candidate_email=assessment.candidate.email,
            candidate_name=assessment.candidate.full_name,
            position=assessment.task.name,
            pipeline_source="recruiter" if actor_type == "recruiter" else "agent",
            pipeline_actor_type=actor_type,
            pipeline_reason="Assessment invite sent",
        )
        db.commit()


def test_invite_intent_dispatches_only_after_outer_commit(db):
    from app.components.notifications import tasks as notification_tasks
    from app.domains.integrations_notifications.invite_flow import (
        INVITE_PENDING_DISPATCH,
        dispatch_assessment_invite,
    )

    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    with patch.object(
        notification_tasks.dispatch_pending_assessment_invite, "delay"
    ) as kick, patch(
        "app.domains.integrations_notifications.invite_flow._send_taali_invite_email"
    ) as immediate_email:
        result = dispatch_assessment_invite(
            assessment=assessment,
            org=org,
            candidate_email="alice@x.test",
            candidate_name="Alice",
            position="Backend",
        )
        assert result == INVITE_PENDING_DISPATCH
        assert assessment.invite_email_status == INVITE_PENDING_DISPATCH
        assert assessment.invite_sent_at is None
        assert assessment.application.pipeline_stage == "review"
        immediate_email.assert_not_called()
        kick.assert_not_called()
        db.commit()

    kick.assert_called_once_with(int(assessment.id), reply_to=None)


def test_nested_rollback_discards_invite_intent_and_never_kicks_worker(db):
    from app.components.notifications import tasks as notification_tasks
    from app.domains.integrations_notifications.invite_flow import (
        dispatch_assessment_invite,
    )

    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    db.commit()
    with patch.object(
        notification_tasks.dispatch_pending_assessment_invite, "delay"
    ) as kick:
        with pytest.raises(RuntimeError):
            with db.begin_nested():
                dispatch_assessment_invite(
                    assessment=assessment,
                    org=org,
                    candidate_email="alice@x.test",
                    candidate_name="Alice",
                    position="Backend",
                )
                raise RuntimeError("rollback")
        db.commit()

    kick.assert_not_called()
    db.refresh(assessment)
    assert assessment.invite_email_status is None


def test_broker_queue_is_idempotent_and_does_not_claim_candidate_contact(
    db, monkeypatch
):
    from app.domains.integrations_notifications.invite_flow import (
        INVITE_PENDING_DISPATCH,
        INVITE_QUEUED,
        deliver_pending_assessment_invite,
    )

    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    assessment.invite_email_status = INVITE_PENDING_DISPATCH
    assessment.invite_email_reply_to = "recruiter@acme.test"
    db.commit()
    lock_sql = _capture_postgres_lock_sql(monkeypatch)
    with patch(
        "app.domains.integrations_notifications.invite_flow._send_taali_invite_email"
    ) as email:
        first = deliver_pending_assessment_invite(
            db, assessment_id=int(assessment.id)
        )
        second = deliver_pending_assessment_invite(
            db, assessment_id=int(assessment.id)
        )

    assert first["status"] == "queued"
    assert second["status"] == "already_claimed"
    assert email.call_count == 1
    assert email.call_args.kwargs["idempotency_key"] == (
        f"assessment-invite/{int(assessment.id)}"
    )
    db.refresh(assessment)
    assert assessment.invite_email_status == INVITE_QUEUED
    assert assessment.invite_sent_at is None
    assert assessment.application.pipeline_stage == "review"
    assert len(lock_sql) == 2
    assert all("LEFT OUTER JOIN" in statement for statement in lock_sql)
    assert all(
        statement.rsplit("FOR UPDATE", 1)[-1].strip() == "OF assessments"
        for statement in lock_sql
    )
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == assessment.application_id,
            CandidateApplicationEvent.event_type == "assessment_invite_sent",
        )
        .count()
        == 0
    )


def test_pending_dispatch_sweep_recovers_lost_postcommit_kick(db):
    from app.components.notifications import tasks as notification_tasks

    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    assessment.invite_email_status = "pending_dispatch"
    db.commit()
    with patch.object(
        notification_tasks.dispatch_pending_assessment_invite, "delay"
    ) as kick:
        result = notification_tasks.sweep_pending_assessment_invites.run(limit=10)

    assert result == {
        "scanned": 1,
        "dispatched": 1,
        "failed": 0,
        "recovered_claims": 0,
    }
    kick.assert_called_once_with(int(assessment.id))


def test_retryable_invite_sweep_scopes_postgres_lock_and_recovers_due_row(
    db, monkeypatch
):
    from app.components.notifications import tasks as notification_tasks

    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    assessment.invite_email_status = "retry_wait"
    assessment.invite_email_next_attempt_at = datetime.now(
        timezone.utc
    ) - timedelta(seconds=1)
    db.commit()

    lock_sql = _capture_postgres_lock_sql(monkeypatch)
    monkeypatch.setattr(
        notification_tasks,
        "_default_worker_resend_ready",
        lambda: (True, None),
    )
    monkeypatch.setattr("app.platform.database.SessionLocal", lambda: db)
    with patch.object(notification_tasks.send_assessment_email, "delay") as send:
        result = notification_tasks.sweep_retryable_assessment_invites.run(
            limit=10
        )

    assert result == {
        "gated": False,
        "reason": None,
        "scanned": 1,
        "leased": 1,
        "dispatched": 1,
        "failed": 0,
    }
    send.assert_called_once()
    assert len(lock_sql) == 1
    assert "LEFT OUTER JOIN" in lock_sql[0]
    assert (
        lock_sql[0].rsplit("FOR UPDATE", 1)[-1].strip()
        == "OF assessments SKIP LOCKED"
    )


def test_provider_success_atomically_confirms_pipeline_and_handoff_outbox(
    db, monkeypatch
):
    from app.platform.config import settings as cfg
    from app.services.assessment_invite_delivery import (
        confirm_assessment_invite_provider_success,
    )

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)
    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Assessment",
        workable_writeback=True,
    )
    assessment = _make_assessment(db, org=org, workable_candidate_id="wkbl-confirm")
    _commit_invite_intent(db, assessment, org)

    result = confirm_assessment_invite_provider_success(
        db,
        assessment_id=int(assessment.id),
        email_id="em-confirmed",
        expected_generation=0,
    )

    assert result["confirmed"] is True
    assert result["handoff_pending"] is True
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert row.invite_email_id == "em-confirmed"
    assert row.invite_email_status == "sent"
    assert row.invite_sent_at is not None
    assert row.application.pipeline_stage == "invited"
    assert row.invite_workable_handoff_status == "pending"
    assert row.invite_workable_handoff_generation == 0
    assert row.invite_workable_handoff_stage == "Assessment"
    event_types = {
        item.event_type
        for item in db.query(CandidateApplicationEvent).filter(
            CandidateApplicationEvent.application_id == row.application_id
        )
    }
    assert {
        "pipeline_initialized",
        "pipeline_stage_changed",
        "assessment_invite_sent",
    }.issubset(event_types)


def test_provider_success_confirmation_is_idempotent(db):
    from app.services.assessment_invite_delivery import (
        confirm_assessment_invite_provider_success,
    )

    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    _commit_invite_intent(db, assessment, org)
    first = confirm_assessment_invite_provider_success(
        db,
        assessment_id=int(assessment.id),
        email_id="em-once",
        expected_generation=0,
    )
    version = assessment.application.version
    second = confirm_assessment_invite_provider_success(
        db,
        assessment_id=int(assessment.id),
        email_id="em-once",
        expected_generation=0,
    )

    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    db.refresh(assessment.application)
    assert assessment.application.version == version
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == assessment.application_id,
            CandidateApplicationEvent.event_type == "assessment_invite_sent",
        )
        .count()
        == 1
    )


def test_workable_note_retry_uses_stage_checkpoint_and_never_resends_email(
    db, monkeypatch
):
    from app.components.notifications import tasks as notification_tasks
    from app.platform.config import settings as cfg
    from app.services.assessment_invite_delivery import (
        confirm_assessment_invite_provider_success,
    )
    from app.services.assessment_invite_workable_handoff import (
        run_assessment_invite_workable_handoff,
    )

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(cfg, "FRONTEND_URL", "https://app.taali.test")
    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Assessment",
        workable_writeback=True,
    )
    assessment = _make_assessment(db, org=org, workable_candidate_id="wkbl-retry")
    _commit_invite_intent(db, assessment, org)
    confirm_assessment_invite_provider_success(
        db,
        assessment_id=int(assessment.id),
        email_id="em-retry",
        expected_generation=0,
    )

    with patch(
        "app.services.assessment_invite_workable_handoff.move_candidate_in_workable",
        return_value={"success": True},
    ) as move, patch(
        "app.services.assessment_invite_workable_handoff.build_workable_adapter"
    ) as adapter_factory, patch.object(
        notification_tasks.send_assessment_email, "delay"
    ) as email:
        adapter = adapter_factory.return_value
        adapter.post_candidate_comment.side_effect = [
            {"success": False, "error": "Workable 503"},
            {"success": True},
        ]
        first = run_assessment_invite_workable_handoff(
            db, assessment_id=int(assessment.id), generation=0
        )
        db.expire_all()
        row = db.query(Assessment).filter(Assessment.id == assessment.id).one()
        assert first["status"] == "retry_wait"
        assert row.invite_workable_stage_moved_at is not None
        assert row.invite_workable_note_posted_at is None
        row.invite_workable_handoff_next_attempt_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=1)
        db.commit()
        second = run_assessment_invite_workable_handoff(
            db, assessment_id=int(assessment.id), generation=0
        )

    assert second["status"] == "succeeded"
    assert move.call_count == 1
    assert adapter.post_candidate_comment.call_count == 2
    email.assert_not_called()
    _, _, note = adapter.post_candidate_comment.call_args.args
    assert f"https://app.taali.test/assessment/{assessment.id}" in note
    assert "assessment-invite/" in note


def test_successful_workable_handoff_redelivery_is_deduplicated(db, monkeypatch):
    from app.platform.config import settings as cfg
    from app.services.assessment_invite_delivery import (
        confirm_assessment_invite_provider_success,
    )
    from app.services.assessment_invite_workable_handoff import (
        run_assessment_invite_workable_handoff,
    )

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)
    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Assessment",
        workable_writeback=True,
    )
    assessment = _make_assessment(db, org=org, workable_candidate_id="wkbl-once")
    _commit_invite_intent(db, assessment, org)
    confirm_assessment_invite_provider_success(
        db,
        assessment_id=int(assessment.id),
        email_id="em-once",
        expected_generation=0,
    )
    with patch(
        "app.services.assessment_invite_workable_handoff.move_candidate_in_workable",
        return_value={"success": True},
    ) as move, patch(
        "app.services.assessment_invite_workable_handoff.build_workable_adapter"
    ) as adapter_factory:
        adapter_factory.return_value.post_candidate_comment.return_value = {
            "success": True
        }
        first = run_assessment_invite_workable_handoff(
            db, assessment_id=int(assessment.id), generation=0
        )
        second = run_assessment_invite_workable_handoff(
            db, assessment_id=int(assessment.id), generation=0
        )

    assert first["status"] == "succeeded"
    assert second == {"status": "succeeded", "deduplicated": True}
    assert move.call_count == 1
    assert adapter_factory.return_value.post_candidate_comment.call_count == 1


def _enable_bullhorn_invite_handoff(org, assessment) -> None:
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    assessment.role.source = "bullhorn"
    assessment.role.bullhorn_job_order_id = "job-42"
    assessment.candidate.bullhorn_candidate_id = "candidate-7"
    assessment.application.source = "bullhorn"
    assessment.application.bullhorn_job_submission_id = "submission-9"


def test_confirmed_bullhorn_invite_uses_serialized_provider_ops(db, monkeypatch):
    from app.platform.config import settings as cfg
    from app.services.assessment_invite_delivery import (
        confirm_assessment_invite_provider_success,
    )
    from app.services.assessment_invite_workable_handoff import (
        run_assessment_invite_workable_handoff,
    )
    from app.services.workable_op_runner import OP_MOVE_STAGE, OP_POST_NOTE

    monkeypatch.setattr(cfg, "BULLHORN_ENABLED", True)
    monkeypatch.setattr(cfg, "FRONTEND_URL", "https://app.taali.test")
    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    _enable_bullhorn_invite_handoff(org, assessment)
    _commit_invite_intent(db, assessment, org)

    confirmed = confirm_assessment_invite_provider_success(
        db,
        assessment_id=int(assessment.id),
        email_id="em-bullhorn",
        expected_generation=0,
    )
    assert confirmed["handoff_pending"] is True
    db.refresh(assessment)
    assert assessment.invite_channel == "bullhorn_pending"
    assert assessment.invite_workable_handoff_stage == "invited"

    with patch(
        "app.services.workable_op_runner.execute_op",
        side_effect=[
            {"status": "ok", "application_id": assessment.application_id},
            {"status": "ok", "application_id": assessment.application_id},
        ],
    ) as execute:
        result = run_assessment_invite_workable_handoff(
            db, assessment_id=int(assessment.id), generation=0
        )

    assert result["status"] == "succeeded"
    assert [call.kwargs["op_type"] for call in execute.call_args_list] == [
        OP_MOVE_STAGE,
        OP_POST_NOTE,
    ]
    move_payload = execute.call_args_list[0].kwargs["payload"]
    assert move_payload["target_intent"] == "invited"
    assert move_payload["actor_type"] == "agent"
    assert move_payload["source"] == "agent"
    note_payload = execute.call_args_list[1].kwargs["payload"]
    assert f"https://app.taali.test/assessment/{assessment.id}" in note_payload["body"]
    assert note_payload["actor_type"] == "agent"
    assert note_payload["source"] == "agent"
    db.refresh(assessment)
    assert assessment.invite_channel == "bullhorn_hybrid"
    assert assessment.invite_sent_at is not None
    assert assessment.application.pipeline_stage == "invited"


def test_confirmed_bullhorn_handoff_uses_bullhorn_sync_mutex(db, monkeypatch):
    from app.components.integrations.bullhorn.sync_runner import (
        BULLHORN_ORG_MUTEX_NAMESPACE,
    )
    from app.components.notifications import tasks as notification_tasks
    from app.platform.config import settings as cfg
    from app.services.assessment_invite_delivery import (
        confirm_assessment_invite_provider_success,
    )
    from app.tasks import assessment_tasks

    monkeypatch.setattr(cfg, "BULLHORN_ENABLED", True)
    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    _enable_bullhorn_invite_handoff(org, assessment)
    _commit_invite_intent(db, assessment, org)
    confirm_assessment_invite_provider_success(
        db,
        assessment_id=int(assessment.id),
        email_id="em-bullhorn-mutex",
        expected_generation=0,
    )

    acquired: list[dict] = []

    def _acquire(*args, **kwargs):
        acquired.append(kwargs)
        return (object(), "bullhorn-test-lock", None)

    monkeypatch.setattr(assessment_tasks, "_acquire_workable_org_mutex", _acquire)
    monkeypatch.setattr(assessment_tasks, "_release_workable_org_mutex", lambda *_: None)
    monkeypatch.setattr(assessment_tasks, "mark_workable_op_pending", lambda *_: None)
    with patch(
        "app.services.assessment_invite_workable_handoff.run_assessment_invite_workable_handoff",
        return_value={"status": "succeeded"},
    ):
        result = notification_tasks.dispatch_assessment_invite_workable_handoff.run(
            int(assessment.id), 0
        )

    assert result == {"status": "succeeded"}
    assert acquired[0]["namespace"] == BULLHORN_ORG_MUTEX_NAMESPACE
    assert acquired[0]["source"].startswith("bullhorn_op:")


def test_bullhorn_invite_missing_mapping_preserves_email_and_surfaces_hitl(
    db, monkeypatch
):
    from app.platform.config import settings as cfg
    from app.services.assessment_invite_delivery import (
        confirm_assessment_invite_provider_success,
    )
    from app.services.assessment_invite_workable_handoff import (
        run_assessment_invite_workable_handoff,
    )
    from app.services.workable_actions_service import WorkableWritebackError

    monkeypatch.setattr(cfg, "BULLHORN_ENABLED", True)
    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    _enable_bullhorn_invite_handoff(org, assessment)
    _commit_invite_intent(db, assessment, org)
    confirm_assessment_invite_provider_success(
        db,
        assessment_id=int(assessment.id),
        email_id="em-bullhorn-mapping",
        expected_generation=0,
    )

    with patch(
        "app.services.workable_op_runner.execute_op",
        side_effect=WorkableWritebackError(
            action="move",
            code="needs_mapping",
            message="No Bullhorn status is mapped for Taali intent 'invited'",
            retriable=False,
        ),
    ):
        result = run_assessment_invite_workable_handoff(
            db, assessment_id=int(assessment.id), generation=0
        )

    assert result["status"] == "failed"
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert row.invite_sent_at is not None
    assert row.invite_email_id == "em-bullhorn-mapping"
    assert row.application.pipeline_stage == "invited"
    assert row.invite_channel == "bullhorn_partial"
    assert "needs_mapping" in row.invite_workable_handoff_last_error
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == row.application_id,
            CandidateApplicationEvent.event_type
            == "assessment_invite_bullhorn_handoff_failed",
        )
        .count()
        == 1
    )
    hitl = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == row.role_id,
            AgentNeedsInput.subject_id == row.application_id,
            AgentNeedsInput.resolved_at.is_(None),
        )
        .one()
    )
    assert "Bullhorn assessment/invited stage mapped" in hitl.prompt
    assert hitl.response_schema["link_label"] == "Open Bullhorn stage mapping"


@pytest.mark.parametrize(
    ("stages", "error_fragment"),
    [
        ([], "No cached Workable stage has kind=assessment"),
        (
            [
                {"slug": "take-home", "name": "Take Home", "kind": "assessment"},
                {"slug": "coding", "name": "Coding", "kind": "assessment"},
            ],
            "Multiple cached Workable stages have kind=assessment",
        ),
    ],
)
def test_workable_invite_without_unique_target_is_durable_hitl_after_email(
    db, monkeypatch, stages, error_fragment
):
    from app.platform.config import settings as cfg
    from app.services.assessment_invite_delivery import (
        confirm_assessment_invite_provider_success,
    )
    from app.services.assessment_invite_workable_handoff import (
        run_assessment_invite_workable_handoff,
    )

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)
    org = _make_org(
        db,
        workable_connected=True,
        workable_writeback=True,
        invite_stage_name="",
    )
    assessment = _make_assessment(
        db, org=org, workable_candidate_id="workable-needs-map"
    )
    assessment.role.workable_job_id = "workable-role"
    assessment.role.workable_stages = stages
    _commit_invite_intent(db, assessment, org)

    confirmed = confirm_assessment_invite_provider_success(
        db,
        assessment_id=int(assessment.id),
        email_id="em-workable-needs-map",
        expected_generation=0,
    )
    assert confirmed["handoff_pending"] is True

    result = run_assessment_invite_workable_handoff(
        db, assessment_id=int(assessment.id), generation=0
    )

    assert result["status"] == "failed"
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert row.invite_email_id == "em-workable-needs-map"
    assert row.invite_sent_at is not None
    assert row.invite_channel == "workable_partial"
    assert "needs_mapping" in row.invite_workable_handoff_last_error
    assert error_fragment in row.invite_workable_handoff_last_error
    hitl = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == row.role_id,
            AgentNeedsInput.subject_id == row.application_id,
            AgentNeedsInput.resolved_at.is_(None),
        )
        .one()
    )
    assert hitl.response_schema["link_label"] == "Open Workable stage mapping"


def test_stale_workable_generation_cannot_touch_new_handoff(db, monkeypatch):
    from app.platform.config import settings as cfg
    from app.services.assessment_invite_workable_handoff import (
        run_assessment_invite_workable_handoff,
    )

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)
    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    assessment.invite_workable_handoff_generation = 1
    assessment.invite_workable_handoff_status = "pending"
    assessment.invite_workable_handoff_stage = "Assessment"
    db.commit()
    with patch(
        "app.services.assessment_invite_workable_handoff.move_candidate_in_workable"
    ) as move:
        result = run_assessment_invite_workable_handoff(
            db, assessment_id=int(assessment.id), generation=0
        )

    assert result["status"] == "missing_or_superseded"
    move.assert_not_called()
    db.refresh(assessment)
    assert assessment.invite_workable_handoff_generation == 1
    assert assessment.invite_workable_handoff_status == "pending"


def test_workable_handoff_sweep_recovers_pending_rows(db):
    from app.components.notifications import tasks as notification_tasks

    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    assessment.invite_email_confirmed_generation = 0
    assessment.invite_workable_handoff_generation = 0
    assessment.invite_workable_handoff_status = "pending"
    db.commit()
    with patch.object(
        notification_tasks.dispatch_assessment_invite_workable_handoff, "delay"
    ) as kick:
        result = notification_tasks.sweep_assessment_invite_workable_handoffs.run(
            limit=10
        )

    assert result == {"scanned": 1, "dispatched": 1, "failed": 0}
    kick.assert_called_once_with(int(assessment.id), 0)


def test_idempotency_key_changes_only_for_explicit_resend(db):
    from app.domains.integrations_notifications.invite_flow import (
        assessment_invite_idempotency_key,
    )

    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    root = f"assessment-invite/{int(assessment.id)}"
    assert assessment_invite_idempotency_key(assessment) == root
    assessment.invite_email_retry_count = 5
    assessment.invite_email_status = "retry_wait"
    assert assessment_invite_idempotency_key(assessment) == root
    assessment.invite_email_send_generation = 1
    assert assessment_invite_idempotency_key(assessment) == f"{root}/resend/1"


def test_explicit_resend_queues_a_new_provider_generation(db):
    from app.actions.resend_assessment_invite import run
    from app.actions.types import Actor

    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    assessment.role.agentic_mode_enabled = True
    assessment.role.tasks.append(assessment.task)
    assessment.invite_email_id = "em-original"
    assessment.invite_delivered_at = datetime.now(timezone.utc)

    result = run(
        db,
        Actor.system(),
        organization_id=int(org.id),
        assessment_id=int(assessment.id),
    )

    assert result.status == "queued"
    assert assessment.invite_email_send_generation == 1
    assert assessment.invite_email_id is None
    assert assessment.invite_delivered_at is None
    assert assessment.application.pipeline_stage == "review"


def test_explicit_resend_reopens_an_expired_assessment_link(db):
    from app.actions.resend_assessment_invite import run
    from app.actions.types import Actor
    from app.models.assessment import AssessmentStatus

    org = _make_org(db)
    assessment = _make_assessment(db, org=org)
    assessment.role.agentic_mode_enabled = True
    assessment.role.tasks.append(assessment.task)
    assessment.status = AssessmentStatus.EXPIRED
    assessment.expires_at = datetime.now(timezone.utc) - timedelta(days=1)

    result = run(
        db,
        Actor.system(),
        organization_id=int(org.id),
        assessment_id=int(assessment.id),
    )

    assert result.status == "queued"
    assert assessment.status == AssessmentStatus.PENDING
    assert assessment.expires_at > datetime.now(timezone.utc)
