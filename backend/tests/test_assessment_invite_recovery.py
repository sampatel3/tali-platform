"""Agent-driven assessment invite expiry/bounce recovery."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import event

from app.actions.resend_assessment_invite import run as resend_invite
from app.actions.types import Actor
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.services.assessment_invite_recovery import (
    RECOVERY_TRIGGER_EXPIRED,
    recover_assessment_invite,
)
from app.services.email_suppression_service import is_suppressed, suppress
from app.services.resend_webhook_service import apply_resend_event


_RUN_PK = {"value": 90_000}


def _assign_agent_run_pk(mapper, connection, target):  # pragma: no cover
    if target.id is None:
        _RUN_PK["value"] += 1
        target.id = _RUN_PK["value"]


event.listen(AgentRun, "before_insert", _assign_agent_run_pk)


def _seed(
    db,
    *,
    auto_resend: bool,
    expired: bool = True,
    invite_email_id: str = "em-original",
) -> Assessment:
    suffix = uuid.uuid4().hex
    org = Organization(name="Recovery Org", slug=f"recovery-{suffix}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Backend Engineer",
        source="manual",
        agentic_mode_enabled=True,
        auto_promote=False,
        auto_resend_assessment=auto_resend,
    )
    task = Task(
        organization_id=int(org.id),
        name="Backend exercise",
        task_key=f"recovery-task-{suffix}",
        is_active=True,
    )
    db.add_all([role, task])
    db.flush()
    role.tasks.append(task)
    candidate = Candidate(
        organization_id=int(org.id),
        full_name="Alice Candidate",
        email=f"alice-{suffix}@example.test",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="invited",
        pipeline_stage="invited",
        pipeline_stage_source="agent",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    assessment = Assessment(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        application_id=int(application.id),
        task_id=int(task.id),
        token=f"assessment-{suffix}",
        status=(AssessmentStatus.EXPIRED if expired else AssessmentStatus.PENDING),
        duration_minutes=60,
        expires_at=datetime.now(timezone.utc)
        + (timedelta(minutes=-1) if expired else timedelta(days=6)),
        invite_email_id=invite_email_id,
        invite_email_status="sent",
        invite_email_send_generation=0,
    )
    db.add(assessment)
    db.commit()
    return assessment


def test_expired_invite_auto_resends_once_when_policy_authorizes(db):
    from app.components.notifications import tasks as notification_tasks

    assessment = _seed(db, auto_resend=True)
    with patch.object(
        notification_tasks.dispatch_pending_assessment_invite, "delay"
    ) as kick:
        first = recover_assessment_invite(
            db,
            assessment_id=int(assessment.id),
            trigger=RECOVERY_TRIGGER_EXPIRED,
        )
        second = recover_assessment_invite(
            db,
            assessment_id=int(assessment.id),
            trigger=RECOVERY_TRIGGER_EXPIRED,
        )

    assert first["status"] == "auto_resent"
    assert second["status"] == "not_due"
    kick.assert_called_once_with(int(assessment.id), reply_to=None)
    db.refresh(assessment)
    assert assessment.status == AssessmentStatus.PENDING
    refreshed_expiry = assessment.expires_at
    if refreshed_expiry.tzinfo is None:
        refreshed_expiry = refreshed_expiry.replace(tzinfo=timezone.utc)
    assert refreshed_expiry > datetime.now(timezone.utc) + timedelta(days=5)
    assert assessment.invite_email_send_generation == 1
    assert assessment.invite_email_id is None
    assert assessment.invite_email_status == "pending_dispatch"
    assert db.query(AgentDecision).count() == 0
    assert db.query(AgentRun).count() == 1
    assert sum(
        1
        for event in assessment.timeline or []
        if event.get("event_type") == "assessment_invite_recovery_auto_resent"
    ) == 1


def test_expired_invite_without_auto_policy_emits_exactly_one_hitl(db):
    assessment = _seed(db, auto_resend=False)

    first = recover_assessment_invite(
        db,
        assessment_id=int(assessment.id),
        trigger=RECOVERY_TRIGGER_EXPIRED,
    )
    second = recover_assessment_invite(
        db,
        assessment_id=int(assessment.id),
        trigger=RECOVERY_TRIGGER_EXPIRED,
    )

    assert first["status"] == "awaiting_recruiter_approval"
    assert second["status"] == "awaiting_recruiter_approval"
    assert second["deduplicated"] is True
    decisions = (
        db.query(AgentDecision)
        .filter(AgentDecision.decision_type == "resend_assessment_invite")
        .all()
    )
    assert len(decisions) == 1
    assert decisions[0].status == "pending"
    assert decisions[0].evidence["assessment_id"] == int(assessment.id)
    assert decisions[0].evidence["auto_resend_authorized"] is False
    assert "not enabled" in decisions[0].evidence["auto_resend_hold_reason"]
    assert db.query(AgentRun).count() == 1
    db.refresh(assessment)
    assert assessment.status == AssessmentStatus.EXPIRED
    assert assessment.invite_email_send_generation == 0


def test_duplicate_bounce_webhook_preserves_tracking_and_queues_one_hitl(db):
    assessment = _seed(db, auto_resend=True, expired=False, invite_email_id="em-bounce")
    recipient = assessment.candidate.email
    payload = {
        "type": "email.bounced",
        "data": {"email_id": "em-bounce", "to": [recipient]},
    }

    first = apply_resend_event(db, payload)
    second = apply_resend_event(db, payload)

    assert first["recovery"]["status"] == "awaiting_recruiter_approval"
    assert second["recovery"]["deduplicated"] is True
    db.refresh(assessment)
    assert assessment.invite_email_status == "bounced"
    assert assessment.invite_bounced_at is not None
    assert assessment.invite_email_id == "em-bounce"
    assert assessment.invite_email_send_generation == 0
    assert is_suppressed(
        db,
        email=recipient,
        organization_id=int(assessment.organization_id),
    ) == "bounced"
    decisions = db.query(AgentDecision).all()
    assert len(decisions) == 1
    assert decisions[0].status == "pending"
    assert decisions[0].evidence["requires_candidate_email_review"] is True
    assert decisions[0].evidence["suppression_reason"] == "bounced"
    assert db.query(AgentRun).count() == 1
    failures = [
        event
        for event in assessment.timeline or []
        if event.get("event_type") == "assessment_invite_delivery_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["recipient"] == recipient


def test_suppressed_address_is_blocked_at_shared_resend_action(db):
    assessment = _seed(db, auto_resend=True, expired=False)
    suppress(
        db,
        email=assessment.candidate.email,
        reason="bounced",
        source="test",
        organization_id=None,
    )

    result = resend_invite(
        db,
        Actor.system(),
        organization_id=int(assessment.organization_id),
        assessment_id=int(assessment.id),
    )

    assert result.status == "blocked"
    assert "suppressed" in str(result.detail)
    db.refresh(assessment)
    assert assessment.invite_email_send_generation == 0
    assert assessment.invite_email_id == "em-original"


def test_second_expiry_hits_retry_loop_rail_and_queues_one_hitl(db):
    assessment = _seed(db, auto_resend=True)
    assessment.timeline = [
        {
            "event_type": "assessment_invite_recovery_auto_resent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "incident_key": "prior-expiry",
        }
    ]
    db.commit()

    first = recover_assessment_invite(
        db,
        assessment_id=int(assessment.id),
        trigger=RECOVERY_TRIGGER_EXPIRED,
    )
    second = recover_assessment_invite(
        db,
        assessment_id=int(assessment.id),
        trigger=RECOVERY_TRIGGER_EXPIRED,
    )

    assert first["status"] == "awaiting_recruiter_approval"
    assert second["deduplicated"] is True
    decisions = db.query(AgentDecision).all()
    assert len(decisions) == 1
    assert "limit reached" in decisions[0].evidence["auto_resend_hold_reason"]
    db.refresh(assessment)
    assert assessment.invite_email_send_generation == 0


def test_cleanup_task_drives_expiry_recovery(db):
    from app.components.notifications import tasks as notification_tasks
    from app.tasks.assessment_tasks import cleanup_expired_assessments

    assessment = _seed(db, auto_resend=True)
    assessment.status = AssessmentStatus.PENDING
    db.commit()
    with patch.object(
        notification_tasks.dispatch_pending_assessment_invite, "delay"
    ) as kick:
        cleanup_expired_assessments()

    db.expire_all()
    refreshed = db.get(Assessment, int(assessment.id))
    assert refreshed.status == AssessmentStatus.PENDING
    assert refreshed.invite_email_send_generation == 1
    kick.assert_called_once_with(int(assessment.id), reply_to=None)
