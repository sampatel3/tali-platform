"""Atomic recruiter-facing role job-spec editor contract."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.domains.assessments_runtime import roles_management_routes
from app.models.agent_decision import AgentDecision
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.role import Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.task import Task
from app.models.user import User
from tests.conftest import auth_headers, create_task_via_api


SPEC_A = """# Backend Engineer

Description
Build reliable APIs for a high-volume hiring platform.

Requirements
- Python and FastAPI in production
- PostgreSQL schema and query design
- Must own production incidents end to end
"""

SPEC_B = """# Staff Platform Engineer

Description
Lead reliability and platform engineering across the product.

Requirements
- Kubernetes operations at scale
- Must design distributed systems
- Based in Dubai with hybrid working
"""


def _disable_focus_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(roles_management_routes, "on_role_jd_attached", lambda _role: None)


def test_job_spec_editor_persists_spec_name_tasks_and_diff(client, db, monkeypatch):
    _disable_focus_dispatch(monkeypatch)
    headers, _ = auth_headers(client)
    task_a = create_task_via_api(client, headers, name="API incident task").json()
    task_b = create_task_via_api(client, headers, name="Data model task").json()
    role = client.post("/api/v1/roles", json={"name": "Old title"}, headers=headers).json()

    response = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={
            "expected_version": role["version"],
            "name": "Backend Engineer",
            "job_spec_text": SPEC_A,
            "task_ids": [task_b["id"], task_a["id"], task_b["id"]],
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["applied"] is True
    assert payload["role"]["name"] == "Backend Engineer"
    assert payload["role"]["description"] == SPEC_A.strip()
    assert payload["role"]["job_spec_text"] == SPEC_A.strip()
    assert payload["role"]["job_spec_manually_edited_at"] is not None
    assert payload["role"]["tasks_count"] == 2
    assert payload["diff"]["criteria_count"] >= 1
    assert payload["diff"]["added"]
    assert payload["would_rescreen"] == {"count": 0, "est_cost_usd": 0.0}

    db.expire_all()
    saved = db.query(Role).filter(Role.id == role["id"]).first()
    assert saved.name == "Backend Engineer"
    assert saved.job_spec_text == saved.description == SPEC_A.strip()
    assert {task.id for task in saved.tasks} == {task_a["id"], task_b["id"]}
    assert saved.job_spec_manually_edited_at is not None
    assert saved.interview_focus is None
    assert saved.interview_focus_generated_at is None


def test_job_spec_editor_invalidates_without_authorizing_paid_rescore(
    client, db, monkeypatch
):
    _disable_focus_dispatch(monkeypatch)
    headers, email = auth_headers(client)
    role_payload = client.post(
        "/api/v1/roles", json={"name": "Stale score role"}, headers=headers
    ).json()
    user = db.query(User).filter(User.email == email).one()
    role = db.query(Role).filter(Role.id == role_payload["id"]).one()
    role.agentic_mode_enabled = True
    role.tech_questions_cached = [{"question": "Old question"}]
    role.tech_questions_signature = "old-signature"
    candidate = Candidate(
        organization_id=user.organization_id,
        email="job-spec-stale-score@example.com",
        full_name="Stale Score Candidate",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=user.organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        cv_text="Senior Python engineer with platform experience.",
        pre_screen_score_100=78.0,
        cv_match_score=84.0,
        pre_screen_run_at=datetime.now(timezone.utc),
    )
    db.add(application)
    db.flush()
    decision = AgentDecision(
        organization_id=user.organization_id,
        role_id=role.id,
        application_id=application.id,
        decision_type="advance_to_interview",
        recommendation="Advance",
        status="pending",
        reasoning="Based on the old job specification",
        model_version="test",
        prompt_version="test",
        idempotency_key=f"old-spec-{application.id}",
    )
    db.add(decision)
    db.commit()

    with patch(
        "app.tasks.scoring_tasks.sweep_stale_scores.apply_async"
    ) as score_dispatch, patch(
        "app.tasks.automation_tasks.regenerate_role_tech_questions.apply_async"
    ) as tech_dispatch:
        response = client.put(
            f"/api/v1/roles/{role.id}/job-spec",
            json={
                "expected_version": role_payload["version"],
                "job_spec_text": SPEC_A,
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scores_invalidated"] == 1
    assert payload["rescore_dispatch_approved"] is False
    assert payload["would_rescreen"]["count"] == 1
    score_dispatch.assert_not_called()
    tech_dispatch.assert_not_called()

    db.expire_all()
    application = db.get(CandidateApplication, application.id)
    decision = db.get(AgentDecision, decision.id)
    role = db.get(Role, role.id)
    stale = (
        db.query(CvScoreJob)
        .filter(
            CvScoreJob.application_id == application.id,
            CvScoreJob.status == "stale",
        )
        .one()
    )
    assert application.cv_match_score == 84.0
    assert application.pre_screen_score_100 == 78.0
    assert application.pre_screen_run_at is None
    assert decision.status == "discarded"
    assert role.tech_questions_signature is None
    assert role.tech_questions_cached == [{"question": "Old question"}]
    assert stale.dispatch_approved is False
    assert stale.requires_active_agent is True


def test_job_spec_editor_rejects_assessment_task_removal_before_mutation(
    client, db, monkeypatch
):
    _disable_focus_dispatch(monkeypatch)
    headers, email = auth_headers(client)
    task = create_task_via_api(client, headers, name="In-use assessment task").json()
    role = client.post("/api/v1/roles", json={"name": "Original title"}, headers=headers).json()
    first = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={"expected_version": role["version"], "job_spec_text": SPEC_A, "task_ids": [task["id"]]},
        headers=headers,
    )
    assert first.status_code == 200, first.text

    user = db.query(User).filter(User.email == email).first()
    candidate = Candidate(
        organization_id=user.organization_id,
        email="job-spec-atomic@example.com",
        full_name="Atomic Candidate",
    )
    db.add(candidate)
    db.flush()
    db.add(
        Assessment(
            organization_id=user.organization_id,
            candidate_id=candidate.id,
            role_id=role["id"],
            task_id=task["id"],
            token="job-spec-atomic-assessment",
        )
    )
    db.commit()

    conflict = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={
            "expected_version": first.json()["role"]["version"],
            "name": "Should not persist",
            "job_spec_text": SPEC_B,
            "task_ids": [],
        },
        headers=headers,
    )
    assert conflict.status_code == 409, conflict.text

    db.expire_all()
    saved = db.query(Role).filter(Role.id == role["id"]).first()
    assert saved.name == "Original title"
    assert saved.job_spec_text == SPEC_A.strip()
    assert {linked.id for linked in saved.tasks} == {task["id"]}


def test_job_spec_editor_omitted_task_ids_preserves_in_use_tasks(
    client, db, monkeypatch
):
    _disable_focus_dispatch(monkeypatch)
    headers, email = auth_headers(client)
    task = create_task_via_api(client, headers, name="Preserved assessment task").json()
    role = client.post(
        "/api/v1/roles", json={"name": "Editor-only update"}, headers=headers
    ).json()
    linked = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={"expected_version": role["version"], "job_spec_text": SPEC_A, "task_ids": [task["id"]]},
        headers=headers,
    )
    assert linked.status_code == 200, linked.text

    user = db.query(User).filter(User.email == email).first()
    candidate = Candidate(
        organization_id=user.organization_id,
        email="job-spec-preserve-task@example.com",
        full_name="Preserved Task Candidate",
    )
    db.add(candidate)
    db.flush()
    db.add(
        Assessment(
            organization_id=user.organization_id,
            candidate_id=candidate.id,
            role_id=role["id"],
            task_id=task["id"],
            token="job-spec-preserve-task-assessment",
        )
    )
    db.commit()

    # An editor that only owns name/spec fields omits task_ids. The linked,
    # already-used task must remain untouched and must not trip removal checks.
    response = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={"expected_version": linked.json()["role"]["version"], "name": "Updated without task payload", "job_spec_text": SPEC_B},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["role"]["tasks_count"] == 1
    db.expire_all()
    saved = db.query(Role).filter(Role.id == role["id"]).first()
    assert saved.name == "Updated without task payload"
    assert saved.job_spec_text == SPEC_B.strip()
    assert {linked_task.id for linked_task in saved.tasks} == {task["id"]}


def test_job_spec_editor_is_org_scoped_for_roles_and_tasks(client, db, monkeypatch):
    _disable_focus_dispatch(monkeypatch)
    headers_a, _ = auth_headers(client, organization_name="Spec editor org A")
    foreign_task = create_task_via_api(client, headers_a, name="Private org A task").json()
    foreign_role = client.post(
        "/api/v1/roles", json={"name": "Private org A role"}, headers=headers_a
    ).json()

    headers_b, _ = auth_headers(client, organization_name="Spec editor org B")
    local_role = client.post(
        "/api/v1/roles", json={"name": "Org B role"}, headers=headers_b
    ).json()

    role_scope = client.put(
        f"/api/v1/roles/{foreign_role['id']}/job-spec",
        json={"expected_version": foreign_role["version"], "job_spec_text": SPEC_A, "task_ids": []},
        headers=headers_b,
    )
    assert role_scope.status_code == 403

    task_scope = client.put(
        f"/api/v1/roles/{local_role['id']}/job-spec",
        json={"expected_version": local_role["version"], "job_spec_text": SPEC_A, "task_ids": [foreign_task["id"]]},
        headers=headers_b,
    )
    assert task_scope.status_code == 422

    db.expire_all()
    saved = db.query(Role).filter(Role.id == local_role["id"]).first()
    assert saved.job_spec_text is None
    assert saved.description is None
    assert saved.tasks == []

    global_task = Task(
        organization_id=None,
        name="Global assessment template",
        description="Visible to every organization",
        task_type="python",
    )
    db.add(global_task)
    db.commit()
    global_link = client.put(
        f"/api/v1/roles/{local_role['id']}/job-spec",
        json={"expected_version": local_role["version"], "job_spec_text": SPEC_A, "task_ids": [global_task.id]},
        headers=headers_b,
    )
    assert global_link.status_code == 200, global_link.text
    assert global_link.json()["role"]["tasks_count"] == 1


def test_job_spec_editor_rejects_short_specs_without_mutation(client, db, monkeypatch):
    _disable_focus_dispatch(monkeypatch)
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "Length guard"}, headers=headers).json()

    response = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={"expected_version": role["version"], "job_spec_text": "x" * 59, "task_ids": []},
        headers=headers,
    )
    assert response.status_code == 422

    db.expire_all()
    saved = db.query(Role).filter(Role.id == role["id"]).first()
    assert saved.job_spec_text is None
    assert saved.job_spec_manually_edited_at is None


def test_job_spec_editor_marks_sister_scores_stale_without_auto_spend(
    client, db, monkeypatch
):
    _disable_focus_dispatch(monkeypatch)
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    owner = Role(organization_id=user.organization_id, name="ATS owner")
    db.add(owner)
    db.flush()
    candidate = Candidate(
        organization_id=user.organization_id,
        email="related-spec-editor@example.com",
        full_name="Related Spec Candidate",
        cv_text="Python platform engineer with distributed systems experience.",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=user.organization_id,
        candidate_id=candidate.id,
        role_id=owner.id,
        cv_text=candidate.cv_text,
    )
    db.add(application)
    db.flush()
    sister = Role(
        organization_id=user.organization_id,
        name="Alternate scoring view",
        role_kind="sister",
        ats_owner_role_id=owner.id,
        job_spec_text=SPEC_A,
    )
    db.add(sister)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=user.organization_id,
        role_id=sister.id,
        source_application_id=application.id,
        status="done",
        spec_fingerprint="previous-spec",
        role_fit_score=84.0,
        summary="Strong match to the previous related-role spec.",
    )
    db.add(evaluation)
    db.commit()

    with patch(
        "app.tasks.sister_role_tasks.score_sister_role.apply_async"
    ) as dispatch:
        response = client.put(
            f"/api/v1/roles/{sister.id}/job-spec",
            json={
                "expected_version": int(sister.version or 1),
                "name": "Alternate platform view",
                "job_spec_text": SPEC_B,
            },
            headers=headers,
        )
    dispatch.assert_not_called()
    assert response.status_code == 200, response.text
    assert response.json()["role"]["name"] == "Alternate platform view"
    assert response.json()["role"]["job_spec_text"] == SPEC_B.strip()
    assert response.json()["would_rescreen"] == {"count": 1, "est_cost_usd": 0.08}
    assert response.json()["rescore_dispatch_approved"] is False

    db.expire_all()
    saved = db.query(Role).filter(Role.id == sister.id).first()
    reset = db.query(SisterRoleEvaluation).filter_by(id=evaluation.id).one()
    assert saved.job_spec_text == saved.description == SPEC_B.strip()
    assert reset.status == "stale"
    # Keep the last result visible as explicitly stale until the recruiter
    # confirms paid re-scoring; the archived snapshot remains the audit trail.
    assert reset.role_fit_score == 84.0
    assert reset.summary == "Strong match to the previous related-role spec."
    assert reset.spec_fingerprint != "previous-spec"
    assert reset.history[-1]["role_fit_score"] == 84.0
