"""API tests for role-first recruiting workflow endpoints."""

import io

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


def test_reject_assessment_creation_without_application_cv(client):
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
    assert resp.status_code == 400
    assert "cv" in resp.json()["detail"].lower()
