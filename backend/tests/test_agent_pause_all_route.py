"""HTTP-layer tests for the org-wide soft pause/resume endpoints.

  POST /api/v1/agent/pause-all     soft-pause every agent-enabled role
  POST /api/v1/agent/resume-all    resume every paused role back under cap

The defining contract of the *soft* pause (vs the per-role toggle-off) is
that pending decisions survive — pausing must not empty the review queue.
"""
from __future__ import annotations

from datetime import datetime, timezone

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
        org = Organization(name=org_name, slug=f"pa-{id(sess)}")
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


def test_resume_all_clears_pause_for_under_budget_roles(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_org_with_agent_roles("Resume Org", role_names=["A", "B"])
    _attach_user_to_org(email, seeded["org_id"])

    client.post("/api/v1/agent/pause-all", headers=headers)
    resp = client.post("/api/v1/agent/resume-all", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["affected"] == 2
    assert body["skipped"] == 0

    for role_id in seeded["role_ids"]:
        paused_at, reason, _enabled = _role_pause_state(role_id)
        assert paused_at is None
        assert reason is None


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
