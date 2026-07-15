"""Focused tests for role-scoped recruiter-input Agent Chat commands."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.agent_chat import recruiter_inputs
from app.models.agent_needs_input import AgentNeedsInput
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _world(db):
    org = Organization(name="Recruiter input org", slug=f"recruiter-input-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"recruiter-input-{id(db)}@example.test",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=int(org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    first = Role(organization_id=int(org.id), name="Backend", source="manual")
    second = Role(organization_id=int(org.id), name="Design", source="manual")
    db.add_all([user, first, second])
    db.flush()
    return org, user, first, second


def _request(db, role, **overrides) -> AgentNeedsInput:
    values = {
        "organization_id": int(role.organization_id),
        "role_id": int(role.id),
        "kind": "other",
        "prompt": "What should I do?",
    }
    values.update(overrides)
    row = AgentNeedsInput(**values)
    db.add(row)
    db.flush()
    return row


def test_list_returns_only_open_requests_for_conversation_role(db):
    _, _, role, other_role = _world(db)
    open_row = _request(db, role, prompt="Open")
    answered = _request(db, role, prompt="Answered", response={"value": "x"})
    answered.resolved_at = open_row.created_at
    dismissed = _request(db, role, prompt="Dismissed")
    dismissed.dismissed_at = open_row.created_at
    _request(db, other_role, prompt="Other role")
    db.flush()

    result = recruiter_inputs.list_open_recruiter_inputs(db, role=role)

    assert result["role_id"] == int(role.id)
    assert result["open_count"] == 1
    assert [item["needs_input_id"] for item in result["requests"]] == [int(open_row.id)]
    assert result["requests"][0]["input_mode"] == "string"
    assert result["requests"][0]["can_dismiss"] is True


def test_answer_option_accepts_label_and_stores_canonical_option(db):
    _, user, role, _ = _world(db)
    row = _request(
        db,
        role,
        kind="candidate_tie_break",
        options=[
            {"value": "candidate_17", "label": "Marcus Lee"},
            {"value": "candidate_22", "label": "Aisha Khan"},
        ],
    )

    result = recruiter_inputs.answer_recruiter_input(
        db,
        role=role,
        user=user,
        needs_input_id=int(row.id),
        value="marcus lee",
    )

    assert result["status"] == "answered"
    assert row.response == {"value": "candidate_17", "label": "Marcus Lee"}
    assert row.resolved_by_user_id == int(user.id)


def test_answer_option_rejects_value_outside_live_options(db):
    _, user, role, _ = _world(db)
    row = _request(
        db,
        role,
        kind="candidate_tie_break",
        options=[{"value": "a", "label": "Candidate A"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        recruiter_inputs.answer_recruiter_input(
            db,
            role=role,
            user=user,
            needs_input_id=int(row.id),
            value="Candidate B",
        )

    assert exc_info.value.status_code == 422
    assert row.resolved_at is None


def test_free_text_answer_validates_nested_value_schema(db):
    _, user, role, _ = _world(db)
    row = _request(
        db,
        role,
        response_schema={
            "type": "object",
            "properties": {
                "value": {"type": "string", "minLength": 5, "maxLength": 20}
            },
            "required": ["value"],
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        recruiter_inputs.answer_recruiter_input(
            db,
            role=role,
            user=user,
            needs_input_id=int(row.id),
            value=" no ",
        )
    assert exc_info.value.status_code == 422
    assert row.resolved_at is None

    result = recruiter_inputs.answer_recruiter_input(
        db,
        role=role,
        user=user,
        needs_input_id=int(row.id),
        value="  Hire remotely  ",
    )
    assert result["response"] == {"value": "Hire remotely"}


def test_threshold_accepts_numeric_override_and_applies_canonical_writeback(db):
    _, user, role, _ = _world(db)
    row = _request(
        db,
        role,
        kind="threshold_ambiguous",
        prompt="Use 70 or send another number",
        options=[{"value": "70", "label": "Use 70"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        recruiter_inputs.answer_recruiter_input(
            db,
            role=role,
            user=user,
            needs_input_id=int(row.id),
            value=101,
        )
    assert exc_info.value.status_code == 422
    assert role.score_threshold is None

    recruiter_inputs.answer_recruiter_input(
        db,
        role=role,
        user=user,
        needs_input_id=int(row.id),
        value="68",
    )
    assert row.response == {"value": 68}
    assert role.score_threshold == 68


def test_answer_cannot_cross_the_conversation_role_boundary(db):
    _, user, role, other_role = _world(db)
    other_request = _request(db, other_role)

    with pytest.raises(HTTPException) as exc_info:
        recruiter_inputs.answer_recruiter_input(
            db,
            role=role,
            user=user,
            needs_input_id=int(other_request.id),
            value="answer",
        )

    assert exc_info.value.status_code == 404
    assert other_request.resolved_at is None


def test_dismiss_honors_schema_permission_and_defaults_to_existing_behavior(db):
    _, user, role, _ = _world(db)
    required = _request(db, role, response_schema={"allow_dismiss": False})
    optional = _request(db, role)

    with pytest.raises(HTTPException) as exc_info:
        recruiter_inputs.dismiss_recruiter_input(
            db,
            role=role,
            user=user,
            needs_input_id=int(required.id),
        )
    assert exc_info.value.status_code == 403
    assert required.dismissed_at is None

    result = recruiter_inputs.dismiss_recruiter_input(
        db,
        role=role,
        user=user,
        needs_input_id=int(optional.id),
    )
    assert result["status"] == "dismissed"
    assert optional.dismissed_at is not None


@pytest.mark.parametrize(
    "kind",
    ["missing_job_spec", "missing_cv", "cv_unreadable", "task_assignment_missing"],
)
def test_external_data_question_cannot_be_falsely_answered(db, kind):
    _, user, role, _ = _world(db)
    row = _request(
        db,
        role,
        kind=kind,
        response_schema={"link_url": f"/jobs/{int(role.id)}"},
    )

    listed = recruiter_inputs.list_open_recruiter_inputs(db, role=role)
    assert listed["requests"][0]["input_mode"] == "external"
    assert listed["requests"][0]["can_answer"] is False

    with pytest.raises(HTTPException) as exc_info:
        recruiter_inputs.answer_recruiter_input(
            db,
            role=role,
            user=user,
            needs_input_id=int(row.id),
            value="done",
        )
    assert exc_info.value.status_code == 422
    assert row.resolved_at is None
