"""Durable, scoped HTTP contracts for recruiter-triggered agent runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.models.agent_run import AGENT_RUN_DISPATCHING, AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _user_and_org(db, email: str) -> tuple[User, int]:
    user = db.query(User).filter(User.email == email).one()
    return user, int(user.organization_id)


def _role(db, organization_id: int, name: str) -> Role:
    role = Role(
        organization_id=organization_id,
        name=name,
        source="manual",
        agentic_mode_enabled=True,
        job_spec_text="Hire a reliable platform engineer with production ownership.",
    )
    db.add(role)
    db.flush()
    return role


def _application(
    db,
    *,
    organization_id: int,
    role: Role,
    suffix: str,
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=organization_id,
        full_name=f"Candidate {suffix}",
        email=f"run-now-{suffix}@example.test",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        source="manual",
        application_outcome="open",
        pipeline_stage="review",
    )
    db.add(application)
    db.flush()
    return application


def test_run_now_broker_failure_persists_one_recoverable_intent(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    role = _role(db, organization_id, "Broker recovery")
    db.commit()
    request_headers = {**headers, "Idempotency-Key": "run-now-broker-recovery"}

    with patch(
        "app.tasks.agent_tasks.agent_manual_run.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        response = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={},
            headers=request_headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "dispatch_pending"
    assert body["queued"] is False
    assert body["broker_accepted"] is False
    assert body["dispatch_pending"] is True
    assert body["intent_persisted"] is True
    assert body["replayed"] is False
    assert body["task_id"] is None
    assert body["idempotency_key"] == "run-now-broker-recovery"

    db.expire_all()
    intent = db.get(AgentRun, int(body["agent_run_id"]))
    assert intent is not None
    assert intent.status == AGENT_RUN_DISPATCHING
    assert int(intent.organization_id) == organization_id
    assert int(intent.role_id) == int(role.id)

    # An immediate HTTP retry replays the intent but cannot know whether the
    # earlier ambiguous publish reached the broker, so it must not say queued.
    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        replay = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={},
            headers=request_headers,
        )
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["agent_run_id"] == body["agent_run_id"]
    assert replay_body["queued"] is False
    assert replay_body["broker_accepted"] is None
    assert replay_body["dispatch_pending"] is True
    assert replay_body["replayed"] is True
    delay.assert_not_called()

    snapshot = dict(intent.agent_state_snapshot or {})
    snapshot["dispatch_next_attempt_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    intent.agent_state_snapshot = snapshot
    db.commit()

    from app.tasks.agent_tasks import recover_dispatching_manual_agent_runs

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        recovered = recover_dispatching_manual_agent_runs.run(limit=10)
    assert recovered == {"scanned": 1, "kicked": 1, "publish_failed": 0}
    delay.assert_called_once_with(
        role_id=int(role.id),
        application_id=None,
        dispatch_key=str(intent.dispatch_key),
    )

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        bounded = recover_dispatching_manual_agent_runs.run(limit=10)
    assert bounded["kicked"] == 0
    delay.assert_not_called()


def test_run_now_idempotency_key_is_bound_to_role_and_application_scope(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    first_role = _role(db, organization_id, "First scope")
    second_role = _role(db, organization_id, "Second scope")
    first_app = _application(
        db,
        organization_id=organization_id,
        role=first_role,
        suffix="first",
    )
    second_app = _application(
        db,
        organization_id=organization_id,
        role=second_role,
        suffix="second",
    )
    db.commit()
    request_headers = {**headers, "Idempotency-Key": "fixed-run-scope"}

    with patch(
        "app.tasks.agent_tasks.agent_manual_run.delay",
        return_value=SimpleNamespace(id="run-now-task"),
    ) as delay:
        accepted = client.post(
            f"/api/v1/roles/{first_role.id}/agent/run-now",
            json={"application_id": first_app.id},
            headers=request_headers,
        )
    assert accepted.status_code == 200, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["queued"] is True
    assert accepted_body["broker_accepted"] is True
    assert accepted_body["dispatch_pending"] is False
    assert accepted_body["replayed"] is False
    assert accepted_body["application_id"] == int(first_app.id)
    delay.assert_called_once()

    # Reusing the key for another role is a scope conflict, not a second run.
    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        conflict = client.post(
            f"/api/v1/roles/{second_role.id}/agent/run-now",
            json={"application_id": second_app.id},
            headers=request_headers,
        )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "MANUAL_RUN_IDEMPOTENCY_CONFLICT"
    delay.assert_not_called()
    assert db.query(AgentRun).count() == 1

    # An application from another role is rejected before any intent/publish.
    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        wrong_application = client.post(
            f"/api/v1/roles/{first_role.id}/agent/run-now",
            json={
                "application_id": second_app.id,
                "idempotency_key": "wrong-application-scope",
            },
            headers=headers,
        )
    assert wrong_application.status_code == 404, wrong_application.text
    delay.assert_not_called()
    assert db.query(AgentRun).count() == 1


def test_run_now_legacy_request_id_is_a_stable_fallback(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    role = _role(db, organization_id, "Legacy request id")
    db.commit()
    request_headers = {**headers, "X-Request-ID": "legacy-run-now-retry"}

    with patch(
        "app.tasks.agent_tasks.agent_manual_run.delay",
        return_value=SimpleNamespace(id="legacy-task"),
    ) as delay:
        first = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={},
            headers=request_headers,
        )
        replay = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={},
            headers=request_headers,
        )

    assert first.status_code == 200, first.text
    assert first.json()["queued"] is True
    assert replay.status_code == 200, replay.text
    assert replay.json()["queued"] is False
    assert replay.json()["replayed"] is True
    assert replay.json()["agent_run_id"] == first.json()["agent_run_id"]
    assert delay.call_count == 1
    assert db.query(AgentRun).count() == 1


def test_run_now_authorization_fails_before_intent_or_publish(client, db):
    headers, email = auth_headers(client)
    user, organization_id = _user_and_org(db, email)
    role = _role(db, organization_id, "Unauthorized run")
    user.role = "member"
    db.commit()

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        response = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={"idempotency_key": "unauthorized-run"},
            headers=headers,
        )

    assert response.status_code == 403, response.text
    delay.assert_not_called()
    assert db.query(AgentRun).count() == 0


def test_run_now_rejects_conflicting_body_and_header_keys(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    role = _role(db, organization_id, "Conflicting keys")
    db.commit()

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        response = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={"idempotency_key": "body-key"},
            headers={**headers, "Idempotency-Key": "header-key"},
        )

    assert response.status_code == 422, response.text
    delay.assert_not_called()
    assert db.query(AgentRun).count() == 0
