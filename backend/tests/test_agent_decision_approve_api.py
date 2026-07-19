"""Public acceptance contract for one recruiter approval."""

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def test_approve_returns_only_a_durable_acceptance_receipt(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    role = Role(
        organization_id=int(user.organization_id),
        name="Backend",
        source="manual",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=int(user.organization_id),
        email="approval-receipt@example.test",
        full_name="Approval Receipt",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(user.organization_id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    decision = AgentDecision(
        organization_id=int(user.organization_id),
        role_id=int(role.id),
        application_id=int(application.id),
        decision_type="skip_assessment_reject",
        recommendation="skip_assessment_reject",
        status="pending",
        reasoning="Does not meet the minimum requirements",
        confidence=0.9,
        model_version="test-model",
        prompt_version="test-prompt",
        idempotency_key=f"approve-receipt:{int(application.id)}",
    )
    db.add(decision)
    db.commit()

    response = client.post(
        f"/api/v1/agent-decisions/{int(decision.id)}/approve?force=true",
        headers=headers,
        json={"note": "Reviewed"},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {
        "decision_id": int(decision.id),
        "accepted": True,
    }
