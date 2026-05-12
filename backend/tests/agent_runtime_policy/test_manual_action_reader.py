"""manual_action_reader: classifies recruiter events into ManualAction kinds."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.agent_runtime.manual_action_reader import read_recent_manual_actions

from .conftest import add_event, make_world


def test_recruiter_assessment_send_classifies_as_sent_assessment(db):
    _org, _role, _, app = make_world(db)
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="assessment_invite_sent",
    )
    actions = read_recent_manual_actions(
        db, application_id=int(app.id), lookback_hours=72
    )
    assert len(actions) == 1
    assert actions[0].kind == "sent_assessment"


def test_agent_actor_events_are_ignored(db):
    _org, _role, _, app = make_world(db)
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="assessment_invite_sent",
        actor_type="agent",
    )
    actions = read_recent_manual_actions(
        db, application_id=int(app.id), lookback_hours=72
    )
    assert actions == []


def test_outcome_changed_to_rejected_classifies_as_rejected(db):
    _org, _role, _, app = make_world(db)
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="application_outcome_changed",
        to_outcome="rejected",
    )
    actions = read_recent_manual_actions(
        db, application_id=int(app.id), lookback_hours=72
    )
    assert len(actions) == 1
    assert actions[0].kind == "rejected"


def test_pipeline_stage_changed_to_interview_classifies_as_advanced(db):
    _org, _role, _, app = make_world(db)
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="pipeline_stage_changed",
        to_stage="advanced",
    )
    actions = read_recent_manual_actions(
        db, application_id=int(app.id), lookback_hours=72
    )
    assert len(actions) == 1
    assert actions[0].kind == "advanced"


def test_old_events_outside_lookback_are_excluded(db):
    _org, _role, _, app = make_world(db)
    ev = add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="assessment_invite_sent",
    )
    # Backdate the event by 200 hours.
    ev.created_at = datetime.now(timezone.utc) - timedelta(hours=200)
    db.flush()
    actions = read_recent_manual_actions(
        db, application_id=int(app.id), lookback_hours=72
    )
    assert actions == []


def test_zero_lookback_returns_empty(db):
    _org, _role, _, app = make_world(db)
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="assessment_invite_sent",
    )
    actions = read_recent_manual_actions(
        db, application_id=int(app.id), lookback_hours=0
    )
    assert actions == []
