"""API tests for role-first recruiting workflow endpoints."""

import io

from app.domains.assessments_runtime import applications_routes
from app.domains.assessments_runtime import roles_management_routes
from app.models.candidate_application import CandidateApplication
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
            "cv_job_match_score": 8.4,
            "skills_match": 8.0,
            "experience_relevance": 8.8,
            "match_details": {"summary": "Strong API and SQL alignment."},
        },
    )

    cv_file = {"file": ("resume.pdf", io.BytesIO(b"%PDF-1.4 python sql backend api"), "application/pdf")}
    upload_resp = client.post(f"/api/v1/applications/{app['id']}/upload-cv", files=cv_file, headers=headers)
    assert upload_resp.status_code == 200, upload_resp.text

    list_resp = client.get(f"/api/v1/roles/{role['id']}/applications", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    apps = list_resp.json()
    assert len(apps) == 1
    assert apps[0]["cv_match_score"] == 8.4
    assert apps[0]["cv_match_details"]["summary"] == "Strong API and SQL alignment."
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
