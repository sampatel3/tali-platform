"""API tests for role-first recruiting workflow endpoints."""

import io
from datetime import datetime, timezone

from PyPDF2 import PdfReader

from app.domains.assessments_runtime import applications_routes
from app.domains.assessments_runtime import roles_management_routes
from app.models.assessment import Assessment, AssessmentStatus
from app.models.application_interview import ApplicationInterview
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers, create_task_via_api


def test_role_application_assessment_lifecycle(client):
    headers, _ = auth_headers(client)
    task_resp = create_task_via_api(client, headers, name="Role linked task")
    assert task_resp.status_code == 201
    task = task_resp.json()

    role_resp = client.post(
        "/api/v1/roles",
        json={"name": "Backend Engineer", "description": "Role for backend hiring"},
        headers=headers,
    )
    assert role_resp.status_code == 201, role_resp.text
    role = role_resp.json()

    link_resp = client.post(
        f"/api/v1/roles/{role['id']}/tasks",
        json={"task_id": task["id"]},
        headers=headers,
    )
    assert link_resp.status_code == 200, link_resp.text

    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Python backend role requirements"), "text/plain")}
    upload_job_spec = client.post(
        f"/api/v1/roles/{role['id']}/upload-job-spec",
        files=job_spec_file,
        headers=headers,
    )
    assert upload_job_spec.status_code == 200, upload_job_spec.text

    app_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={
            "candidate_email": "multi-role@example.com",
            "candidate_name": "Multi Role",
            "candidate_position": "Engineer",
        },
        headers=headers,
    )
    assert app_resp.status_code == 201, app_resp.text
    app_payload = app_resp.json()

    cv_file = {"file": ("resume.pdf", io.BytesIO(b"%PDF-1.4 role app cv"), "application/pdf")}
    upload_cv = client.post(
        f"/api/v1/applications/{app_payload['id']}/upload-cv",
        files=cv_file,
        headers=headers,
    )
    assert upload_cv.status_code == 200, upload_cv.text

    assessment_resp = client.post(
        f"/api/v1/applications/{app_payload['id']}/assessments",
        json={"task_id": task["id"], "duration_minutes": 45},
        headers=headers,
    )
    assert assessment_resp.status_code == 201, assessment_resp.text
    assessment = assessment_resp.json()
    assert assessment["task_id"] == task["id"]
    assert assessment["role_id"] == role["id"]
    assert assessment["application_id"] == app_payload["id"]


def test_single_candidate_can_have_multiple_role_applications(client):
    headers, _ = auth_headers(client)

    role_1 = client.post("/api/v1/roles", json={"name": "Data Engineer"}, headers=headers).json()
    role_2 = client.post("/api/v1/roles", json={"name": "ML Engineer"}, headers=headers).json()
    spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Role requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role_1['id']}/upload-job-spec", files=spec_file, headers=headers).status_code == 200
    spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Role requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role_2['id']}/upload-job-spec", files=spec_file, headers=headers).status_code == 200

    app_1 = client.post(
        f"/api/v1/roles/{role_1['id']}/applications",
        json={"candidate_email": "same-person@example.com", "candidate_name": "Same Person"},
        headers=headers,
    )
    app_2 = client.post(
        f"/api/v1/roles/{role_2['id']}/applications",
        json={"candidate_email": "same-person@example.com", "candidate_name": "Same Person"},
        headers=headers,
    )
    assert app_1.status_code == 201, app_1.text
    assert app_2.status_code == 201, app_2.text
    assert app_1.json()["candidate_id"] == app_2.json()["candidate_id"]
    assert app_1.json()["role_id"] != app_2.json()["role_id"]


def test_assessments_filters_by_role_and_application(client):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="Filter task").json()
    role = client.post("/api/v1/roles", json={"name": "Filter role"}, headers=headers).json()
    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Role filter requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=job_spec_file, headers=headers).status_code == 200
    client.post(f"/api/v1/roles/{role['id']}/tasks", json={"task_id": task["id"]}, headers=headers)
    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "filters@example.com", "candidate_name": "Filters"},
        headers=headers,
    ).json()
    cv_file = {"file": ("resume.pdf", io.BytesIO(b"%PDF-1.4 filters cv"), "application/pdf")}
    assert client.post(f"/api/v1/applications/{app['id']}/upload-cv", files=cv_file, headers=headers).status_code == 200
    create_assessment = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": task["id"]},
        headers=headers,
    )
    assert create_assessment.status_code == 201
    assessment = create_assessment.json()

    list_by_role = client.get(f"/api/v1/assessments/?role_id={role['id']}", headers=headers)
    assert list_by_role.status_code == 200
    role_items = list_by_role.json()["items"]
    assert any(item["id"] == assessment["id"] for item in role_items)

    list_by_app = client.get(f"/api/v1/assessments/?application_id={app['id']}", headers=headers)
    assert list_by_app.status_code == 200
    app_items = list_by_app.json()["items"]
    assert any(item["id"] == assessment["id"] for item in app_items)


def test_reject_assessment_for_task_not_linked_to_role(client):
    headers, _ = auth_headers(client)
    linked_task = create_task_via_api(client, headers, name="Linked task").json()
    unlinked_task = create_task_via_api(client, headers, name="Unlinked task").json()
    role = client.post("/api/v1/roles", json={"name": "Validation role"}, headers=headers).json()
    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Validation role requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=job_spec_file, headers=headers).status_code == 200
    client.post(f"/api/v1/roles/{role['id']}/tasks", json={"task_id": linked_task["id"]}, headers=headers)
    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "validation@example.com"},
        headers=headers,
    ).json()
    cv_file = {"file": ("resume.pdf", io.BytesIO(b"%PDF-1.4 validation cv"), "application/pdf")}
    assert client.post(f"/api/v1/applications/{app['id']}/upload-cv", files=cv_file, headers=headers).status_code == 200

    resp = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": unlinked_task["id"]},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "not linked" in resp.json()["detail"].lower()


def test_reject_application_without_job_spec(client):
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "No spec role"}, headers=headers).json()
    resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "nospec@example.com"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "job spec" in resp.json()["detail"].lower()


def test_allow_assessment_creation_without_application_cv(client):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="CV gate task").json()
    role = client.post("/api/v1/roles", json={"name": "CV gate role"}, headers=headers).json()
    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"CV gate requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=job_spec_file, headers=headers).status_code == 200
    assert client.post(f"/api/v1/roles/{role['id']}/tasks", json={"task_id": task["id"]}, headers=headers).status_code == 200
    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "nocv@example.com"},
        headers=headers,
    ).json()

    resp = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": task["id"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["application_id"] == app["id"]
    assert body["task_id"] == task["id"]


def test_reject_role_assessment_creation_without_available_credits(client, db, monkeypatch):
    headers, email = auth_headers(client)
    task = create_task_via_api(client, headers, name="Credit gate task").json()
    role = client.post("/api/v1/roles", json={"name": "Credit gate role"}, headers=headers).json()
    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Credit gate requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=job_spec_file, headers=headers).status_code == 200
    assert client.post(f"/api/v1/roles/{role['id']}/tasks", json={"task_id": task["id"]}, headers=headers).status_code == 200
    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "credit-gate@example.com"},
        headers=headers,
    ).json()

    user = db.query(User).filter(User.email == email).first()
    assert user is not None
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    assert org is not None
    org.credits_balance = 0
    db.commit()

    import app.components.assessments.service as assessments_svc

    monkeypatch.setattr(assessments_svc.settings, "MVP_DISABLE_LEMON", False)

    resp = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": task["id"]},
        headers=headers,
    )
    assert resp.status_code == 402
    assert "purchase credits" in resp.json()["detail"].lower()


def test_duplicate_role_assessment_requires_retake(client):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="Retake guard task").json()
    role = client.post("/api/v1/roles", json={"name": "Retake guard role"}, headers=headers).json()
    spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Retake guard requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=spec_file, headers=headers).status_code == 200
    assert client.post(f"/api/v1/roles/{role['id']}/tasks", json={"task_id": task["id"]}, headers=headers).status_code == 200
    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "retake-guard@example.com"},
        headers=headers,
    ).json()

    first = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": task["id"], "duration_minutes": 45},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    first_assessment = first.json()

    duplicate = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": task["id"], "duration_minutes": 45},
        headers=headers,
    )
    assert duplicate.status_code == 409, duplicate.text
    detail = duplicate.json()["detail"]
    assert detail["code"] == "retake_required"
    assert detail["assessment_id"] == first_assessment["id"]


def test_role_assessment_retake_voids_previous_attempt(client, db):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="Retake lifecycle task").json()
    role = client.post("/api/v1/roles", json={"name": "Retake lifecycle role"}, headers=headers).json()
    spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Retake lifecycle requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=spec_file, headers=headers).status_code == 200
    assert client.post(f"/api/v1/roles/{role['id']}/tasks", json={"task_id": task["id"]}, headers=headers).status_code == 200
    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "retake-lifecycle@example.com"},
        headers=headers,
    ).json()

    first = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": task["id"], "duration_minutes": 45},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    first_assessment = first.json()

    retake = client.post(
        f"/api/v1/applications/{app['id']}/assessments/retake",
        json={"task_id": task["id"], "duration_minutes": 30, "void_reason": "Candidate hit an environment issue"},
        headers=headers,
    )
    assert retake.status_code == 201, retake.text
    retake_assessment = retake.json()
    assert retake_assessment["id"] != first_assessment["id"]

    old_row = db.query(Assessment).filter(Assessment.id == first_assessment["id"]).first()
    new_row = db.query(Assessment).filter(Assessment.id == retake_assessment["id"]).first()
    assert old_row is not None
    assert new_row is not None
    assert old_row.is_voided is True
    assert old_row.superseded_by_assessment_id == new_row.id
    assert old_row.void_reason == "Candidate hit an environment issue"
    assert new_row.is_voided is False

    default_list = client.get("/api/v1/assessments/", headers=headers)
    assert default_list.status_code == 200, default_list.text
    default_ids = [item["id"] for item in default_list.json()["items"]]
    assert new_row.id in default_ids
    assert old_row.id not in default_ids

    history_list = client.get("/api/v1/assessments/?include_voided=true", headers=headers)
    assert history_list.status_code == 200, history_list.text
    history_ids = [item["id"] for item in history_list.json()["items"]]
    assert new_row.id in history_ids
    assert old_row.id in history_ids


def test_role_assessment_retake_reuses_pending_credit_reservation(client, db, monkeypatch):
    headers, email = auth_headers(client)
    task = create_task_via_api(client, headers, name="Retake reserved credit task").json()
    role = client.post("/api/v1/roles", json={"name": "Retake reserved credit role"}, headers=headers).json()
    spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Retake reserved credit requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=spec_file, headers=headers).status_code == 200
    assert client.post(f"/api/v1/roles/{role['id']}/tasks", json={"task_id": task["id"]}, headers=headers).status_code == 200
    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "retake-reserved@example.com"},
        headers=headers,
    ).json()

    user = db.query(User).filter(User.email == email).first()
    assert user is not None
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    assert org is not None
    org.credits_balance = 1
    db.commit()

    import app.components.assessments.service as assessments_svc

    monkeypatch.setattr(assessments_svc.settings, "MVP_DISABLE_LEMON", False)

    first = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": task["id"], "duration_minutes": 45},
        headers=headers,
    )
    assert first.status_code == 201, first.text

    retake = client.post(
        f"/api/v1/applications/{app['id']}/assessments/retake",
        json={"task_id": task["id"], "duration_minutes": 30, "void_reason": "Reset attempt"},
        headers=headers,
    )
    assert retake.status_code == 201, retake.text


def test_role_application_summary_uses_role_fit_before_completion_and_hierarchical_taali_after_completion(client, db):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="Score summary task").json()
    role = client.post("/api/v1/roles", json={"name": "Score summary role"}, headers=headers).json()
    spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Score summary requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=spec_file, headers=headers).status_code == 200
    assert client.post(f"/api/v1/roles/{role['id']}/tasks", json={"task_id": task["id"]}, headers=headers).status_code == 200
    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "summary-score@example.com", "candidate_name": "Summary Score"},
        headers=headers,
    ).json()

    app_row = db.query(CandidateApplication).filter(CandidateApplication.id == app["id"]).first()
    app_row.cv_match_score = 82.0
    app_row.cv_match_details = {
        "score_scale": "0-100",
        "requirements_match_score_100": 74.0,
    }
    db.commit()

    pre = client.get(f"/api/v1/roles/{role['id']}/applications?sort_by=taali_score", headers=headers)
    assert pre.status_code == 200, pre.text
    pre_item = pre.json()[0]
    assert pre_item["taali_score"] == 78.0
    assert pre_item["score_mode"] == "role_fit_only"
    assert pre_item["score_summary"]["assessment_score"] is None
    assert pre_item["score_summary"]["role_fit_score"] == 78.0
    assert pre_item["score_summary"]["requirements_fit_score"] == 74.0
    assert pre_item["score_summary"]["weights"]["assessment_score"] == 0.5
    assert pre_item["score_summary"]["weights"]["role_fit_score"] == 0.5

    created = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": task["id"], "duration_minutes": 45},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assessment_id = created.json()["id"]

    assessment_row = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    assessment_row.status = AssessmentStatus.COMPLETED
    assessment_row.completed_at = datetime.now(timezone.utc)
    assessment_row.assessment_score = 70.0
    assessment_row.taali_score = 74.0
    assessment_row.final_score = 70.0
    assessment_row.cv_job_match_score = 82.0
    assessment_row.cv_job_match_details = {
        "score_scale": "0-100",
        "requirements_match_score_100": 74.0,
    }
    db.commit()

    post = client.get(f"/api/v1/roles/{role['id']}/applications?sort_by=taali_score", headers=headers)
    assert post.status_code == 200, post.text
    post_item = post.json()[0]
    assert post_item["taali_score"] == 74.0
    assert post_item["score_mode"] == "assessment_plus_role_fit"
    assert post_item["valid_assessment_id"] == assessment_id
    assert post_item["valid_assessment_status"] == AssessmentStatus.COMPLETED.value
    assert post_item["score_summary"]["assessment_score"] == 70.0
    assert post_item["score_summary"]["cv_fit_score"] == 82.0
    assert post_item["score_summary"]["role_fit_score"] == 78.0
    assert post_item["score_summary"]["taali_score"] == 74.0


def test_application_interview_debrief_and_client_report_work_before_completion(client, db):
    headers, email = auth_headers(client)
    user_row = db.query(User).filter(User.email == email).first()
    assert user_row is not None
    org_row = db.query(Organization).filter(Organization.id == user_row.organization_id).first()
    assert org_row is not None
    org_row.fireflies_owner_email = "recruiter@example.com"
    org_row.fireflies_api_key_encrypted = "encrypted"
    org_row.fireflies_invite_email = "taali@fireflies.ai"

    role_row = Role(
        organization_id=org_row.id,
        name="Platform Engineer",
        job_spec_filename="job-spec.txt",
        job_spec_text="Platform engineering role requirements",
    )
    db.add(role_row)
    db.flush()

    candidate_row = Candidate(
        organization_id=org_row.id,
        email="pre-assessment@example.com",
        full_name="Pre Assessment",
    )
    db.add(candidate_row)
    db.flush()

    app_row = CandidateApplication(
        organization_id=org_row.id,
        candidate_id=candidate_row.id,
        role_id=role_row.id,
    )
    db.add(app_row)
    db.flush()

    role_row.interview_focus = {
        "role_summary": "Validate systems design depth and operational judgment.",
        "questions": [
            {
                "question": "Walk me through a recent incident you owned end to end.",
                "what_to_listen_for": ["Specific tradeoffs", "Root cause analysis", "Measurable outcomes"],
                "concerning_signals": ["Vague ownership"],
            },
        ],
    }
    app_row.cv_match_score = 88.0
    app_row.cv_match_details = {
        "score_scale": "0-100",
        "summary": "Strong CV evidence for platform and backend delivery, with one infrastructure gap to validate.",
        "requirements_match_score_100": 82.0,
        "requirements_coverage": {
            "total": 3,
            "met": 2,
            "partially_met": 1,
            "missing": 0,
        },
        "matching_skills": ["Python", "FastAPI", "Distributed systems"],
        "missing_skills": ["Kubernetes"],
        "experience_highlights": ["Led backend platform delivery for production systems."],
        "concerns": ["Infrastructure automation depth needs validation."],
        "requirements_assessment": [
            {
                "requirement": "Production platform ownership",
                "status": "met",
                "evidence": "Relevant production platform history is present in the CV.",
            },
            {
                "requirement": "Kubernetes operations",
                "status": "partially_met",
                "evidence": "Adjacent infrastructure work is clear, but Kubernetes examples are thin.",
            },
        ],
    }
    app_row.cv_filename = "pre-assessment.txt"
    app_row.cv_text = (
        "Pre Assessment\n"
        "Senior platform engineer with Python, FastAPI, and distributed systems delivery.\n\n"
        "Experience\n"
        "- Led backend platform delivery for production systems.\n"
        "- Built operational tooling for engineering teams.\n"
    )
    db.add(
        ApplicationInterview(
            organization_id=app_row.organization_id,
            application_id=app_row.id,
            stage="screening",
            source="fireflies",
            provider="fireflies",
            provider_meeting_id="ff-screening-1",
            provider_url="https://fireflies.ai/view/ff-screening-1",
            status="completed",
            transcript_text="Recruiter: Tell me about Kubernetes ownership.\nCandidate: I partnered closely with platform teams but want deeper direct ownership.",
            summary="Stage 1 Fireflies transcript confirmed strong platform ownership with a Kubernetes depth gap to validate.",
            speakers=[{"name": "Recruiter"}, {"name": "Candidate"}],
            provider_payload={
                "title": "Pre Assessment screening",
                "taali_match": {
                    "fireflies_invite_email": "taali@fireflies.ai",
                    "linked_via": "webhook_auto_match",
                    "matched_application_id": app_row.id,
                },
            },
            meeting_date=datetime(2026, 4, 24, 10, 15, tzinfo=timezone.utc),
            linked_at=datetime(2026, 4, 24, 10, 30, tzinfo=timezone.utc),
        )
    )
    db.commit()

    debrief_resp = client.post(
        f"/api/v1/applications/{app_row.id}/interview-debrief",
        json={},
        headers=headers,
    )
    assert debrief_resp.status_code == 200, debrief_resp.text
    debrief_payload = debrief_resp.json()
    assert debrief_payload["cached"] is False
    assert "Stage 1 Fireflies transcript is linked" in debrief_payload["interview_debrief"]["summary"]
    assert "No completed assessment exists yet" in debrief_payload["interview_debrief"]["summary"]
    assert debrief_payload["interview_debrief"]["fireflies_context"]["status"] == "linked"
    assert debrief_payload["interview_debrief"]["fireflies_context"]["invite_email"] == "taali@fireflies.ai"
    assert len(debrief_payload["interview_debrief"]["probing_questions"]) >= 1
    assert debrief_payload["interview_debrief"]["probing_questions"][0]["dimension"] == "Stage 1 screening"

    report_resp = client.get(f"/api/v1/applications/{app_row.id}/report.pdf", headers=headers)
    assert report_resp.status_code == 200, report_resp.text
    assert report_resp.headers["content-type"].startswith("application/pdf")
    assert 'filename="Platform Engineer-Pre Assessment.pdf"' in report_resp.headers["content-disposition"]

    reader = PdfReader(io.BytesIO(report_resp.content))
    assert len(reader.pages) == 2
    first_page_text = reader.pages[0].extract_text() or ""
    second_page_text = reader.pages[1].extract_text() or ""
    assert "Client Assessment Summary" in first_page_text
    assert "Candidate summary" in first_page_text
    assert "Review key points" in first_page_text
    assert "No completed assessment exists yet" in first_page_text
    assert "Pre Assessment" in second_page_text
    assert "Senior platform engineer with Python" in second_page_text


def test_reject_delete_role_with_existing_application(client):
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "Delete guard role"}, headers=headers).json()
    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Delete guard requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=job_spec_file, headers=headers).status_code == 200
    app_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "delete-guard@example.com"},
        headers=headers,
    )
    assert app_resp.status_code == 201

    delete_resp = client.delete(f"/api/v1/roles/{role['id']}", headers=headers)
    assert delete_resp.status_code == 400
    assert "applications" in delete_resp.json()["detail"].lower()


def test_reject_unlink_role_task_when_assessment_exists(client):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="Unlink guard task").json()
    role = client.post("/api/v1/roles", json={"name": "Unlink guard role"}, headers=headers).json()
    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Unlink guard requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=job_spec_file, headers=headers).status_code == 200
    assert client.post(f"/api/v1/roles/{role['id']}/tasks", json={"task_id": task["id"]}, headers=headers).status_code == 200
    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "unlink-guard@example.com"},
        headers=headers,
    ).json()
    cv_file = {"file": ("resume.pdf", io.BytesIO(b"%PDF-1.4 unlink guard cv"), "application/pdf")}
    assert client.post(f"/api/v1/applications/{app['id']}/upload-cv", files=cv_file, headers=headers).status_code == 200
    created = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": task["id"]},
        headers=headers,
    )
    assert created.status_code == 201

    unlink_resp = client.delete(f"/api/v1/roles/{role['id']}/tasks/{task['id']}", headers=headers)
    assert unlink_resp.status_code == 400
    assert "already has assessments" in unlink_resp.json()["detail"].lower()


def test_application_cv_match_score_is_returned(client, monkeypatch):
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "CV match role"}, headers=headers).json()
    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Python backend API SQL role"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=job_spec_file, headers=headers).status_code == 200

    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "fit@example.com", "candidate_name": "Fit Candidate"},
        headers=headers,
    ).json()

    monkeypatch.setattr(applications_routes.settings, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        applications_routes,
        "process_document_upload",
        lambda **_: {
            "file_url": "/tmp/mock-resume.pdf",
            "filename": "resume.pdf",
            "extracted_text": "Python SQL backend API",
            "text_preview": "Python SQL backend API",
        },
    )
    monkeypatch.setattr(
        applications_routes,
        "calculate_cv_job_match_sync",
        lambda **_: {
            "cv_job_match_score": 84,
            "skills_match": 80,
            "experience_relevance": 88,
            "match_details": {
                "summary": "Strong API and SQL alignment.",
                "score_scale": "0-100",
                "score_rationale_bullets": [
                    "Composite fit 84/100 from strong API skills and relevant backend delivery.",
                    "Recruiter requirements coverage: 2/3 met, 1 partial, 0 missing.",
                ],
            },
        },
    )

    cv_file = {"file": ("resume.pdf", io.BytesIO(b"%PDF-1.4 python sql backend api"), "application/pdf")}
    upload_resp = client.post(f"/api/v1/applications/{app['id']}/upload-cv", files=cv_file, headers=headers)
    assert upload_resp.status_code == 200, upload_resp.text

    list_resp = client.get(f"/api/v1/roles/{role['id']}/applications", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    apps = list_resp.json()
    assert len(apps) == 1
    assert apps[0]["cv_match_score"] == 84.0
    assert apps[0]["cv_match_details"]["summary"] == "Strong API and SQL alignment."
    assert len(apps[0]["cv_match_details"]["score_rationale_bullets"]) == 2
    assert apps[0]["cv_match_scored_at"] is not None


def test_job_spec_upload_generates_interview_focus(client, monkeypatch):
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "Interview focus role"}, headers=headers).json()

    monkeypatch.setattr(roles_management_routes.settings, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        roles_management_routes,
        "process_document_upload",
        lambda **_: {
            "file_url": "/tmp/mock-job-spec.pdf",
            "filename": "job-spec.pdf",
            "extracted_text": "Senior backend role requiring APIs, SQL, and incident ownership.",
            "text_preview": "Senior backend role requiring APIs, SQL, and incident ownership.",
        },
    )
    monkeypatch.setattr(
        roles_management_routes,
        "generate_interview_focus_sync",
        lambda **_: {
            "role_summary": "Role needs strong API design, data modeling, and production ownership.",
            "manual_screening_triggers": ["Hands-on API ownership", "Database depth"],
            "questions": [
                {
                    "question": "Describe an API you designed end-to-end.",
                    "what_to_listen_for": ["Tradeoffs, scale, reliability"],
                    "concerning_signals": ["Vague ownership"],
                },
                {
                    "question": "How did you optimize a slow SQL workload?",
                    "what_to_listen_for": ["Profiling approach", "Index strategy"],
                    "concerning_signals": ["No concrete example"],
                },
                {
                    "question": "Walk through an incident you led in production.",
                    "what_to_listen_for": ["Root cause, mitigation, prevention"],
                    "concerning_signals": ["No post-incident learning"],
                },
            ],
        },
    )

    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"role requirements"), "text/plain")}
    upload_resp = client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=job_spec_file, headers=headers)
    assert upload_resp.status_code == 200, upload_resp.text
    payload = upload_resp.json()
    assert payload["interview_focus_generated"] is True
    assert payload["interview_focus_error"] is None
    assert len(payload["interview_focus"]["questions"]) == 3
    assert payload["interview_focus_generated_at"] is not None

    list_resp = client.get("/api/v1/roles", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    roles = list_resp.json()
    assert len(roles) >= 1
    assert roles[0]["interview_focus"]["questions"][0]["question"] == "Describe an API you designed end-to-end."
    assert roles[0]["interview_focus_generated_at"] is not None


def test_job_spec_upload_returns_interview_focus_error_when_api_key_missing(client, monkeypatch):
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "No key role"}, headers=headers).json()

    monkeypatch.setattr(roles_management_routes.settings, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(
        roles_management_routes,
        "process_document_upload",
        lambda **_: {
            "file_url": "/tmp/mock-job-spec.pdf",
            "filename": "job-spec.pdf",
            "extracted_text": "Role requirements for production backend engineer.",
            "text_preview": "Role requirements for production backend engineer.",
        },
    )

    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"role requirements"), "text/plain")}
    upload_resp = client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=job_spec_file, headers=headers)
    assert upload_resp.status_code == 200, upload_resp.text
    payload = upload_resp.json()
    assert payload["interview_focus_generated"] is False
    assert payload["interview_focus"] is None
    assert "not configured" in (payload["interview_focus_error"] or "").lower()


def test_list_role_applications_supports_score_sort_and_source_filter(client, db):
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "Rank role"}, headers=headers).json()
    spec = {"file": ("job-spec.txt", io.BytesIO(b"Role requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=spec, headers=headers).status_code == 200

    app_manual = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "manual-rank@example.com", "candidate_name": "Manual Rank"},
        headers=headers,
    ).json()
    app_workable = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "workable-rank@example.com", "candidate_name": "Workable Rank"},
        headers=headers,
    ).json()

    row_manual = db.query(CandidateApplication).filter(CandidateApplication.id == app_manual["id"]).first()
    row_workable = db.query(CandidateApplication).filter(CandidateApplication.id == app_workable["id"]).first()
    row_manual.source = "manual"
    row_manual.cv_match_score = 6.2
    row_manual.rank_score = 6.2
    row_workable.source = "workable"
    row_workable.workable_score = 8.7
    row_workable.rank_score = 8.7
    db.commit()

    ranked = client.get(
        f"/api/v1/roles/{role['id']}/applications?sort_by=rank_score&sort_order=desc",
        headers=headers,
    )
    assert ranked.status_code == 200, ranked.text
    data = ranked.json()
    assert len(data) == 2
    assert data[0]["rank_score"] >= data[1]["rank_score"]
    assert data[0]["source"] == "workable"

    workable_only = client.get(
        f"/api/v1/roles/{role['id']}/applications?source=workable&min_workable_score=8",
        headers=headers,
    )
    assert workable_only.status_code == 200, workable_only.text
    filtered = workable_only.json()
    assert len(filtered) == 1
    assert filtered[0]["source"] == "workable"


def _create_role_with_spec(client, headers, *, name: str) -> dict:
    role_resp = client.post("/api/v1/roles", json={"name": name}, headers=headers)
    assert role_resp.status_code == 201, role_resp.text
    role = role_resp.json()
    spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Role requirements"), "text/plain")}
    spec_resp = client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=spec_file, headers=headers)
    assert spec_resp.status_code == 200, spec_resp.text
    return role


def test_application_manual_interview_and_fireflies_link_endpoints(client, db, monkeypatch):
    headers, email = auth_headers(client)

    user_row = db.query(User).filter(User.email == email).first()
    assert user_row is not None
    org_row = db.query(Organization).filter(Organization.id == user_row.organization_id).first()
    assert org_row is not None
    org_row.fireflies_owner_email = "recruiter@example.com"
    org_row.fireflies_api_key_encrypted = "encrypted"
    org_row.fireflies_invite_email = "taali@fireflies.ai"
    role_row = Role(
        organization_id=org_row.id,
        name="Interview support role",
        job_spec_filename="job-spec.txt",
        job_spec_text="Role requirements",
    )
    db.add(role_row)
    db.flush()
    candidate_row = Candidate(
        organization_id=org_row.id,
        email="interview-support@example.com",
        full_name="Interview Support",
    )
    db.add(candidate_row)
    db.flush()
    app_row = CandidateApplication(
        organization_id=org_row.id,
        candidate_id=candidate_row.id,
        role_id=role_row.id,
    )
    db.add(app_row)
    db.commit()

    manual_resp = client.post(
        f"/api/v1/applications/{app_row.id}/interviews",
        json={
            "stage": "screening",
            "transcript_text": "Recruiter: Tell me about your API work.\nCandidate: Built distributed services.",
            "summary": "Strong recruiter screen with clear backend examples.",
            "speakers": [{"name": "Recruiter"}, {"name": "Candidate"}],
        },
        headers=headers,
    )
    assert manual_resp.status_code == 201, manual_resp.text
    manual = manual_resp.json()
    assert manual["stage"] == "screening"
    assert manual["source"] == "manual"
    assert manual["provider"] == "manual"
    assert "distributed services" in manual["transcript_text"]

    class _DummyFireflies:
        def get_transcript(self, meeting_id):
            assert meeting_id == "meeting-123"
            return {
                "id": meeting_id,
                "date": "2026-04-24T09:30:00Z",
                "transcript_url": "https://fireflies.ai/view/meeting-123",
                "organizer_email": "recruiter@example.com",
                "participants": [
                    "interview-support@example.com",
                    "recruiter@example.com",
                    "taali@fireflies.ai",
                ],
                "speakers": [{"id": "1", "name": "Interviewer"}, {"id": "2", "name": "Candidate"}],
                "summary": {"short_summary": "Candidate handled architecture tradeoffs well."},
                "sentences": [
                    {"speaker_name": "Interviewer", "text": "Explain service boundaries."},
                    {"speaker_name": "Candidate", "text": "We separated ingestion and scoring services."},
                ],
            }

    monkeypatch.setattr(applications_routes, "_fireflies_service_for_org", lambda org: _DummyFireflies())

    fireflies_resp = client.post(
        f"/api/v1/applications/{app_row.id}/interviews/fireflies-link",
        json={
            "stage": "tech_stage_2",
            "fireflies_meeting_id": "meeting-123",
        },
        headers=headers,
    )
    assert fireflies_resp.status_code == 201, fireflies_resp.text
    linked = fireflies_resp.json()
    assert linked["stage"] == "tech_stage_2"
    assert linked["source"] == "fireflies"
    assert linked["provider"] == "fireflies"
    assert linked["provider_meeting_id"] == "meeting-123"
    assert "separated ingestion and scoring services" in linked["transcript_text"]
    assert linked["provider_payload"]["taali_match"]["fireflies_invite_email"] == "taali@fireflies.ai"

    app_detail_resp = client.get(f"/api/v1/applications/{app_row.id}", headers=headers)
    assert app_detail_resp.status_code == 200, app_detail_resp.text
    app_detail = app_detail_resp.json()
    assert len(app_detail["interviews"]) == 2
    assert app_detail["screening_interview_summary"] is not None
    assert app_detail["tech_interview_summary"] is not None
    assert app_detail["interview_evidence_summary"] is not None
    assert app_detail["tech_interview_summary"]["fireflies"]["status"] == "linked"
    assert app_detail["tech_interview_summary"]["fireflies"]["invite_email"] == "taali@fireflies.ai"
    assert app_detail["interview_evidence_summary"]["fireflies"]["latest_provider_meeting_id"] == "meeting-123"


def test_pipeline_stage_and_outcome_endpoints_enforce_guards_and_events(client):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Pipeline guard role")

    create_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "pipeline-guard@example.com", "candidate_name": "Pipeline Guard"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    app = create_resp.json()
    assert app["pipeline_stage"] == "applied"
    assert app["application_outcome"] == "open"
    assert app["version"] == 1

    events_resp = client.get(f"/api/v1/applications/{app['id']}/events", headers=headers)
    assert events_resp.status_code == 200, events_resp.text
    event_types = [event["event_type"] for event in events_resp.json()]
    assert "pipeline_initialized" in event_types

    move_resp = client.patch(
        f"/api/v1/applications/{app['id']}/stage",
        json={
            "pipeline_stage": "invited",
            "expected_version": 1,
            "reason": "Send invite",
            "idempotency_key": "move-invited-1",
        },
        headers=headers,
    )
    assert move_resp.status_code == 200, move_resp.text
    moved = move_resp.json()
    assert moved["pipeline_stage"] == "invited"
    assert moved["version"] == 2

    blocked_resp = client.patch(
        f"/api/v1/applications/{app['id']}/stage",
        json={"pipeline_stage": "review", "expected_version": 2},
        headers=headers,
    )
    assert blocked_resp.status_code == 409, blocked_resp.text
    assert "not allowed" in blocked_resp.json()["detail"].lower()

    close_resp = client.patch(
        f"/api/v1/applications/{app['id']}/outcome",
        json={
            "application_outcome": "rejected",
            "expected_version": 2,
            "reason": "Did not pass rubric bar",
            "idempotency_key": "outcome-rejected-1",
        },
        headers=headers,
    )
    assert close_resp.status_code == 200, close_resp.text
    closed = close_resp.json()
    assert closed["application_outcome"] == "rejected"
    assert closed["status"] == "rejected"
    assert closed["version"] == 3

    events_resp = client.get(f"/api/v1/applications/{app['id']}/events", headers=headers)
    assert events_resp.status_code == 200, events_resp.text
    event_types = [event["event_type"] for event in events_resp.json()]
    assert "pipeline_stage_changed" in event_types
    assert "application_outcome_changed" in event_types


def test_workable_linked_application_reject_writes_back_before_local_close(client, db, monkeypatch):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Workable reject role")

    create_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "workable-reject@example.com", "candidate_name": "Workable Reject"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    app = create_resp.json()

    app_row = db.query(CandidateApplication).filter(CandidateApplication.id == app["id"]).first()
    assert app_row is not None
    app_row.source = "workable"
    app_row.workable_candidate_id = "workable-candidate-1"
    db.commit()

    captured = {}

    def fake_disqualify(*, org, app, role, reason=None, withdrew=False):
        captured["org_id"] = getattr(org, "id", None)
        captured["app_id"] = getattr(app, "id", None)
        captured["role_id"] = getattr(role, "id", None) if role is not None else None
        captured["reason"] = reason
        captured["withdrew"] = withdrew
        return {
            "success": True,
            "action": "disqualify",
            "code": "ok",
            "message": "Candidate disqualified in Workable",
            "config": {
                "actor_member_id": "member-1",
                "workable_disqualify_reason_id": "reason-1",
            },
        }

    monkeypatch.setattr(applications_routes, "disqualify_candidate_in_workable", fake_disqualify)

    reject_resp = client.patch(
        f"/api/v1/applications/{app['id']}/outcome",
        json={
            "application_outcome": "rejected",
            "expected_version": app["version"],
            "reason": "Below rubric bar",
            "idempotency_key": "workable-reject-1",
        },
        headers=headers,
    )
    assert reject_resp.status_code == 200, reject_resp.text
    payload = reject_resp.json()
    assert payload["application_outcome"] == "rejected"
    assert payload["version"] == app["version"] + 1
    assert captured == {
        "org_id": payload["organization_id"],
        "app_id": app["id"],
        "role_id": role["id"],
        "reason": "Below rubric bar",
        "withdrew": False,
    }

    events_resp = client.get(f"/api/v1/applications/{app['id']}/events", headers=headers)
    assert events_resp.status_code == 200, events_resp.text
    event_types = [event["event_type"] for event in events_resp.json()]
    assert "application_outcome_changed" in event_types
    assert "workable_disqualified" in event_types


def test_workable_linked_application_reopen_reverts_disqualification(client, db, monkeypatch):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Workable reopen role")

    create_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "workable-reopen@example.com", "candidate_name": "Workable Reopen"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    app = create_resp.json()

    app_row = db.query(CandidateApplication).filter(CandidateApplication.id == app["id"]).first()
    assert app_row is not None
    app_row.source = "workable"
    app_row.workable_candidate_id = "workable-candidate-2"
    app_row.application_outcome = "rejected"
    app_row.status = "rejected"
    app_row.application_outcome_updated_at = datetime.now(timezone.utc)
    app_row.version = 2
    db.commit()

    captured = {}

    def fake_revert(*, org, app, role):
        captured["org_id"] = getattr(org, "id", None)
        captured["app_id"] = getattr(app, "id", None)
        captured["role_id"] = getattr(role, "id", None) if role is not None else None
        return {
            "success": True,
            "action": "revert",
            "code": "ok",
            "message": "Candidate disqualification reverted in Workable",
            "config": {
                "actor_member_id": "member-1",
                "workable_disqualify_reason_id": "reason-1",
            },
        }

    monkeypatch.setattr(applications_routes, "revert_candidate_disqualification_in_workable", fake_revert)

    reopen_resp = client.patch(
        f"/api/v1/applications/{app['id']}/outcome",
        json={
            "application_outcome": "open",
            "expected_version": 2,
            "reason": "Manual reopen from recruiter review",
            "idempotency_key": "workable-reopen-1",
        },
        headers=headers,
    )
    assert reopen_resp.status_code == 200, reopen_resp.text
    payload = reopen_resp.json()
    assert payload["application_outcome"] == "open"
    assert payload["status"] == "applied"
    assert payload["version"] == 3
    assert captured == {
        "org_id": payload["organization_id"],
        "app_id": app["id"],
        "role_id": role["id"],
    }

    events_resp = client.get(f"/api/v1/applications/{app['id']}/events", headers=headers)
    assert events_resp.status_code == 200, events_resp.text
    event_types = [event["event_type"] for event in events_resp.json()]
    assert "application_outcome_changed" in event_types
    assert "workable_reverted" in event_types


def test_workable_linked_application_reject_failure_preserves_local_state(client, db, monkeypatch):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Workable reject failure role")

    create_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "workable-reject-failure@example.com", "candidate_name": "Reject Failure"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    app = create_resp.json()

    app_row = db.query(CandidateApplication).filter(CandidateApplication.id == app["id"]).first()
    assert app_row is not None
    app_row.source = "workable"
    app_row.workable_candidate_id = "workable-candidate-3"
    db.commit()

    monkeypatch.setattr(
        applications_routes,
        "disqualify_candidate_in_workable",
        lambda **_: {
            "success": False,
            "action": "disqualify",
            "code": "api_error",
            "message": "Workable rejected the disqualification request",
            "config": {},
        },
    )

    reject_resp = client.patch(
        f"/api/v1/applications/{app['id']}/outcome",
        json={
            "application_outcome": "rejected",
            "expected_version": app["version"],
            "reason": "Below rubric bar",
            "idempotency_key": "workable-reject-failure-1",
        },
        headers=headers,
    )
    assert reject_resp.status_code == 502, reject_resp.text
    assert "workable rejected" in reject_resp.json()["detail"].lower()

    detail_resp = client.get(f"/api/v1/applications/{app['id']}", headers=headers)
    assert detail_resp.status_code == 200, detail_resp.text
    payload = detail_resp.json()
    assert payload["application_outcome"] == "open"
    assert payload["version"] == app["version"]

    events_resp = client.get(f"/api/v1/applications/{app['id']}/events", headers=headers)
    assert events_resp.status_code == 200, events_resp.text
    event_types = [event["event_type"] for event in events_resp.json()]
    assert "workable_writeback_failed" in event_types
    assert "application_outcome_changed" not in event_types


def test_manual_application_reject_stays_local_without_workable_writeback(client, monkeypatch):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Manual reject role")

    create_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "manual-reject@example.com", "candidate_name": "Manual Reject"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    app = create_resp.json()

    def should_not_write_back(**_):
        raise AssertionError("Manual application reject should not call Workable write-back")

    monkeypatch.setattr(applications_routes, "disqualify_candidate_in_workable", should_not_write_back)

    reject_resp = client.patch(
        f"/api/v1/applications/{app['id']}/outcome",
        json={
            "application_outcome": "rejected",
            "expected_version": app["version"],
            "reason": "Manual recruiter reject",
            "idempotency_key": "manual-reject-1",
        },
        headers=headers,
    )
    assert reject_resp.status_code == 200, reject_resp.text
    payload = reject_resp.json()
    assert payload["application_outcome"] == "rejected"
    assert payload["status"] == "rejected"

    events_resp = client.get(f"/api/v1/applications/{app['id']}/events", headers=headers)
    assert events_resp.status_code == 200, events_resp.text
    event_types = [event["event_type"] for event in events_resp.json()]
    assert "application_outcome_changed" in event_types
    assert "workable_disqualified" not in event_types
    assert "workable_writeback_failed" not in event_types


def test_role_pipeline_counts_exclude_closed_outcomes(client):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Pipeline stats role")

    app_open = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "open-candidate@example.com"},
        headers=headers,
    ).json()
    app_closed = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "closed-candidate@example.com"},
        headers=headers,
    ).json()

    move_resp = client.patch(
        f"/api/v1/applications/{app_open['id']}/stage",
        json={"pipeline_stage": "invited", "expected_version": app_open["version"]},
        headers=headers,
    )
    assert move_resp.status_code == 200, move_resp.text

    close_resp = client.patch(
        f"/api/v1/applications/{app_closed['id']}/outcome",
        json={"application_outcome": "rejected", "expected_version": app_closed["version"]},
        headers=headers,
    )
    assert close_resp.status_code == 200, close_resp.text

    pipeline_resp = client.get(f"/api/v1/roles/{role['id']}/pipeline", headers=headers)
    assert pipeline_resp.status_code == 200, pipeline_resp.text
    pipeline_payload = pipeline_resp.json()
    assert pipeline_payload["stage_counts"]["invited"] == 1
    assert pipeline_payload["active_candidates_count"] == 1
    assert all(item["application_outcome"] == "open" for item in pipeline_payload["items"])
    assert all(item["id"] != app_closed["id"] for item in pipeline_payload["items"])

    roles_resp = client.get("/api/v1/roles?include_pipeline_stats=true", headers=headers)
    assert roles_resp.status_code == 200, roles_resp.text
    role_payload = next((item for item in roles_resp.json() if item["id"] == role["id"]), None)
    assert role_payload is not None
    assert role_payload["stage_counts"]["invited"] == 1
    assert role_payload["active_candidates_count"] == 1
    assert role_payload["last_candidate_activity_at"] is not None


def test_global_applications_endpoint_supports_pipeline_filters(client):
    headers, _ = auth_headers(client)
    role_one = _create_role_with_spec(client, headers, name="Global list role one")
    role_two = _create_role_with_spec(client, headers, name="Global list role two")

    app_invited = client.post(
        f"/api/v1/roles/{role_one['id']}/applications",
        json={"candidate_email": "invited-global@example.com"},
        headers=headers,
    ).json()
    app_applied = client.post(
        f"/api/v1/roles/{role_one['id']}/applications",
        json={"candidate_email": "applied-global@example.com"},
        headers=headers,
    ).json()
    client.post(
        f"/api/v1/roles/{role_two['id']}/applications",
        json={"candidate_email": "other-role-global@example.com"},
        headers=headers,
    )

    move_resp = client.patch(
        f"/api/v1/applications/{app_invited['id']}/stage",
        json={"pipeline_stage": "invited", "expected_version": app_invited["version"]},
        headers=headers,
    )
    assert move_resp.status_code == 200, move_resp.text

    global_resp = client.get(
        "/api/v1/applications?pipeline_stage=invited&application_outcome=open&limit=50",
        headers=headers,
    )
    assert global_resp.status_code == 200, global_resp.text
    payload = global_resp.json()
    ids = {item["id"] for item in payload["items"]}
    assert app_invited["id"] in ids
    assert app_applied["id"] not in ids
    assert set(payload.get("stage_counts", {}).keys()) == {"all", "applied", "invited", "in_assessment", "review"}
    assert payload["stage_counts"]["applied"] >= 1
    assert payload["stage_counts"]["invited"] >= 1
    assert all(item.get("assessment_history") == [] for item in payload["items"])
    assert all(item.get("assessment_preview") is None for item in payload["items"])

    scoped_resp = client.get(
        f"/api/v1/applications?role_id={role_one['id']}&application_outcome=open&limit=50",
        headers=headers,
    )
    assert scoped_resp.status_code == 200, scoped_resp.text
    scoped_ids = {item["id"] for item in scoped_resp.json()["items"]}
    assert app_invited["id"] in scoped_ids
    assert app_applied["id"] in scoped_ids


def test_global_applications_can_skip_stage_counts(client):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Global stage counts toggle role")
    create_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "stage-counts-toggle@example.com"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text

    resp = client.get(
        f"/api/v1/applications?role_id={role['id']}&include_stage_counts=false&limit=50",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "stage_counts" not in payload
    assert payload["total"] == 1


def test_global_applications_support_multi_value_filters(client):
    headers, _ = auth_headers(client)
    role_one = _create_role_with_spec(client, headers, name="Global multi filter role one")
    role_two = _create_role_with_spec(client, headers, name="Global multi filter role two")

    app_invited = client.post(
        f"/api/v1/roles/{role_one['id']}/applications",
        json={"candidate_email": "multi-invited@example.com"},
        headers=headers,
    ).json()
    app_rejected = client.post(
        f"/api/v1/roles/{role_two['id']}/applications",
        json={"candidate_email": "multi-rejected@example.com"},
        headers=headers,
    ).json()
    app_applied = client.post(
        f"/api/v1/roles/{role_two['id']}/applications",
        json={"candidate_email": "multi-applied@example.com"},
        headers=headers,
    ).json()

    invited_resp = client.patch(
        f"/api/v1/applications/{app_invited['id']}/stage",
        json={"pipeline_stage": "invited", "expected_version": app_invited["version"]},
        headers=headers,
    )
    assert invited_resp.status_code == 200, invited_resp.text

    rejected_resp = client.patch(
        f"/api/v1/applications/{app_rejected['id']}/outcome",
        json={"application_outcome": "rejected", "expected_version": app_rejected["version"]},
        headers=headers,
    )
    assert rejected_resp.status_code == 200, rejected_resp.text
    rejected_payload = rejected_resp.json()

    excluded_resp = client.patch(
        f"/api/v1/applications/{app_applied['id']}/outcome",
        json={"application_outcome": "hired", "expected_version": app_applied["version"]},
        headers=headers,
    )
    assert excluded_resp.status_code == 200, excluded_resp.text

    global_resp = client.get(
        (
            f"/api/v1/applications?role_ids={role_one['id']},{role_two['id']}"
            "&pipeline_stages=invited,review,applied&application_outcomes=open,rejected&limit=50"
        ),
        headers=headers,
    )
    assert global_resp.status_code == 200, global_resp.text
    payload = global_resp.json()
    ids = {item["id"] for item in payload["items"]}
    assert app_invited["id"] in ids
    assert app_rejected["id"] in ids
    assert app_applied["id"] not in ids
    assert payload["stage_counts"]["invited"] >= 1
    assert payload["stage_counts"][rejected_payload["pipeline_stage"]] >= 1


def test_global_and_role_pipeline_source_filters_support_workable_only(client, db):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Source filter role")

    workable_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "wk-source@example.com", "candidate_name": "Workable Source"},
        headers=headers,
    )
    assert workable_resp.status_code == 201, workable_resp.text
    workable_app = workable_resp.json()

    manual_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "manual-source@example.com", "candidate_name": "Manual Source"},
        headers=headers,
    )
    assert manual_resp.status_code == 201, manual_resp.text
    manual_app = manual_resp.json()

    workable_row = db.query(CandidateApplication).filter(CandidateApplication.id == workable_app["id"]).first()
    manual_row = db.query(CandidateApplication).filter(CandidateApplication.id == manual_app["id"]).first()
    assert workable_row is not None
    assert manual_row is not None

    workable_row.source = "workable"
    workable_row.workable_sourced = True
    workable_row.workable_candidate_id = "wk-source-1"
    manual_row.source = "manual"
    manual_row.workable_sourced = False
    db.commit()

    global_workable = client.get(
        f"/api/v1/applications?role_id={role['id']}&source=workable&application_outcome=open&limit=50",
        headers=headers,
    )
    assert global_workable.status_code == 200, global_workable.text
    workable_items = global_workable.json()["items"]
    assert [item["candidate_email"] for item in workable_items] == ["wk-source@example.com"]

    global_manual = client.get(
        f"/api/v1/applications?role_id={role['id']}&source=manual&application_outcome=open&limit=50",
        headers=headers,
    )
    assert global_manual.status_code == 200, global_manual.text
    manual_items = global_manual.json()["items"]
    assert [item["candidate_email"] for item in manual_items] == ["manual-source@example.com"]

    pipeline_workable = client.get(
        f"/api/v1/roles/{role['id']}/pipeline?source=workable&limit=50",
        headers=headers,
    )
    assert pipeline_workable.status_code == 200, pipeline_workable.text
    pipeline_payload = pipeline_workable.json()
    pipeline_items = pipeline_payload["items"]
    assert [item["candidate_email"] for item in pipeline_items] == ["wk-source@example.com"]
    assert pipeline_payload["stage_counts"]["applied"] >= 1


def test_candidate_report_share_links_are_idempotent_and_member_only(client):
    owner_headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, owner_headers, name="Share link role")

    created = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={
            "candidate_email": "share-link@example.com",
            "candidate_name": "Share Link Candidate",
            "candidate_position": "Platform Engineer",
        },
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    application = created.json()

    share_resp = client.post(
        f"/api/v1/applications/{application['id']}/share-link",
        headers=owner_headers,
    )
    assert share_resp.status_code == 200, share_resp.text
    share_payload = share_resp.json()
    assert share_payload["application_id"] == application["id"]
    assert share_payload["share_token"].startswith("shr_")
    assert share_payload["share_url"].endswith(
        f"/c/{application['id']}?view=interview&k={share_payload['share_token']}"
    )
    assert share_payload["member_access_only"] is False

    share_resp_repeat = client.post(
        f"/api/v1/applications/{application['id']}/share-link",
        headers=owner_headers,
    )
    assert share_resp_repeat.status_code == 200, share_resp_repeat.text
    assert share_resp_repeat.json()["share_token"] == share_payload["share_token"]

    owner_access = client.get(
        f"/api/v1/applications/share/{share_payload['share_token']}",
        headers=owner_headers,
    )
    assert owner_access.status_code == 200, owner_access.text
    owner_payload = owner_access.json()
    assert owner_payload["id"] == application["id"]
    assert owner_payload["candidate_email"] == "share-link@example.com"

    public_access = client.get(
        f"/api/v1/applications/share/{share_payload['share_token']}",
    )
    assert public_access.status_code == 200, public_access.text
    public_payload = public_access.json()
    assert public_payload["id"] == application["id"]
    assert public_payload["candidate_email"] == "share-link@example.com"

    other_headers, _ = auth_headers(client)
    other_access = client.get(
        f"/api/v1/applications/share/{share_payload['share_token']}",
        headers=other_headers,
    )
    assert other_access.status_code == 404


def test_role_pipeline_supports_multi_stage_filter(client):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Role pipeline multi-stage role")

    app_invited = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "pipeline-multi-invited@example.com"},
        headers=headers,
    ).json()
    app_in_assessment = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "pipeline-multi-in-assessment@example.com"},
        headers=headers,
    ).json()
    app_applied = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "pipeline-multi-applied@example.com"},
        headers=headers,
    ).json()

    invited_resp = client.patch(
        f"/api/v1/applications/{app_invited['id']}/stage",
        json={"pipeline_stage": "invited", "expected_version": app_invited["version"]},
        headers=headers,
    )
    assert invited_resp.status_code == 200, invited_resp.text

    in_assessment_resp = client.patch(
        f"/api/v1/applications/{app_in_assessment['id']}",
        json={"status": "in_progress", "expected_version": app_in_assessment["version"]},
        headers=headers,
    )
    assert in_assessment_resp.status_code == 200, in_assessment_resp.text
    assert in_assessment_resp.json()["pipeline_stage"] == "in_assessment"

    pipeline_resp = client.get(
        f"/api/v1/roles/{role['id']}/pipeline?stages=invited,in_assessment&limit=50",
        headers=headers,
    )
    assert pipeline_resp.status_code == 200, pipeline_resp.text
    payload = pipeline_resp.json()
    ids = {item["id"] for item in payload["items"]}
    assert app_invited["id"] in ids
    assert app_in_assessment["id"] in ids
    assert app_applied["id"] not in ids
    assert payload["stage"] == "invited,in_assessment"


def test_global_applications_endpoint_respects_limit_and_offset(client):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Global pagination role")
    for idx in range(65):
        create_resp = client.post(
            f"/api/v1/roles/{role['id']}/applications",
            json={"candidate_email": f"pagination-{idx}@example.com"},
            headers=headers,
        )
        assert create_resp.status_code == 201, create_resp.text

    resp = client.get(
        f"/api/v1/applications?role_id={role['id']}&application_outcome=open&limit=20&offset=20",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["total"] == 65
    assert payload["limit"] == 20
    assert payload["offset"] == 20
    assert len(payload["items"]) == 20


def test_pipeline_endpoints_support_taali_sorting_and_min_filter(client, db):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="TAALI sort role")

    strong = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "taali-strong@example.com", "candidate_name": "Strong Candidate"},
        headers=headers,
    )
    weak = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "taali-weak@example.com", "candidate_name": "Weak Candidate"},
        headers=headers,
    )
    assert strong.status_code == 201, strong.text
    assert weak.status_code == 201, weak.text

    strong_row = db.query(CandidateApplication).filter(CandidateApplication.id == strong.json()["id"]).first()
    weak_row = db.query(CandidateApplication).filter(CandidateApplication.id == weak.json()["id"]).first()
    assert strong_row is not None
    assert weak_row is not None

    strong_row.cv_match_score = 92.0
    strong_row.cv_match_details = {"score_scale": "0-100", "requirements_match_score_100": 88.0}
    strong_row.role_fit_score_cache_100 = 90.0
    strong_row.taali_score_cache_100 = 90.0
    strong_row.score_mode_cache = "role_fit_only"
    weak_row.cv_match_score = 54.0
    weak_row.cv_match_details = {"score_scale": "0-100", "requirements_match_score_100": 46.0}
    weak_row.role_fit_score_cache_100 = 50.0
    weak_row.taali_score_cache_100 = 50.0
    weak_row.score_mode_cache = "role_fit_only"
    db.commit()

    global_sorted = client.get(
        f"/api/v1/applications?role_id={role['id']}&sort_by=taali_score&sort_order=desc&limit=50",
        headers=headers,
    )
    assert global_sorted.status_code == 200, global_sorted.text
    global_items = global_sorted.json()["items"]
    assert len(global_items) == 2
    assert global_items[0]["candidate_email"] == "taali-strong@example.com"
    assert global_items[0]["taali_score"] >= global_items[1]["taali_score"]

    global_filtered = client.get(
        f"/api/v1/applications?role_id={role['id']}&sort_by=taali_score&sort_order=desc&min_taali_score=80&limit=50",
        headers=headers,
    )
    assert global_filtered.status_code == 200, global_filtered.text
    filtered_items = global_filtered.json()["items"]
    assert len(filtered_items) == 1
    assert filtered_items[0]["candidate_email"] == "taali-strong@example.com"

    pipeline_sorted = client.get(
        f"/api/v1/roles/{role['id']}/pipeline?sort_by=taali_score&sort_order=asc&limit=50",
        headers=headers,
    )
    assert pipeline_sorted.status_code == 200, pipeline_sorted.text
    pipeline_items = pipeline_sorted.json()["items"]
    assert len(pipeline_items) == 2
    assert pipeline_items[0]["candidate_email"] == "taali-weak@example.com"
    assert pipeline_items[0]["taali_score"] <= pipeline_items[1]["taali_score"]


def test_pipeline_endpoints_validate_min_taali_threshold_range(client):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="TAALI min threshold validation role")

    global_invalid = client.get(
        f"/api/v1/applications?role_id={role['id']}&min_taali_score=101",
        headers=headers,
    )
    assert global_invalid.status_code == 422, global_invalid.text

    pipeline_invalid = client.get(
        f"/api/v1/roles/{role['id']}/pipeline?min_taali_score=-1",
        headers=headers,
    )
    assert pipeline_invalid.status_code == 422, pipeline_invalid.text


def test_assessment_invite_event_is_logged_when_stage_is_unchanged(client):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, name="Timeline invite task").json()
    role = _create_role_with_spec(client, headers, name="Timeline invite role")

    link_resp = client.post(
        f"/api/v1/roles/{role['id']}/tasks",
        json={"task_id": task["id"]},
        headers=headers,
    )
    assert link_resp.status_code == 200, link_resp.text

    create_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "timeline-invite@example.com", "candidate_name": "Timeline Invite"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    app = create_resp.json()

    move_resp = client.patch(
        f"/api/v1/applications/{app['id']}/stage",
        json={"pipeline_stage": "invited", "expected_version": app["version"]},
        headers=headers,
    )
    assert move_resp.status_code == 200, move_resp.text

    invite_resp = client.post(
        f"/api/v1/applications/{app['id']}/assessments",
        json={"task_id": task["id"], "duration_minutes": 30},
        headers=headers,
    )
    assert invite_resp.status_code == 201, invite_resp.text

    events_resp = client.get(f"/api/v1/applications/{app['id']}/events", headers=headers)
    assert events_resp.status_code == 200, events_resp.text
    event_types = [event["event_type"] for event in events_resp.json()]
    assert "assessment_invite_sent" in event_types


def test_legacy_status_patch_uses_guarded_transition_path(client):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Legacy status compat role")

    create_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "legacy-status@example.com", "candidate_name": "Legacy Status"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    app = create_resp.json()
    assert app["pipeline_stage"] == "applied"
    assert app["version"] == 1

    promote_resp = client.patch(
        f"/api/v1/applications/{app['id']}",
        json={"status": "completed", "expected_version": 1},
        headers=headers,
    )
    assert promote_resp.status_code == 200, promote_resp.text
    promoted = promote_resp.json()
    assert promoted["pipeline_stage"] == "review"
    assert promoted["application_outcome"] == "open"
    assert promoted["status"] == "review"
    assert promoted["version"] == 4

    close_resp = client.patch(
        f"/api/v1/applications/{app['id']}",
        json={"status": "rejected", "expected_version": promoted["version"]},
        headers=headers,
    )
    assert close_resp.status_code == 200, close_resp.text
    closed = close_resp.json()
    assert closed["pipeline_stage"] == "review"
    assert closed["application_outcome"] == "rejected"
    assert closed["status"] == "rejected"
    assert closed["version"] == 5

    events_resp = client.get(f"/api/v1/applications/{app['id']}/events", headers=headers)
    assert events_resp.status_code == 200, events_resp.text
    stage_change_events = [event for event in events_resp.json() if event["event_type"] == "pipeline_stage_changed"]
    assert len(stage_change_events) >= 3


def test_legacy_status_patch_rejects_unreachable_stage_path(client):
    headers, _ = auth_headers(client)
    role = _create_role_with_spec(client, headers, name="Legacy path rejection role")

    create_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "legacy-unreachable@example.com"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    app = create_resp.json()

    move_resp = client.patch(
        f"/api/v1/applications/{app['id']}/stage",
        json={"pipeline_stage": "invited", "expected_version": app["version"]},
        headers=headers,
    )
    assert move_resp.status_code == 200, move_resp.text
    invited = move_resp.json()

    move_review_resp = client.patch(
        f"/api/v1/applications/{app['id']}/stage",
        json={"pipeline_stage": "review", "expected_version": invited["version"]},
        headers=headers,
    )
    assert move_review_resp.status_code == 409, move_review_resp.text

    legacy_reach_resp = client.patch(
        f"/api/v1/applications/{app['id']}",
        json={"status": "applied", "expected_version": invited["version"]},
        headers=headers,
    )
    assert legacy_reach_resp.status_code == 409, legacy_reach_resp.text
    assert "cannot reach stage" in legacy_reach_resp.json()["detail"].lower()
