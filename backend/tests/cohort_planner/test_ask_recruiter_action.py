"""ask_recruiter action: idempotent open, recruiter answer, dismiss."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.actions import ask_recruiter
from app.actions.types import Actor
from app.models.agent_run import AgentRun
from app.models.job_hiring_team import JobHiringTeam
from app.models.user import User

from .conftest import make_world


def _agent_actor(db, role) -> Actor:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    db.flush()
    return Actor.agent(int(run.id))


def _recruiter_actor(db, organization_id: int) -> tuple[Actor, User]:
    user = User(
        organization_id=organization_id,
        email=f"u-{id(db)}@x.test",
        full_name="U",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        role="owner",
    )
    db.add(user)
    db.flush()
    return Actor.recruiter(user), user


def test_open_creates_a_row_for_known_kind(db):
    org, role, _, _ = make_world(db)
    actor = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        actor,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="intent_slot_missing",
        prompt="Set must_have skills",
    )
    assert row.id is not None
    assert row.is_open
    assert row.kind == "intent_slot_missing"


def test_open_is_idempotent_on_role_kind(db):
    org, role, _, _ = make_world(db)
    actor = _agent_actor(db, role)
    # Use kind='other' so the canonical-prompt override doesn't kick in —
    # this test is specifically about idempotency on (role_id, kind) and
    # the agent-supplied prompt being applied when a row already exists.
    a = ask_recruiter.open(
        db,
        actor,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="other",
        prompt="First framing",
    )
    b = ask_recruiter.open(
        db,
        actor,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="other",
        prompt="Refined framing",
    )
    assert a.id == b.id  # same row
    assert b.prompt == "Refined framing"  # but updated


def test_open_rejects_unknown_kind(db):
    org, role, _, _ = make_world(db)
    actor = _agent_actor(db, role)
    with pytest.raises(HTTPException):
        ask_recruiter.open(
            db,
            actor,
            organization_id=int(org.id),
            role_id=int(role.id),
            kind="not_a_real_kind",
            prompt="x",
        )


def test_open_rejects_recruiter_actor(db):
    org, role, _, _ = make_world(db)
    actor, _ = _recruiter_actor(db, int(org.id))
    with pytest.raises(HTTPException):
        ask_recruiter.open(
            db,
            actor,
            organization_id=int(org.id),
            role_id=int(role.id),
            kind="intent_slot_missing",
            prompt="x",
        )


def test_answer_records_response_and_actor(db):
    org, role, _, _ = make_world(db)
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="candidate_tie_break",
        prompt="Approve send?",
    )
    rec_actor, rec_user = _recruiter_actor(db, int(org.id))
    answered = ask_recruiter.answer(
        db,
        rec_actor,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "approve"},
    )
    assert answered.resolved_at is not None
    assert answered.response == {"value": "approve"}
    assert answered.resolved_by_user_id == int(rec_user.id)
    assert not answered.is_open


@pytest.mark.parametrize(
    "kind",
    ["missing_job_spec", "missing_cv", "cv_unreadable", "task_assignment_missing"],
)
def test_answer_rejects_questions_that_require_observed_external_state(db, kind):
    org, role, _, _ = make_world(db)
    row = ask_recruiter.open(
        db,
        _agent_actor(db, role),
        organization_id=int(org.id),
        role_id=int(role.id),
        kind=kind,
        prompt="Complete the required setup",
    )
    recruiter, _ = _recruiter_actor(db, int(org.id))

    with pytest.raises(HTTPException) as exc_info:
        ask_recruiter.answer(
            db,
            recruiter,
            organization_id=int(org.id),
            needs_input_id=int(row.id),
            response={"value": "done"},
            expected_version=int(role.version or 1),
        )

    assert exc_info.value.status_code == 422
    assert row.resolved_at is None
    assert row.response is None


def test_answer_rejects_already_answered(db):
    org, role, _, _ = make_world(db)
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="intent_slot_missing",
        prompt="x",
    )
    rec, _ = _recruiter_actor(db, int(org.id))
    ask_recruiter.answer(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "ok"},
    )
    with pytest.raises(HTTPException):
        ask_recruiter.answer(
            db,
            rec,
            organization_id=int(org.id),
            needs_input_id=int(row.id),
            expected_version=int(role.version or 1),
            response={"value": "second answer"},
        )


@pytest.mark.parametrize("team_role", ["interviewer", "coordinator"])
def test_answer_denies_non_controlling_job_team_roles(db, team_role):
    org, role, _, _ = make_world(db)
    role.score_threshold = 55
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="threshold_ambiguous",
        prompt="Use 30?",
    )
    user = User(
        organization_id=org.id,
        email=f"{team_role}-{id(db)}@x.test",
        full_name=team_role,
        hashed_password="x",
        is_active=True,
        is_verified=True,
        role="member",
    )
    db.add(user)
    db.flush()
    db.add(
        JobHiringTeam(
            organization_id=org.id,
            role_id=role.id,
            user_id=user.id,
            team_role=team_role,
        )
    )
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        ask_recruiter.answer(
            db,
            Actor.recruiter(user),
            organization_id=int(org.id),
            needs_input_id=int(row.id),
            expected_version=int(role.version or 1),
            response={"value": "30"},
        )

    assert exc_info.value.status_code == 403
    assert row.is_open
    assert role.score_threshold == 55


def test_dismiss_is_idempotent(db):
    org, role, _, _ = make_world(db)
    agent = _agent_actor(db, role)
    row = ask_recruiter.open(
        db,
        agent,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="intent_slot_missing",
        prompt="x",
    )
    rec, _ = _recruiter_actor(db, int(org.id))
    a = ask_recruiter.dismiss(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
    )
    b = ask_recruiter.dismiss(
        db,
        rec,
        organization_id=int(org.id),
        needs_input_id=int(row.id),
    )
    assert a.id == b.id
    assert a.dismissed_at is not None
