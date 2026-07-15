"""HTTP recruiter-input policy and immediate autonomous follow-up."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models.agent_needs_input import AgentNeedsInput
from app.models.role import Role
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


def _question(db, role, *, schema=None) -> AgentNeedsInput:
    row = AgentNeedsInput(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        kind="other",
        prompt="Which direction should I take?",
        response_schema=schema,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


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
            json={"response": {"value": "Prioritize platform reliability"}},
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "resolved"
    delay.assert_called_once_with(int(role.id))


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
            json={"response": {"value": "Keep the current bar"}},
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
            json={"response": {"value": "Noted"}},
        )

    assert response.status_code == 200, response.text
    delay.assert_not_called()
