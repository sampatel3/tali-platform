"""override_decision dispatches the alternative action when override_action is set."""

from __future__ import annotations

from unittest.mock import patch

from app.actions import override_decision
from app.actions.types import Actor
from app.models.agent_decision import AgentDecision
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User

from .conftest import make_world


def _make_user(db, org) -> User:
    u = User(
        email=f"r-{id(db)}@x.test",
        hashed_password="x",
        full_name="R",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_decision(db, org, role, app, decision_type: str) -> AgentDecision:
    d = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=None,
        decision_type=decision_type,
        recommendation=decision_type,
        status="pending",
        reasoning="r",
        confidence=0.7,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"test:{app.id}:{decision_type}",
    )
    db.add(d)
    db.flush()
    return d


def test_override_to_reject_dispatches_reject_application(db):
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "send_assessment")

    with patch("app.actions.override_decision.reject_application.run") as mock_reject:
        override_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            override_action="reject",
            note="Missing must-have AWS Glue",
        )
        db.flush()

    assert mock_reject.called
    kwargs = mock_reject.call_args.kwargs
    assert kwargs["application_id"] == int(app.id)
    assert "Missing must-have AWS Glue" in kwargs["reason"]
    db.refresh(decision)
    assert decision.status == "overridden"
    assert decision.override_action == "reject"
    assert decision.resolution_note == "Missing must-have AWS Glue"


def test_override_to_skip_assessment_advance_dispatches_advance(db):
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "send_assessment")

    with patch("app.actions.override_decision.advance_stage.run") as mock_advance:
        override_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            override_action="skip_assessment_advance",
            note="Pre-vetted referral",
        )
        db.flush()

    assert mock_advance.called
    kwargs = mock_advance.call_args.kwargs
    assert kwargs["application_id"] == int(app.id)
    assert kwargs["to_stage"] == "advanced"
    db.refresh(decision)
    assert decision.override_action == "skip_assessment_advance"


def test_override_to_send_assessment_on_reject_dispatches_send(db):
    """Recruiter disagrees with reject and wants to send the assessment."""
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "reject")

    with patch("app.actions.override_decision.send_assessment.run") as mock_send:
        override_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            override_action="send_assessment",
            note="Strong referral; want to give them a chance",
        )
        db.flush()

    assert mock_send.called
    kwargs = mock_send.call_args.kwargs
    assert kwargs["application_id"] == int(app.id)
    db.refresh(decision)
    assert decision.status == "overridden"


def test_override_with_no_action_is_no_op_on_candidate(db):
    """Legacy "just disagree" path — no override_action passed."""
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "send_assessment")

    with (
        patch("app.actions.override_decision.reject_application.run") as mock_reject,
        patch("app.actions.override_decision.advance_stage.run") as mock_advance,
        patch("app.actions.override_decision.send_assessment.run") as mock_send,
    ):
        override_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            override_action=None,
            note="I just disagree",
        )
        db.flush()

    assert not mock_reject.called
    assert not mock_advance.called
    assert not mock_send.called
    db.refresh(decision)
    assert decision.status == "overridden"


def test_override_with_unknown_action_raises(db):
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "send_assessment")

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        override_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            override_action="banana",
            note="?",
        )
    assert excinfo.value.status_code == 422
