"""manual_action_reader: classifies recruiter events into ManualAction kinds."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.agent_runtime.manual_action_reader import read_recent_manual_actions
from app.models.role import Role
from app.models.sister_role_evaluation import SisterRoleEvaluation

from .conftest import add_event, make_world


def test_recruiter_assessment_send_classifies_as_sent_assessment(db):
    _org, role, _, app = make_world(db)
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="assessment_invite_sent",
    )
    actions = read_recent_manual_actions(
        db,
        application_id=int(app.id),
        role_id=int(role.id),
        lookback_hours=72,
    )
    assert len(actions) == 1
    assert actions[0].kind == "sent_assessment"


def test_agent_actor_events_are_ignored(db):
    _org, role, _, app = make_world(db)
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="assessment_invite_sent",
        actor_type="agent",
    )
    actions = read_recent_manual_actions(
        db,
        application_id=int(app.id),
        role_id=int(role.id),
        lookback_hours=72,
    )
    assert actions == []


def test_outcome_changed_to_rejected_classifies_as_rejected(db):
    _org, role, _, app = make_world(db)
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="application_outcome_changed",
        to_outcome="rejected",
    )
    actions = read_recent_manual_actions(
        db,
        application_id=int(app.id),
        role_id=int(role.id),
        lookback_hours=72,
    )
    assert len(actions) == 1
    assert actions[0].kind == "rejected"


def test_pipeline_stage_changed_to_interview_classifies_as_advanced(db):
    _org, role, _, app = make_world(db)
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="pipeline_stage_changed",
        to_stage="advanced",
    )
    actions = read_recent_manual_actions(
        db,
        application_id=int(app.id),
        role_id=int(role.id),
        lookback_hours=72,
    )
    assert len(actions) == 1
    assert actions[0].kind == "advanced"


def test_old_events_outside_lookback_are_excluded(db):
    _org, role, _, app = make_world(db)
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
        db,
        application_id=int(app.id),
        role_id=int(role.id),
        lookback_hours=72,
    )
    assert actions == []


def test_zero_lookback_returns_empty(db):
    _org, role, _, app = make_world(db)
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="assessment_invite_sent",
    )
    actions = read_recent_manual_actions(
        db,
        application_id=int(app.id),
        role_id=int(role.id),
        lookback_hours=0,
    )
    assert actions == []


def test_manual_actions_are_isolated_by_logical_role_on_shared_application(db):
    org, owner_role, candidate, app = make_world(db)
    related_role = Role(
        organization_id=int(org.id),
        name="Independent related role",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=int(owner_role.id),
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(related_role)
    db.flush()
    db.add(
        SisterRoleEvaluation(
            organization_id=int(org.id),
            role_id=int(related_role.id),
            candidate_id=int(candidate.id),
            source_application_id=int(app.id),
            ats_application_id=int(app.id),
            status="done",
            pipeline_stage="review",
            application_outcome="open",
            membership_source="initial_snapshot",
            spec_fingerprint="manual-action-role-isolation",
        )
    )
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(org.id),
        role_id=int(owner_role.id),
        event_type="pipeline_stage_changed",
        to_stage="advanced",
    )
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(org.id),
        role_id=int(related_role.id),
        event_type="assessment_invite_sent",
    )

    owner_actions = read_recent_manual_actions(
        db,
        application_id=int(app.id),
        role_id=int(owner_role.id),
        lookback_hours=72,
    )
    related_actions = read_recent_manual_actions(
        db,
        application_id=int(app.id),
        role_id=int(related_role.id),
        lookback_hours=72,
    )

    assert [action.kind for action in owner_actions] == ["advanced"]
    assert [action.kind for action in related_actions] == ["sent_assessment"]
