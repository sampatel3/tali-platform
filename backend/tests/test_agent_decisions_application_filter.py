"""GET /agent-decisions?application_id=X — the single-candidate lens.

The candidate standing report fetches just this application's pending
decision(s) to surface the agent's recommendation in its header strip,
so the filter must return ONLY that application's decisions (and never
another application's, even within the same org / role).
"""
from __future__ import annotations

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.user import User
from tests.conftest import auth_headers


def _app(db, org_id, role_id, email):
    cand = Candidate(organization_id=org_id, email=email, full_name=email.split("@")[0])
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id,
        candidate_id=cand.id,
        role_id=role_id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    return app


def _decision(db, org_id, role_id, app_id, *, status="pending"):
    d = AgentDecision(
        organization_id=org_id,
        role_id=role_id,
        application_id=app_id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status=status,
        reasoning="seed",
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"appfilter-test:{app_id}:{status}",
    )
    db.add(d)
    db.flush()
    return d


def test_application_id_filters_to_one_candidate(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()

    # Two applications on the same role, each with its own pending decision.
    app_a = _app(db, org_id, role.id, "a@x.test")
    app_b = _app(db, org_id, role.id, "b@x.test")
    decision_a = _decision(db, org_id, role.id, app_a.id)
    decision_b = _decision(db, org_id, role.id, app_b.id)
    db.commit()

    # Filtering by app_a returns ONLY app_a's decision.
    res = client.get(
        f"/api/v1/agent-decisions?application_id={app_a.id}", headers=headers
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    assert {row["id"] for row in rows} == {decision_a.id}
    assert all(row["application_id"] == app_a.id for row in rows)
    assert decision_b.id not in {row["id"] for row in rows}

    # And by app_b returns ONLY app_b's decision — proving the filter scopes
    # per-application, not just "any decision on the role".
    res_b = client.get(
        f"/api/v1/agent-decisions?application_id={app_b.id}", headers=headers
    )
    assert res_b.status_code == 200, res_b.text
    assert {row["id"] for row in res_b.json()} == {decision_b.id}

    # Sanity: without the filter, both decisions are visible in the queue.
    res_all = client.get("/api/v1/agent-decisions?status=pending", headers=headers)
    assert res_all.status_code == 200, res_all.text
    all_ids = {row["id"] for row in res_all.json()}
    assert {decision_a.id, decision_b.id} <= all_ids


def test_application_id_with_no_decisions_returns_empty(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()
    app = _app(db, org_id, role.id, "nodecision@x.test")
    db.commit()

    res = client.get(
        f"/api/v1/agent-decisions?application_id={app.id}", headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


def test_decision_search_uses_the_candidate_full_name_column(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(
        organization_id=org_id,
        name="Searchable role",
        source="manual",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()
    application = _app(db, org_id, role.id, "unique-search-name@x.test")
    application.candidate.full_name = "Unique Candidate Fullname"
    decision = _decision(db, org_id, role.id, application.id)
    db.commit()

    response = client.get(
        "/api/v1/agent-decisions",
        params={"q": "Candidate Fullname", "status": "pending"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [decision.id]


def test_related_role_decision_includes_complete_named_role_family(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    owner = Role(
        organization_id=org_id,
        name="AI Engineer",
        source="workable",
        workable_job_id="AI-ENGINEER",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=org_id,
        name="AI Engineer · Evaluation",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    app = _app(db, org_id, owner.id, "related-decision@x.test")
    decision = _decision(db, org_id, related.id, app.id)
    db.commit()

    response = client.get(
        f"/api/v1/agent-decisions?application_id={app.id}", headers=headers
    )

    assert response.status_code == 200, response.text
    payload = next(row for row in response.json() if row["id"] == decision.id)
    assert payload["role_name"] == related.name
    assert payload["role_family"] == {
        "owner": {"id": owner.id, "name": owner.name},
        "related": [{"id": related.id, "name": related.name}],
    }
    assert payload["workable_job_id"] is None


def test_related_role_workable_decision_uses_the_linked_owner_job(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    owner = Role(
        organization_id=org_id,
        name="Workable owner",
        source="workable",
        workable_job_id="OWNER-JOB",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=org_id,
        name="Related evaluation",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    app = _app(db, org_id, owner.id, "related-workable@x.test")
    app.source = "workable"
    app.workable_candidate_id = "candidate-123"
    decision = _decision(db, org_id, related.id, app.id)
    db.commit()

    response = client.get(
        f"/api/v1/agent-decisions?application_id={app.id}", headers=headers
    )

    assert response.status_code == 200, response.text
    payload = next(row for row in response.json() if row["id"] == decision.id)
    assert payload["workable_job_id"] == owner.workable_job_id
