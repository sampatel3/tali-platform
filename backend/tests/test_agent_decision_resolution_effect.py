"""Grounded Recent Decisions effects for ordinary and independent roles."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from tests.conftest import auth_headers


def _application(db, *, organization_id: int, role_id: int, email: str):
    candidate = Candidate(
        organization_id=organization_id,
        email=email,
        full_name=email.split("@", 1)[0],
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=int(candidate.id),
        role_id=role_id,
        source="manual",
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
    )
    db.add(application)
    db.flush()
    return candidate, application


def _decision(
    db,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
    suffix: str,
    decision_type: str = "advance_to_interview",
):
    decision = AgentDecision(
        organization_id=organization_id,
        role_id=role_id,
        application_id=application_id,
        decision_type=decision_type,
        recommendation=decision_type,
        status="approved",
        reasoning="Grounded fixture recommendation.",
        confidence=0.9,
        model_version="test",
        prompt_version="test",
        idempotency_key=f"resolution-effect:{role_id}:{application_id}:{suffix}",
        resolved_at=datetime.now(timezone.utc),
    )
    db.add(decision)
    db.flush()
    return decision


def _event(
    db,
    *,
    application: CandidateApplication,
    role_id: int,
    decision: AgentDecision,
    event_type: str,
    effect_status: str,
    to_stage: str | None = None,
    to_outcome: str | None = None,
    target_stage: str | None = None,
    metadata: dict | None = None,
):
    event = CandidateApplicationEvent(
        organization_id=int(application.organization_id),
        application_id=int(application.id),
        role_id=role_id,
        agent_decision_id=int(decision.id),
        event_type=event_type,
        from_stage="review",
        to_stage=to_stage,
        from_outcome="open",
        to_outcome=to_outcome,
        target_stage=target_stage,
        effect_status=effect_status,
        actor_type="agent",
        event_metadata=metadata,
    )
    db.add(event)
    db.flush()
    return event


def _payloads(client, headers, role_id: int):
    response = client.get(
        f"/api/v1/agent-decisions?status=decided&role_id={role_id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return {int(row["id"]): row for row in response.json()}


def test_logical_advance_does_not_confirm_requested_ats_target(db):
    from app.domains.assessments_runtime.pipeline_event_service import append_event

    organization = Organization(
        name="Target Separation Org",
        slug="target-separation-org",
    )
    db.add(organization)
    db.flush()
    candidate = Candidate(
        organization_id=int(organization.id),
        email="target-separation@example.com",
        full_name="Target Separation",
    )
    role = Role(
        organization_id=int(organization.id),
        name="Target separation",
        source="manual",
    )
    db.add_all([candidate, role])
    db.flush()
    application = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        source="manual",
        pipeline_stage="review",
        application_outcome="open",
    )
    db.add(application)
    db.flush()

    logical_event = append_event(
        db,
        app=application,
        event_type="pipeline_stage_changed",
        actor_type="recruiter",
        from_stage="review",
        to_stage="advanced",
        metadata={"workable_target_stage": "Technical Interview"},
    )
    provider_event = append_event(
        db,
        app=application,
        event_type="workable_moved",
        actor_type="recruiter",
        target_stage="Technical Interview",
    )

    assert logical_event.target_stage == "advanced"
    assert provider_event.target_stage == "Technical Interview"


def test_approved_decision_without_action_event_is_not_reported_as_completed(
    client, db
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    role = Role(
        organization_id=int(user.organization_id),
        name="Grounded ordinary role",
        source="manual",
    )
    db.add(role)
    db.flush()
    _, application = _application(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role.id),
        email="ordinary-unconfirmed@example.com",
    )
    decision = _decision(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role.id),
        application_id=int(application.id),
        suffix="unconfirmed",
    )
    db.commit()

    effect = _payloads(client, headers, int(role.id))[int(decision.id)][
        "resolution_effect"
    ]

    assert effect == {
        "status": "unknown",
        "action": "advance",
        "target": None,
        "occurred_at": None,
        "event_id": None,
    }

    # A linked failure to post an audit/summary note is not a failed advance.
    # Only an event whose own semantics identify the requested action may
    # certify its outcome.
    _event(
        db,
        application=application,
        role_id=int(role.id),
        decision=decision,
        event_type="workable_writeback_failed",
        effect_status="failed",
        target_stage="Technical Interview",
        metadata={"source": "decision_summary", "verdict": "advance"},
    )
    db.commit()

    after_unrelated_failure = _payloads(client, headers, int(role.id))[
        int(decision.id)
    ]["resolution_effect"]
    assert after_unrelated_failure["status"] == "unknown"
    assert after_unrelated_failure["event_id"] is None


def test_resolution_effect_reports_confirmed_and_failed_ordinary_actions(
    client, db
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    role = Role(
        organization_id=int(user.organization_id),
        name="Ordinary action effects",
        source="manual",
    )
    db.add(role)
    db.flush()
    _, confirmed_app = _application(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role.id),
        email="ordinary-confirmed@example.com",
    )
    _, failed_app = _application(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role.id),
        email="ordinary-failed@example.com",
    )
    confirmed = _decision(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role.id),
        application_id=int(confirmed_app.id),
        suffix="confirmed",
    )
    failed = _decision(
        db,
        organization_id=int(user.organization_id),
        role_id=int(role.id),
        application_id=int(failed_app.id),
        suffix="failed",
    )
    confirmed_event = _event(
        db,
        application=confirmed_app,
        role_id=int(role.id),
        decision=confirmed,
        event_type="workable_moved",
        effect_status="confirmed",
        target_stage="Technical Interview",
    )
    failed_event = _event(
        db,
        application=failed_app,
        role_id=int(role.id),
        decision=failed,
        event_type="workable_move_failed",
        effect_status="failed",
        target_stage="Technical Interview",
        metadata={"action": "move"},
    )
    db.commit()

    payloads = _payloads(client, headers, int(role.id))

    assert payloads[int(confirmed.id)]["resolution_effect"] == {
        "status": "confirmed",
        "action": "advance",
        "target": "Technical Interview",
        "occurred_at": confirmed_event.created_at.isoformat(),
        "event_id": int(confirmed_event.id),
    }
    assert payloads[int(failed.id)]["resolution_effect"] == {
        "status": "failed",
        "action": "advance",
        "target": "Technical Interview",
        "occurred_at": failed_event.created_at.isoformat(),
        "event_id": int(failed_event.id),
    }


def test_resolution_effect_is_isolated_to_independent_related_logical_role(
    client, db
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner = Role(
        organization_id=int(user.organization_id),
        name="ATS transport owner",
        source="workable",
        workable_job_id="RESOLUTION-EFFECT-OWNER",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=int(user.organization_id),
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
    )
    db.add(related)
    db.flush()
    candidate, application = _application(
        db,
        organization_id=int(user.organization_id),
        role_id=int(owner.id),
        email="related-effects@example.com",
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=int(user.organization_id),
            role_id=int(related.id),
            candidate_id=int(candidate.id),
            source_application_id=int(application.id),
            ats_application_id=int(application.id),
            status="done",
            pipeline_stage="review",
            spec_fingerprint="related-resolution-effect",
        )
    )
    wrong_role_decision = _decision(
        db,
        organization_id=int(user.organization_id),
        role_id=int(related.id),
        application_id=int(application.id),
        suffix="wrong-role-event",
    )
    confirmed_decision = _decision(
        db,
        organization_id=int(user.organization_id),
        role_id=int(related.id),
        application_id=int(application.id),
        suffix="related-confirmed",
    )
    _event(
        db,
        application=application,
        role_id=int(owner.id),
        decision=wrong_role_decision,
        event_type="pipeline_stage_changed",
        effect_status="confirmed",
        to_stage="advanced",
        target_stage="Owner interview",
    )
    related_event = _event(
        db,
        application=application,
        role_id=int(related.id),
        decision=confirmed_decision,
        event_type="role_pipeline_stage_changed",
        effect_status="confirmed",
        to_stage="advanced",
        target_stage="Related-role interview",
    )
    db.commit()

    payloads = _payloads(client, headers, int(related.id))

    assert payloads[int(wrong_role_decision.id)]["resolution_effect"]["status"] == "unknown"
    assert payloads[int(confirmed_decision.id)]["resolution_effect"] == {
        "status": "confirmed",
        "action": "advance",
        "target": "Related-role interview",
        "occurred_at": related_event.created_at.isoformat(),
        "event_id": int(related_event.id),
    }
