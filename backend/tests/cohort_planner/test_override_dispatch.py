"""override_decision dispatches the alternative action when override_action is set."""

from __future__ import annotations

from unittest.mock import patch

from app.actions import override_decision
from app.actions.types import Actor
from app.models.agent_decision import AgentDecision
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


def test_reclassify_to_advance_queue_requeues_without_advancing(db):
    """"Skip & advance" reclassifies the card into the advance queue: it
    becomes a PENDING advance_to_interview decision, with no stage transition
    and no Workable write (the advance flow collects the stage later)."""
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "send_assessment")

    with patch("app.actions.advance_stage.run") as mock_advance:
        result = override_decision.reclassify_to_advance_queue(
            db,
            Actor.recruiter(user),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            note="Pre-vetted referral",
            expected_decision_type="send_assessment",
        )
        db.flush()

    assert not mock_advance.called  # no stage transition / no Workable write
    db.refresh(decision)
    assert decision.status == "pending"  # stays in the queue for approval
    assert decision.decision_type == "advance_to_interview"
    assert decision.recommendation == "advance_to_interview"
    assert (decision.evidence or {}).get("reclassified_from") == "send_assessment"
    assert (decision.evidence or {}).get("recruiter_skip_note") == "Pre-vetted referral"
    assert result.id == decision.id


def test_reclassify_to_advance_queue_is_noop_when_already_advance(db):
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "advance_to_interview")

    override_decision.reclassify_to_advance_queue(
        db,
        Actor.recruiter(user),
        organization_id=int(org.id),
        decision_id=int(decision.id),
        expected_decision_type="advance_to_interview",
    )
    db.refresh(decision)
    assert decision.status == "pending"
    assert decision.decision_type == "advance_to_interview"


def test_reclassify_to_advance_queue_rejects_stale_already_advance_decision(db):
    import pytest
    from fastapi import HTTPException

    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "advance_to_interview")

    with pytest.raises(HTTPException) as exc_info:
        override_decision.reclassify_to_advance_queue(
            db,
            Actor.recruiter(user),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            expected_decision_type="send_assessment",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "DECISION_CHANGED"
    db.refresh(decision)
    assert decision.status == "pending"
    assert decision.decision_type == "advance_to_interview"


def test_override_to_send_assessment_on_reject_dispatches_send(db):
    """Recruiter disagrees with reject and wants to send the assessment."""
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "reject")

    fake_send_result = type("_R", (), {"status": "sent", "assessment_id": 9})()
    with patch(
        "app.actions.override_decision.send_assessment.run",
        return_value=fake_send_result,
    ) as mock_send:
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


def test_override_to_send_assessment_rejects_when_dispatch_fails(db):
    """If send_assessment returns a non-sent status (misconfigured /
    insufficient_credits / no_candidate), the override raises 409 and
    the decision stays pending — recruiter doesn't lose the queue row
    on a silent failure (Codex #192)."""
    import pytest
    from fastapi import HTTPException

    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "reject")

    fake_fail = type("_R", (), {"status": "misconfigured", "assessment_id": None})()
    with patch(
        "app.actions.override_decision.send_assessment.run",
        return_value=fake_fail,
    ):
        with pytest.raises(HTTPException) as excinfo:
            override_decision.run(
                db,
                Actor.recruiter(user),
                organization_id=int(org.id),
                decision_id=int(decision.id),
                override_action="send_assessment",
                note="Try sending",
            )
    assert excinfo.value.status_code == 409
    db.refresh(decision)
    assert decision.status == "pending"


def test_override_with_manual_review_legacy_is_accepted(db):
    """Legacy clients still pass override_action='manual_review' — it
    should resolve as a no-op override (Codex #192), not 422."""
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    decision = _make_decision(db, org, role, app, "send_assessment")
    override_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=int(org.id),
        decision_id=int(decision.id),
        override_action="manual_review",
        note="Legacy path",
    )
    db.flush()
    db.refresh(decision)
    assert decision.status == "overridden"
    assert decision.override_action == "manual_review"


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
