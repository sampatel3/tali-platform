"""Assessment invites keep persisted secrets out of Celery messages."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.components.notifications import tasks as notification_tasks
from app.components.notifications.email_client import EmailService
from app.domains.integrations_notifications.invite_flow import (
    _send_taali_invite_email,
)
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task


def _assessment(db) -> Assessment:
    organization = Organization(name="Queue-safe org", slug=f"queue-safe-{id(db)}")
    db.add(organization)
    db.flush()
    role = Role(organization_id=organization.id, name="Backend", source="manual")
    task = Task(
        organization_id=organization.id,
        name="Technical task",
        task_key=f"queue-safe-task-{id(db)}",
        is_active=True,
    )
    candidate = Candidate(
        organization_id=organization.id,
        email="candidate@example.com",
        full_name="Candidate",
    )
    db.add_all((role, task, candidate))
    db.flush()
    application = CandidateApplication(
        organization_id=organization.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="review",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    assessment = Assessment(
        organization_id=organization.id,
        candidate_id=candidate.id,
        task_id=task.id,
        role_id=role.id,
        application_id=application.id,
        token="persisted-assessment-secret",
        duration_minutes=60,
        expires_at=datetime.now(timezone.utc),
    )
    db.add(assessment)
    db.commit()
    return assessment


def test_invite_producer_queues_only_persisted_identity(monkeypatch):
    monkeypatch.setattr(
        "app.platform.startup_validation.is_production_like", lambda _: False
    )
    with patch.object(notification_tasks.send_assessment_email, "delay") as delay:
        _send_taali_invite_email(
            candidate_email="candidate@example.com",
            candidate_name="Candidate",
            token="assessment-token-must-not-enter-broker",
            assessment_id=41,
            org_name="Acme",
            position="Backend",
            candidate_facing_brand="Acme Careers",
            reply_to="recruiter@example.com",
            idempotency_key="assessment-invite/41",
        )

    queued = dict(delay.call_args.kwargs)
    queued.pop("request_id", None)
    assert queued == {
        "assessment_id": 41,
        "reply_to": "recruiter@example.com",
        "idempotency_key": "assessment-invite/41",
    }
    assert "assessment-token-must-not-enter-broker" not in repr(delay.call_args)
    assert "candidate@example.com" not in repr(delay.call_args)


def test_invite_worker_loads_secret_after_dequeue(db, monkeypatch):
    assessment = _assessment(db)
    monkeypatch.setattr(notification_tasks.settings, "RESEND_API_KEY", "rk_test")
    monkeypatch.setattr(
        notification_tasks.settings, "EMAIL_FROM", "TAALI <noreply@taali.ai>"
    )
    monkeypatch.setattr(
        notification_tasks, "_invalidate_resend_probe", lambda error: None
    )

    with patch.object(
        EmailService,
        "send_assessment_invite",
        return_value={"success": True, "email_id": "email-queue-safe"},
    ) as send:
        result = notification_tasks.send_assessment_email.apply(
            kwargs={
                "assessment_id": int(assessment.id),
                "idempotency_key": f"assessment-invite/{int(assessment.id)}",
            },
            retries=0,
        ).get()

    assert result["success"] is True
    assert send.call_args.kwargs["token"] == "persisted-assessment-secret"
    assert send.call_args.kwargs["candidate_email"] == "candidate@example.com"
