"""Public acceptance contract for one recruiter approval."""

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.role import Role
from app.models.user import User
from app.components.scoring.freshness import capture_score_generation
from app.services.decision_input_fingerprint import capture_input_fingerprint
from app.services.role_intent_fingerprint import role_intent_fingerprint
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
        cv_match_score=20.0,
    )
    db.add(application)
    db.flush()
    db.add(
        CvScoreJob(
            application_id=int(application.id),
            role_id=int(role.id),
            status="done",
            cache_key=f"role-intent:{role_intent_fingerprint(role, db=db)}",
        )
    )
    db.flush()
    score_generation = capture_score_generation(
        db,
        role=role,
        application_id=int(application.id),
    )
    assert score_generation is not None
    input_fingerprint, criteria_fingerprint, cv_fingerprint = (
        capture_input_fingerprint(
            db,
            application_id=int(application.id),
            role_id=int(role.id),
            score_generation=score_generation,
        )
    )
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
        input_fingerprint=input_fingerprint,
        criteria_fingerprint=criteria_fingerprint,
        cv_fingerprint=cv_fingerprint,
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
