"""HTTP-layer smoke test for GET /api/v1/roles/{id}/agent/activity.

Seeds an org/role with one of each activity source (run, decision, event,
needs_input) and asserts the route returns a merged, reverse-chronological
feed scoped to the current org.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role


# Same BigInteger autoincrement workaround the teach-decision tests use —
# SQLite doesn't autoincrement BIGINT PKs by default.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_runs": 0, "agent_decisions": 0, "agent_needs_input": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentRun, "before_insert", _assign_big_pk)
event.listen(AgentDecision, "before_insert", _assign_big_pk)
event.listen(AgentNeedsInput, "before_insert", _assign_big_pk)


def _seed_activity(org_name: str = "Activity Org") -> dict:
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        org = Organization(name=org_name, slug=f"act-{id(sess)}")
        sess.add(org)
        sess.flush()
        role = Role(
            organization_id=org.id,
            name="Activity Role",
            source="manual",
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=0,
        )
        sess.add(role)
        sess.flush()
        cand = Candidate(organization_id=org.id, email="a@x.test", full_name="Ada Lovelace")
        sess.add(cand)
        sess.flush()
        app_row = CandidateApplication(
            organization_id=org.id,
            candidate_id=cand.id,
            role_id=role.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
        )
        sess.add(app_row)
        sess.flush()

        now = datetime.now(timezone.utc)
        # Oldest → newest so the merged feed has a clear ordering to assert.
        run = AgentRun(
            organization_id=org.id,
            role_id=role.id,
            trigger="cron",
            status="succeeded",
            started_at=now - timedelta(minutes=30),
            finished_at=now - timedelta(minutes=29),
            decisions_emitted=2,
            total_cost_micro_usd=12345,
        )
        sess.add(run)
        decision = AgentDecision(
            organization_id=org.id,
            role_id=role.id,
            application_id=app_row.id,
            decision_type="advance_to_interview",
            recommendation="advance_to_interview",
            status="pending",
            reasoning="Strong CV match across all six dimensions.",
            confidence=0.91,
            model_version="m",
            prompt_version="p",
            idempotency_key=f"act:{app_row.id}:advance",
            created_at=now - timedelta(minutes=20),
        )
        sess.add(decision)
        ev = CandidateApplicationEvent(
            application_id=app_row.id,
            organization_id=org.id,
            event_type="stage_change",
            from_stage="applied",
            to_stage="review",
            actor_type="agent",
            reason="auto-advanced",
            created_at=now - timedelta(minutes=10),
        )
        sess.add(ev)
        need = AgentNeedsInput(
            organization_id=org.id,
            role_id=role.id,
            kind="monthly_budget_missing",
            prompt="Set a monthly budget so the agent can pace itself.",
            created_at=now - timedelta(minutes=5),
        )
        sess.add(need)
        sess.commit()
        return {
            "org_id": int(org.id),
            "role_id": int(role.id),
            "application_id": int(app_row.id),
        }
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


def test_agent_activity_route_returns_merged_feed(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_activity()
    _attach_user_to_org(email, seeded["org_id"])

    resp = client.get(
        f"/api/v1/roles/{seeded['role_id']}/agent/activity?limit=50",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role_id"] == seeded["role_id"]
    entries = body["entries"]
    assert len(entries) == 4
    kinds = [e["kind"] for e in entries]
    # Newest first: needs_input (5m) > event (10m) > decision (20m) > run (30m).
    assert kinds == ["needs_input", "event", "decision", "run"]

    decision_entry = next(e for e in entries if e["kind"] == "decision")
    assert decision_entry["candidate_name"] == "Ada Lovelace"
    assert decision_entry["decision_type"] == "advance_to_interview"
    assert decision_entry["confidence"] is not None

    run_entry = next(e for e in entries if e["kind"] == "run")
    assert "decision" in run_entry["title"]
    assert run_entry["cost_micro_usd"] == 12345


def test_agent_activity_route_404s_for_other_org(client):
    """Role belongs to another org → 404, no cross-org leakage."""
    from tests.conftest import auth_headers

    headers, _ = auth_headers(client)
    seeded = _seed_activity(org_name="Other Org")
    # Do NOT attach the test user to the seeded org — should 404.
    resp = client.get(
        f"/api/v1/roles/{seeded['role_id']}/agent/activity",
        headers=headers,
    )
    assert resp.status_code == 404
