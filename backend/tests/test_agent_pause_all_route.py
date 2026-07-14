"""HTTP-layer tests for the agent pause / resume / turn-off contract.

  POST /api/v1/agent/pause-all          soft-pause every agent-enabled role
  POST /api/v1/agent/resume-all         resume every paused role back under cap
  POST /api/v1/roles/{id}/agent/pause   soft-pause one role
  POST /api/v1/roles/{id}/agent/resume  resume one paused role

The defining contract: a pending decision's lifecycle is tied to the
candidate, not the agent's power state. Neither a soft pause NOR turning the
agent fully off (PATCH agentic_mode_enabled=false) empties the review queue —
discarding it is an explicit opt-in via POST /agent-decisions/discard.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


# SQLite doesn't autoincrement BIGINT PKs — mirror the workaround the other
# agent-decision route tests use.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_decisions": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentDecision, "before_insert", _assign_big_pk)


def _seed_org_with_agent_roles(org_name: str, *, role_names: list[str]) -> dict:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        org = Organization(name=org_name, slug=f"pa-{uuid.uuid4().hex[:12]}")
        sess.add(org)
        sess.flush()
        role_ids = []
        for name in role_names:
            role = Role(
                organization_id=org.id,
                name=name,
                source="manual",
                agentic_mode_enabled=True,
                # Non-zero cap with zero spend → genuinely under budget, so
                # resume-all can clear the pause.
                monthly_usd_budget_cents=5000,
            )
            sess.add(role)
            sess.flush()
            role_ids.append(int(role.id))
        sess.commit()
        return {"org_id": int(org.id), "role_ids": role_ids}
    finally:
        sess.close()


def _seed_pending_decision(org_id: int, role_id: int) -> int:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        cand = Candidate(organization_id=org_id, email=f"c{role_id}@x.test", full_name="Ada Lovelace")
        sess.add(cand)
        sess.flush()
        app_row = CandidateApplication(
            organization_id=org_id,
            candidate_id=cand.id,
            role_id=role_id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
        )
        sess.add(app_row)
        sess.flush()
        decision = AgentDecision(
            organization_id=org_id,
            role_id=role_id,
            application_id=app_row.id,
            decision_type="advance_to_interview",
            recommendation="advance_to_interview",
            status="pending",
            reasoning="Strong match.",
            confidence=0.9,
            model_version="m",
            prompt_version="p",
            idempotency_key=f"pa:{role_id}:advance",
            created_at=datetime.now(timezone.utc),
        )
        sess.add(decision)
        sess.commit()
        return int(decision.id)
    finally:
        sess.close()


def _attach_user_to_org(email: str, organization_id: int) -> None:
    from app.models.user import User as _U
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        user = sess.query(_U).filter(_U.email == email).first()
        assert user is not None
        user.organization_id = organization_id
        sess.commit()
    finally:
        sess.close()


def _role_pause_state(role_id: int) -> tuple:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        role = sess.query(Role).filter(Role.id == role_id).first()
        return (role.agent_paused_at, role.agent_paused_reason, role.agentic_mode_enabled)
    finally:
        sess.close()


def _decision_status(decision_id: int) -> str:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        return sess.query(AgentDecision).filter(AgentDecision.id == decision_id).first().status
    finally:
        sess.close()


def test_pause_all_soft_pauses_and_keeps_pending_decisions(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Pause Org", role_names=["A", "B"])
    _attach_user_to_org(email, seeded["org_id"])
    decision_id = _seed_pending_decision(seeded["org_id"], seeded["role_ids"][0])

    resp = client.post("/api/v1/agent/pause-all", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["affected"] == 2
    assert body["enabled_count"] == 2

    for role_id in seeded["role_ids"]:
        paused_at, reason, enabled = _role_pause_state(role_id)
        assert paused_at is not None
        assert reason == "paused by recruiter"
        # Still ENABLED — that's what keeps the queue alive.
        assert enabled is True

    # The defining contract: the pending decision is untouched.
    assert _decision_status(decision_id) == "pending"


def test_pause_all_is_idempotent_for_already_paused_roles(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Idempotent Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])

    first = client.post("/api/v1/agent/pause-all", headers=headers)
    assert first.json()["affected"] == 1
    second = client.post("/api/v1/agent/pause-all", headers=headers)
    # Already paused → nothing flips on the second call.
    assert second.json()["affected"] == 0
    assert second.json()["enabled_count"] == 1


def test_resume_all_clears_pause_and_wakes_under_budget_roles(client, monkeypatch):
    from tests.conftest import auth_headers

    wakeups: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda role_id, *, activation: wakeups.append((role_id, activation)),
    )

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Resume Org", role_names=["A", "B"])
    _attach_user_to_org(email, seeded["org_id"])

    client.post("/api/v1/agent/pause-all", headers=headers)
    resp = client.post("/api/v1/agent/resume-all", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["affected"] == 2
    assert body["skipped"] == 0
    assert sorted(wakeups) == sorted(
        (role_id, False) for role_id in seeded["role_ids"]
    )

    for role_id in seeded["role_ids"]:
        paused_at, reason, _enabled = _role_pause_state(role_id)
        assert paused_at is None
        assert reason is None


def test_resume_all_repauses_role_when_worker_rejects_wakeup(client, monkeypatch):
    from tests.conftest import auth_headers

    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles(
        "Resume Dispatch Failure Org", role_names=["A"]
    )
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    client.post("/api/v1/agent/pause-all", headers=headers)

    resp = client.post("/api/v1/agent/resume-all", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["affected"] == 0
    assert resp.json()["skipped"] == 1
    paused_at, reason, enabled = _role_pause_state(role_id)
    assert paused_at is not None
    assert reason == "agent bootstrap dispatch failed"
    assert enabled is True

    status = client.get(f"/api/v1/roles/{role_id}/agent/status", headers=headers)
    assert status.status_code == 200, status.text
    assert status.json()["bootstrap_status"] == "failed"
    assert status.json()["bootstrap_error"] == "agent bootstrap dispatch failed"


def test_resume_all_leaves_roles_paused_when_production_runtime_is_unready(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Resume Unready Org", role_names=["A", "B"])
    _attach_user_to_org(email, seeded["org_id"])
    client.post("/api/v1/agent/pause-all", headers=headers)

    with (
        patch("app.platform.startup_validation.is_production_like", return_value=True),
        patch(
            "app.services.agent_worker_health.worker_beat_status",
            return_value={"ready": False, "reason": "heartbeat_stale"},
        ),
    ):
        resp = client.post("/api/v1/agent/resume-all", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["affected"] == 0
    assert resp.json()["skipped"] == 2
    for role_id in seeded["role_ids"]:
        assert _role_pause_state(role_id)[0] is not None


def test_pause_all_is_org_scoped(client):
    """Pausing one org must not touch another org's agents."""
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    mine = _seed_org_with_agent_roles("My Org", role_names=["A"])
    other = _seed_org_with_agent_roles("Other Org", role_names=["X"])
    _attach_user_to_org(email, mine["org_id"])

    client.post("/api/v1/agent/pause-all", headers=headers)

    # Mine paused, theirs untouched.
    assert _role_pause_state(mine["role_ids"][0])[0] is not None
    assert _role_pause_state(other["role_ids"][0])[0] is None


# ---------------------------------------------------------------------------
# Per-role pause / resume (the per-role twins of pause-all / resume-all) and
# the turn-off decoupling: neither pause NOR off discards the review queue.
#   POST /api/v1/roles/{id}/agent/pause
#   POST /api/v1/roles/{id}/agent/resume
# ---------------------------------------------------------------------------


def test_pause_one_role_keeps_pending_decisions(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Per-role Pause Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    decision_id = _seed_pending_decision(seeded["org_id"], role_id)

    resp = client.post(f"/api/v1/roles/{role_id}/agent/pause", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["paused"] is True
    assert body["reason"] == "paused by recruiter"

    paused_at, reason, enabled = _role_pause_state(role_id)
    assert paused_at is not None
    assert reason == "paused by recruiter"
    assert enabled is True  # still enabled — that's what keeps the queue alive
    assert _decision_status(decision_id) == "pending"


def test_pause_one_role_is_idempotent(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Per-role Idem Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]

    client.post(f"/api/v1/roles/{role_id}/agent/pause", headers=headers)
    first_paused_at = _role_pause_state(role_id)[0]
    second = client.post(f"/api/v1/roles/{role_id}/agent/pause", headers=headers)
    assert second.status_code == 200
    # The pause timestamp doesn't move on a repeat pause.
    assert _role_pause_state(role_id)[0] == first_paused_at


def test_pause_one_role_requires_enabled_agent(client):
    """Pausing a role whose agent is off is a 409 — nothing to pause."""
    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Off Role Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    sess = TestingSessionLocal()
    try:
        role = sess.query(Role).filter(Role.id == role_id).first()
        role.agentic_mode_enabled = False
        sess.commit()
    finally:
        sess.close()

    resp = client.post(f"/api/v1/roles/{role_id}/agent/pause", headers=headers)
    assert resp.status_code == 409


def test_resume_one_role_clears_pause(client, monkeypatch):
    from tests.conftest import auth_headers

    # Resume kicks an immediate cycle; stub it so this stays a fast HTTP-layer
    # check (celery runs eagerly in the suite).
    monkeypatch.setattr(
        "app.tasks.agent_tasks.agent_cohort_tick_role.delay",
        lambda *a, **k: None,
    )

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Per-role Resume Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]

    client.post(f"/api/v1/roles/{role_id}/agent/pause", headers=headers)
    assert _role_pause_state(role_id)[0] is not None

    resp = client.post(f"/api/v1/roles/{role_id}/agent/resume", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resumed"] is True
    assert body["paused"] is False

    paused_at, reason, _enabled = _role_pause_state(role_id)
    assert paused_at is None
    assert reason is None

    status = client.get(
        f"/api/v1/roles/{role_id}/agent/status", headers=headers
    )
    assert status.status_code == 200, status.text
    assert status.json()["bootstrap_status"] == "starting"
    assert status.json()["bootstrap_started_at"] is not None


def test_resume_one_role_returns_503_and_stays_paused_when_runtime_unready(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Per-role Unready Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    client.post(f"/api/v1/roles/{role_id}/agent/pause", headers=headers)

    with (
        patch("app.platform.startup_validation.is_production_like", return_value=True),
        patch(
            "app.services.agent_worker_health.worker_beat_status",
            return_value={"ready": False, "reason": "heartbeat_stale"},
        ),
    ):
        resp = client.post(f"/api/v1/roles/{role_id}/agent/resume", headers=headers)

    assert resp.status_code == 503, resp.text
    assert "heartbeat_stale" in resp.text
    assert _role_pause_state(role_id)[0] is not None


def test_turn_off_keeps_pending_decisions_by_default(client):
    """Regression guard for the pause/off decoupling: turning the agent OFF
    (PATCH agentic_mode_enabled=false) must NOT discard the role's pending
    decisions — they stay actionable. Discarding is now an explicit opt-in.
    """
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Turn-off Keep Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    decision_id = _seed_pending_decision(seeded["org_id"], role_id)

    resp = client.patch(
        f"/api/v1/roles/{role_id}",
        json={"agentic_mode_enabled": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["agentic_mode_enabled"] is False
    # The defining change: the queue survives turn-off.
    assert _decision_status(decision_id) == "pending"


def test_explicit_discard_after_turn_off_clears_queue(client):
    """The opt-in path: POST /agent-decisions/discard wipes the queue when the
    recruiter ticks 'also discard' on the Turn-off dialog.
    """
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Turn-off Discard Org", role_names=["A"])
    _attach_user_to_org(email, seeded["org_id"])
    role_id = seeded["role_ids"][0]
    decision_id = _seed_pending_decision(seeded["org_id"], role_id)

    client.patch(
        f"/api/v1/roles/{role_id}",
        json={"agentic_mode_enabled": False},
        headers=headers,
    )
    resp = client.post(
        "/api/v1/agent-decisions/discard", json={"role_id": role_id}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["discarded"] == 1
    assert _decision_status(decision_id) == "discarded"
