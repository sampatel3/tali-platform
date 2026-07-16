"""HTTP recruiter-input policy and immediate autonomous follow-up."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models.agent_needs_input import AgentNeedsInput
from app.models.role import Role
from app.models.task import Task
from app.models.user import User
from tests.conftest import auth_headers


def _world(db, client, *, enabled: bool = True, paused: bool = False):
    headers, email = auth_headers(
        client,
        organization_name=f"Needs Input Follow-up {id(db)} {enabled} {paused}",
    )
    user = db.query(User).filter(User.email == email).one()
    role = Role(
        organization_id=int(user.organization_id),
        name="Backend Engineer",
        source="manual",
        agentic_mode_enabled=enabled,
        agent_paused_at=datetime.now(timezone.utc) if paused else None,
        agent_paused_reason="paused by recruiter" if paused else None,
        monthly_usd_budget_cents=5000,
        job_spec_text="Build reliable Python services.",
    )
    db.add(role)
    db.flush()
    return headers, role


def _question(db, role, *, schema=None, kind: str = "other") -> AgentNeedsInput:
    row = AgentNeedsInput(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        kind=kind,
        prompt="Which direction should I take?",
        response_schema=schema,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_list_includes_complete_named_role_family(client, db):
    headers, owner = _world(db, client)
    owner.name = "Data Platform Lead"
    related = Role(
        organization_id=int(owner.organization_id),
        name="AI Engineer",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=int(owner.id),
    )
    sibling = Role(
        organization_id=int(owner.organization_id),
        name="ML Engineer",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=int(owner.id),
    )
    db.add_all([related, sibling])
    db.flush()
    row = _question(db, related, kind="missing_cv")

    response = client.get(
        f"/api/v1/agent-needs-input?role_id={int(related.id)}",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()[0]
    assert payload["id"] == int(row.id)
    assert payload["role_family"] == {
        "owner": {"id": int(owner.id), "name": "Data Platform Lead"},
        "related": [
            {"id": int(related.id), "name": "AI Engineer"},
            {"id": int(sibling.id), "name": "ML Engineer"},
        ],
    }


@pytest.mark.parametrize(
    "schema",
    [
        {"allow_dismiss": False},
        {"dismissible": False},
    ],
)
def test_http_dismiss_enforces_required_question_contract(client, db, schema):
    headers, role = _world(db, client)
    row = _question(db, role, schema=schema)

    with patch("app.tasks.agent_tasks.agent_daily_review_role.delay") as delay:
        response = client.post(
            f"/api/v1/agent-needs-input/{int(row.id)}/dismiss",
            headers=headers,
        )

    assert response.status_code == 403, response.text
    db.expire_all()
    stored = db.query(AgentNeedsInput).filter(AgentNeedsInput.id == row.id).one()
    assert stored.dismissed_at is None
    delay.assert_not_called()


def test_answer_enqueues_one_followup_for_enabled_unpaused_role(client, db):
    headers, role = _world(db, client)
    row = _question(db, role)

    with patch("app.tasks.agent_tasks.agent_daily_review_role.delay") as delay:
        response = client.post(
            f"/api/v1/agent-needs-input/{int(row.id)}/answer",
            headers=headers,
            json={
                "response": {"value": "Prioritize platform reliability"},
                "expected_version": int(role.version or 1),
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "resolved"
    delay.assert_called_once_with(int(role.id))


@pytest.mark.parametrize(
    "kind",
    ["missing_job_spec", "missing_cv", "cv_unreadable", "task_assignment_missing"],
)
def test_http_answer_rejects_external_setup_questions(client, db, kind):
    """The public route cannot bypass the shared external-state contract."""

    headers, role = _world(db, client)
    row = _question(db, role, kind=kind)

    with patch("app.tasks.agent_tasks.agent_daily_review_role.delay") as delay:
        response = client.post(
            f"/api/v1/agent-needs-input/{int(row.id)}/answer",
            headers=headers,
            json={
                "response": {"value": "done"},
                "expected_version": int(role.version or 1),
            },
        )

    assert response.status_code == 422, response.text
    assert "required setup" in response.json()["detail"]
    db.expire_all()
    stored = db.query(AgentNeedsInput).filter(AgentNeedsInput.id == row.id).one()
    assert stored.resolved_at is None
    assert stored.response is None
    delay.assert_not_called()


def test_linking_active_task_auto_resolves_prompt_and_reduces_open_count(client, db):
    headers, role = _world(db, client)
    row = _question(db, role, kind="task_assignment_missing")
    task = Task(
        organization_id=int(role.organization_id),
        name="Active platform exercise",
        is_active=True,
    )
    db.add(task)
    db.commit()

    before = client.get(
        f"/api/v1/agent-needs-input?role_id={int(role.id)}",
        headers=headers,
    )
    assert before.status_code == 200, before.text
    assert [item["id"] for item in before.json()] == [int(row.id)]

    linked = client.post(
        f"/api/v1/roles/{int(role.id)}/tasks",
        headers=headers,
        json={
            "task_id": int(task.id),
            "expected_version": int(role.version or 1),
        },
    )

    assert linked.status_code == 200, linked.text
    after = client.get(
        f"/api/v1/agent-needs-input?role_id={int(role.id)}",
        headers=headers,
    )
    assert after.status_code == 200, after.text
    assert after.json() == []
    db.expire_all()
    stored = db.query(AgentNeedsInput).filter(AgentNeedsInput.id == row.id).one()
    assert stored.resolved_at is not None
    assert stored.response == {"value": "auto_resolved", "auto_resolved": True}


def test_dismiss_enqueues_one_followup_for_enabled_unpaused_role(client, db):
    headers, role = _world(db, client)
    row = _question(db, role)

    with patch("app.tasks.agent_tasks.agent_daily_review_role.delay") as delay:
        response = client.post(
            f"/api/v1/agent-needs-input/{int(row.id)}/dismiss",
            headers=headers,
        )
        replay = client.post(
            f"/api/v1/agent-needs-input/{int(row.id)}/dismiss",
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "dismissed"
    assert replay.status_code == 200, replay.text
    delay.assert_called_once_with(int(role.id))


def test_followup_dispatch_failure_does_not_undo_recorded_answer(client, db):
    headers, role = _world(db, client)
    row = _question(db, role)

    with patch(
        "app.tasks.agent_tasks.agent_daily_review_role.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        response = client.post(
            f"/api/v1/agent-needs-input/{int(row.id)}/answer",
            headers=headers,
            json={
                "response": {"value": "Keep the current bar"},
                "expected_version": int(role.version or 1),
            },
        )

    assert response.status_code == 200, response.text
    db.expire_all()
    stored = db.query(AgentNeedsInput).filter(AgentNeedsInput.id == row.id).one()
    assert stored.resolved_at is not None
    assert stored.response == {"value": "Keep the current bar"}


@pytest.mark.parametrize(
    ("enabled", "paused"),
    [
        (False, False),
        (True, True),
    ],
)
def test_answer_does_not_enqueue_for_inactive_role(client, db, enabled, paused):
    headers, role = _world(db, client, enabled=enabled, paused=paused)
    row = _question(db, role)

    with patch("app.tasks.agent_tasks.agent_daily_review_role.delay") as delay:
        response = client.post(
            f"/api/v1/agent-needs-input/{int(row.id)}/answer",
            headers=headers,
            json={
                "response": {"value": "Noted"},
                "expected_version": int(role.version or 1),
            },
        )

    assert response.status_code == 200, response.text
    delay.assert_not_called()
