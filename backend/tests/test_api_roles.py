"""API tests for role-first recruiting workflow endpoints."""

import io
from datetime import datetime, timezone

from PyPDF2 import PdfReader

from app.domains.assessments_runtime import applications_routes
from app.domains.assessments_runtime import roles_management_routes
from app.models.assessment import Assessment, AssessmentStatus
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
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "Platform Engineer"}, headers=headers).json()
    job_spec_file = {"file": ("job-spec.txt", io.BytesIO(b"Platform engineering role requirements"), "text/plain")}
    assert client.post(f"/api/v1/roles/{role['id']}/upload-job-spec", files=job_spec_file, headers=headers).status_code == 200
    app = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": "pre-assessment@example.com", "candidate_name": "Pre Assessment"},
        headers=headers,
    ).json()

    role_row = db.query(Role).filter(Role.id == role["id"]).first()
    app_row = db.query(CandidateApplication).filter(CandidateApplication.id == app["id"]).first()
    assert role_row is not None
    assert app_row is not None
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
    db.commit()

    debrief_resp = client.post(
        f"/api/v1/applications/{app['id']}/interview-debrief",
        json={},
        headers=headers,
    )
    assert debrief_resp.status_code == 200, debrief_resp.text
    debrief_payload = debrief_resp.json()
    assert debrief_payload["cached"] is False
    assert "No completed assessment exists yet" in debrief_payload["interview_debrief"]["summary"]
    assert len(debrief_payload["interview_debrief"]["probing_questions"]) >= 1

    report_resp = client.get(f"/api/v1/applications/{app['id']}/report.pdf", headers=headers)
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

    scoped_resp = client.get(
        f"/api/v1/applications?role_id={role_one['id']}&application_outcome=open&limit=50",
        headers=headers,
    )
    assert scoped_resp.status_code == 200, scoped_resp.text
    scoped_ids = {item["id"] for item in scoped_resp.json()["items"]}
    assert app_invited["id"] in scoped_ids
    assert app_applied["id"] in scoped_ids


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
    weak_row.cv_match_score = 54.0
    weak_row.cv_match_details = {"score_scale": "0-100", "requirements_match_score_100": 46.0}
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


def test_applications_preflight_allows_tracing_headers(client):
    response = client.options(
        "/api/v1/applications",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization,Content-Type,Baggage,Sentry-Trace,Traceparent,Tracestate",
        },
    )
    assert response.status_code == 200
    allowed_headers = str(response.headers.get("access-control-allow-headers", "")).lower()
    for expected in ("baggage", "sentry-trace", "traceparent", "tracestate"):
        assert expected in allowed_headers


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
