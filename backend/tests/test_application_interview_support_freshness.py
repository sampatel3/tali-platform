from __future__ import annotations

from datetime import datetime, timezone

from app.models.application_interview import ApplicationInterview
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.models.user import User
from tests.conftest import auth_headers


def _pack(*, stage: str, question: str) -> dict:
    return {
        "stage": stage,
        "summary": f"{stage} summary",
        "source": "role_template",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "questions": [{"question": question}],
    }


def test_application_detail_rebuilds_interview_support_from_live_inputs(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    org = db.query(Organization).filter(Organization.id == user.organization_id).one()
    org.fireflies_owner_email = "owner@example.com"
    org.fireflies_invite_email = "invite@fireflies.ai"
    org.fireflies_api_key_encrypted = "configured"

    role = Role(
        organization_id=org.id,
        name="Current role",
        screening_pack_template=_pack(
            stage="screening", question="Current screening question?"
        ),
        tech_interview_pack_template=_pack(
            stage="tech_stage_2", question="Current template tech question?"
        ),
        tech_questions_cached=[
            {
                "question": "Current cached tech question?",
                "evidence_source": "job_spec",
                "source_text": "Current role spec",
            }
        ],
    )
    candidate = Candidate(
        organization_id=org.id,
        email="fresh-support@example.com",
        full_name="Fresh Support",
    )
    db.add_all([role, candidate])
    db.flush()

    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        source="workable",
        cv_match_score=82,
        cv_match_details={
            "matching_skills": ["current matching skill"],
            "missing_skills": ["current missing skill"],
            "concerns": ["current concern"],
            "requirements_assessment": [
                {
                    "requirement": "Current requirement",
                    "status": "partial",
                    "evidence": "Current partial evidence",
                }
            ],
        },
        screening_pack=_pack(stage="screening", question="STALE screening"),
        tech_interview_pack=_pack(
            stage="tech_stage_2", question="STALE technical"
        ),
        screening_interview_summary={"summary": "STALE screening summary"},
        tech_interview_summary={"summary": "STALE tech summary"},
        interview_evidence_summary={
            "matching_skills": ["STALE skill"],
            "assessment_signal": {"assessment_score": 1},
        },
    )
    db.add(app)
    db.flush()

    task = Task(organization_id=org.id, name="Current assessment task")
    db.add(task)
    db.flush()
    db.add(
        Assessment(
            organization_id=org.id,
            candidate_id=candidate.id,
            role_id=role.id,
            application_id=app.id,
            task_id=task.id,
            status=AssessmentStatus.COMPLETED,
            assessment_score=88,
            completed_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        ApplicationInterview(
            organization_id=org.id,
            application_id=app.id,
            stage="screening",
            source="manual",
            provider="manual",
            status="completed",
            summary="Current interview summary",
            meeting_date=datetime.now(timezone.utc),
        )
    )
    db.commit()

    response = client.get(f"/api/v1/applications/{app.id}", headers=headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    screening_questions = [
        item["question"] for item in payload["screening_pack"]["questions"]
    ]
    tech_questions = [
        item["question"] for item in payload["tech_interview_pack"]["questions"]
    ]
    evidence = payload["interview_evidence_summary"]
    assert "Current screening question?" in screening_questions
    assert any("current requirement" in question.lower() for question in screening_questions)
    assert any("current concern" in question.lower() for question in screening_questions)
    assert "Current cached tech question?" in tech_questions
    assert payload["screening_interview_summary"]["summary"] == "Current interview summary"
    assert evidence["matching_skills"] == ["current matching skill"]
    assert evidence["missing_skills"] == ["current missing skill"]
    assert evidence["assessment_signal"]["assessment_score"] == 88
    assert evidence["assessment_signal"]["task_name"] == "Current assessment task"
    assert evidence["fireflies"]["configured"] is True
    assert evidence["fireflies"]["invite_email"] == "invite@fireflies.ai"
