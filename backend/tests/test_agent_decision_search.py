"""GET /agent-decisions?q=... searches the recruiter's decision queue."""

from __future__ import annotations

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _seed_decision(
    db,
    *,
    org_id: int,
    role_id: int,
    full_name: str,
    email: str,
    reasoning: str,
    key: str,
) -> AgentDecision:
    candidate = Candidate(
        organization_id=org_id,
        full_name=full_name,
        email=email,
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate.id,
        role_id=role_id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    decision = AgentDecision(
        organization_id=org_id,
        role_id=role_id,
        application_id=application.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="pending",
        reasoning=reasoning,
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        idempotency_key=key,
    )
    db.add(decision)
    db.flush()
    return decision


def test_decision_search_matches_candidate_name_email_and_reasoning(client, db):
    headers, user_email = auth_headers(client)
    org_id = db.query(User).filter(User.email == user_email).one().organization_id
    role = Role(organization_id=org_id, name="Search role", source="manual")
    db.add(role)
    db.flush()

    by_name = _seed_decision(
        db,
        org_id=org_id,
        role_id=role.id,
        full_name="Nadia Al Mansoori",
        email="nadia@example.test",
        reasoning="Strong general profile.",
        key="decision-search:name",
    )
    by_email = _seed_decision(
        db,
        org_id=org_id,
        role_id=role.id,
        full_name="Omar Khan",
        email="omar.special@example.test",
        reasoning="Strong general profile.",
        key="decision-search:email",
    )
    by_reasoning = _seed_decision(
        db,
        org_id=org_id,
        role_id=role.id,
        full_name="Priya Shah",
        email="priya@example.test",
        reasoning="Demonstrated distributed-systems leadership.",
        key="decision-search:reasoning",
    )
    db.commit()

    for query, expected_id in (
        ("mansoori", by_name.id),
        ("omar.special", by_email.id),
        ("distributed-systems", by_reasoning.id),
    ):
        response = client.get(
            "/api/v1/agent-decisions",
            params={"status": "pending", "q": query},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [expected_id]


def test_decision_search_never_crosses_organization_boundary(client, db):
    headers, user_email = auth_headers(client)
    own_org_id = db.query(User).filter(User.email == user_email).one().organization_id
    own_role = Role(organization_id=own_org_id, name="Own role", source="manual")
    other_org = Organization(name="Other organization", slug="decision-search-other")
    db.add_all([own_role, other_org])
    db.flush()
    other_role = Role(
        organization_id=other_org.id,
        name="Other role",
        source="manual",
    )
    db.add(other_role)
    db.flush()

    own_decision = _seed_decision(
        db,
        org_id=own_org_id,
        role_id=own_role.id,
        full_name="Own Candidate",
        email="own@example.test",
        reasoning="Shared confidential needle.",
        key="decision-search:own-org",
    )
    other_decision = _seed_decision(
        db,
        org_id=other_org.id,
        role_id=other_role.id,
        full_name="Other Candidate",
        email="other@example.test",
        reasoning="Shared confidential needle.",
        key="decision-search:other-org",
    )
    db.commit()

    response = client.get(
        "/api/v1/agent-decisions",
        params={"status": "pending", "q": "confidential needle"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [own_decision.id]
    assert other_decision.id not in {row["id"] for row in response.json()}
