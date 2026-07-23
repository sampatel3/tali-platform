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
from app.models.sister_role_evaluation import SisterRoleEvaluation


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
            role_id=role.id,
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


def test_related_role_activity_uses_event_logical_role_not_transport_role(client):
    """A live related-role action remains visible and labelled in its role.

    The application is deliberately persisted under the original role to model
    an optional shared ATS transport. Event.role_id identifies the product role,
    while its explicit evaluation row remains the current membership authority.
    """
    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_activity(org_name="Related Activity Org")
    _attach_user_to_org(email, seeded["org_id"])

    sess = TestingSessionLocal()
    try:
        related = Role(
            organization_id=seeded["org_id"],
            name="Independent Related Role",
            source="manual",
            role_kind="sister",
            ats_owner_role_id=seeded["role_id"],
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=0,
        )
        sess.add(related)
        sess.flush()
        source_application = sess.get(
            CandidateApplication,
            seeded["application_id"],
        )
        assert source_application is not None
        sess.add(
            SisterRoleEvaluation(
                organization_id=seeded["org_id"],
                role_id=int(related.id),
                candidate_id=int(source_application.candidate_id),
                source_application_id=int(source_application.id),
                ats_application_id=int(source_application.id),
                status="done",
                pipeline_stage="review",
                application_outcome="open",
                membership_source="test",
                spec_fingerprint="related-activity-live-membership",
            )
        )
        sess.flush()
        related_event = CandidateApplicationEvent(
            application_id=seeded["application_id"],
            organization_id=seeded["org_id"],
            role_id=related.id,
            event_type="stage_change",
            from_stage="review",
            to_stage="technical_interview",
            actor_type="agent",
            reason="related-role advance",
            created_at=datetime.now(timezone.utc),
        )
        sess.add(related_event)
        sess.commit()
        related_id = int(related.id)
        event_id = int(related_event.id)
    finally:
        sess.close()

    related_feed = client.get(
        f"/api/v1/roles/{related_id}/agent/activity?limit=50",
        headers=headers,
    )
    assert related_feed.status_code == 200, related_feed.text
    assert [entry["id"] for entry in related_feed.json()["entries"]] == [event_id]

    owner_feed = client.get(
        f"/api/v1/roles/{seeded['role_id']}/agent/activity?limit=50",
        headers=headers,
    )
    assert owner_feed.status_code == 200, owner_feed.text
    assert event_id not in {
        entry["id"]
        for entry in owner_feed.json()["entries"]
        if entry["kind"] == "event"
    }

    org_feed = client.get("/api/v1/agent/activity?limit=50", headers=headers)
    assert org_feed.status_code == 200, org_feed.text
    entry = next(
        item
        for item in org_feed.json()["entries"]
        if item["kind"] == "event" and item["id"] == event_id
    )
    assert entry["role_id"] == related_id
    assert entry["role_name"] == "Independent Related Role"

    org_status = client.get("/api/v1/agent/org-status", headers=headers)
    assert org_status.status_code == 200, org_status.text
    assert "Independent Related Role" in org_status.json()["last_activity"]["summary"]


def test_related_role_agent_status_resolves_linked_legacy_event_without_owner_leak(
    client,
):
    """Status uses event authority and returns the logical application id."""

    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_activity(org_name="Related Status Activity Org")
    _attach_user_to_org(email, seeded["org_id"])

    sess = TestingSessionLocal()
    try:
        owner = sess.get(Role, seeded["role_id"])
        assert owner is not None
        related = Role(
            organization_id=seeded["org_id"],
            name="Related Status Role",
            source="sister",
            role_kind="sister",
            ats_owner_role_id=owner.id,
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=0,
        )
        member = Candidate(
            organization_id=seeded["org_id"],
            email="status-member@x.test",
            full_name="Status Member",
        )
        owner_only = Candidate(
            organization_id=seeded["org_id"],
            email="status-owner-only@x.test",
            full_name="Owner Only Distractor",
        )
        sess.add_all([related, member, owner_only])
        sess.flush()
        local_application = CandidateApplication(
            organization_id=seeded["org_id"],
            candidate_id=member.id,
            role_id=related.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
        )
        ats_transport = CandidateApplication(
            organization_id=seeded["org_id"],
            candidate_id=member.id,
            role_id=owner.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="workable",
            workable_candidate_id="wk-status-member",
        )
        owner_only_application = CandidateApplication(
            organization_id=seeded["org_id"],
            candidate_id=owner_only.id,
            role_id=owner.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="workable",
            workable_candidate_id="wk-status-owner-only",
        )
        sess.add_all(
            [local_application, ats_transport, owner_only_application]
        )
        sess.flush()
        membership = SisterRoleEvaluation(
            organization_id=seeded["org_id"],
            role_id=related.id,
            candidate_id=member.id,
            source_application_id=local_application.id,
            ats_application_id=ats_transport.id,
            status="done",
            pipeline_stage="review",
            application_outcome="open",
            membership_source="direct_application",
            spec_fingerprint="status-event-oracle",
        )
        sess.add(membership)
        sess.flush()
        event_time = datetime.now(timezone.utc) + timedelta(minutes=1)
        legacy_related_event = CandidateApplicationEvent(
            application_id=ats_transport.id,
            organization_id=seeded["org_id"],
            role_id=None,
            event_type="pipeline_stage_changed",
            actor_type="agent",
            reason="legacy related-role action",
            event_metadata={"acting_role_id": related.id},
            created_at=event_time,
        )
        owner_only_event = CandidateApplicationEvent(
            application_id=owner_only_application.id,
            organization_id=seeded["org_id"],
            role_id=owner.id,
            event_type="pipeline_stage_changed",
            actor_type="agent",
            reason="newer owner-only action",
            created_at=event_time + timedelta(minutes=1),
        )
        sess.add_all([legacy_related_event, owner_only_event])
        sess.commit()
        related_id = int(related.id)
        logical_application_id = int(local_application.id)
    finally:
        sess.close()

    response = client.get(
        f"/api/v1/roles/{related_id}/agent/status",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    activity = response.json()["last_activity"]
    assert activity["reason"] == "legacy related-role action"
    assert activity["application_id"] == logical_application_id
    assert activity["candidate_name"] == "Status Member"


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
