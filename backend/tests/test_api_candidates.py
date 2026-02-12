"""API tests for candidate CRUD + file-upload endpoints (/api/v1/candidates/)."""

import io
import uuid

from tests.conftest import auth_headers, create_candidate_via_api


# ---------------------------------------------------------------------------
# POST /api/v1/candidates/ — Create
# ---------------------------------------------------------------------------


def test_create_candidate_success(client):
    headers, _ = auth_headers(client)
    resp = create_candidate_via_api(
        client,
        headers,
        email="alice@example.com",
        full_name="Alice Smith",
        position="Backend Dev",
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "alice@example.com"
    assert data["full_name"] == "Alice Smith"
    assert "id" in data


def test_create_candidate_email_only(client):
    headers, _ = auth_headers(client)
    resp = client.post(
        "/api/v1/candidates/",
        json={"email": "minimal@example.com"},
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "minimal@example.com"


def test_create_candidate_all_fields(client):
    headers, _ = auth_headers(client)
    resp = create_candidate_via_api(
        client,
        headers,
        email="full@example.com",
        full_name="Full Fields",
        position="Senior Engineer",
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "full@example.com"
    assert data["full_name"] == "Full Fields"
    assert data["position"] == "Senior Engineer"


def test_create_candidate_invalid_email_422(client):
    headers, _ = auth_headers(client)
    resp = client.post(
        "/api/v1/candidates/",
        json={"email": "not-an-email"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_create_candidate_missing_email_422(client):
    headers, _ = auth_headers(client)
    resp = client.post(
        "/api/v1/candidates/",
        json={"full_name": "No Email"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_create_candidate_no_auth_401(client):
    resp = client.post(
        "/api/v1/candidates/",
        json={"email": "noauth@example.com"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/candidates/ — List
# ---------------------------------------------------------------------------


def test_list_candidates_empty(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/candidates/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    assert len(items) == 0


def test_list_candidates_with_candidates(client):
    headers, _ = auth_headers(client)
    create_candidate_via_api(client, headers, email="one@example.com")
    create_candidate_via_api(client, headers, email="two@example.com")
    resp = client.get("/api/v1/candidates/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    assert len(items) >= 2


def test_list_candidates_search(client):
    headers, _ = auth_headers(client)
    create_candidate_via_api(client, headers, email="searchable@example.com", full_name="Searchable Person")
    create_candidate_via_api(client, headers, email="other@example.com", full_name="Other Person")
    resp = client.get("/api/v1/candidates/?q=searchable", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    assert len(items) >= 1
    # Every returned item should relate to the search term
    for item in items:
        combined = (item.get("email", "") + item.get("full_name", "")).lower()
        assert "searchable" in combined


def test_list_candidates_pagination(client):
    headers, _ = auth_headers(client)
    for i in range(5):
        create_candidate_via_api(client, headers, email=f"page{i}@example.com")
    resp = client.get("/api/v1/candidates/?limit=2&offset=0", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    assert len(items) <= 2


def test_list_candidates_no_auth_401(client):
    resp = client.get("/api/v1/candidates/")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/candidates/{id} — Get single
# ---------------------------------------------------------------------------


def test_get_candidate_success(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers, email="fetch@example.com").json()
    resp = client.get(f"/api/v1/candidates/{cand['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "fetch@example.com"


def test_get_candidate_not_found_404(client):
    headers, _ = auth_headers(client)
    fake_id = 99999
    resp = client.get(f"/api/v1/candidates/{fake_id}", headers=headers)
    assert resp.status_code == 404


def test_get_candidate_no_auth_401(client):
    resp = client.get(f"/api/v1/candidates/99999")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /api/v1/candidates/{id} — Update
# ---------------------------------------------------------------------------


def test_update_candidate_name(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers).json()
    resp = client.patch(
        f"/api/v1/candidates/{cand['id']}",
        json={"full_name": "Updated Name"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Updated Name"


def test_update_candidate_position(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers).json()
    resp = client.patch(
        f"/api/v1/candidates/{cand['id']}",
        json={"position": "Staff Engineer"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["position"] == "Staff Engineer"


def test_update_candidate_not_found_404(client):
    headers, _ = auth_headers(client)
    fake_id = 99999
    resp = client.patch(
        f"/api/v1/candidates/{fake_id}",
        json={"full_name": "Ghost"},
        headers=headers,
    )
    assert resp.status_code == 404


def test_update_candidate_no_auth_401(client):
    resp = client.patch(
        f"/api/v1/candidates/99999",
        json={"full_name": "No Auth"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/v1/candidates/{id} — Delete
# ---------------------------------------------------------------------------


def test_delete_candidate_success(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers).json()
    resp = client.delete(f"/api/v1/candidates/{cand['id']}", headers=headers)
    assert resp.status_code in (200, 204)


def test_delete_candidate_not_found_404(client):
    headers, _ = auth_headers(client)
    fake_id = 99999
    resp = client.delete(f"/api/v1/candidates/{fake_id}", headers=headers)
    assert resp.status_code == 404


def test_delete_candidate_no_auth_401(client):
    resp = client.delete(f"/api/v1/candidates/99999")
    assert resp.status_code == 401


def test_delete_candidate_then_get_404(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers).json()
    del_resp = client.delete(f"/api/v1/candidates/{cand['id']}", headers=headers)
    assert del_resp.status_code in (200, 204)
    get_resp = client.get(f"/api/v1/candidates/{cand['id']}", headers=headers)
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/candidates/{id}/upload-cv — CV upload
# ---------------------------------------------------------------------------


def test_upload_cv_pdf_file(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers).json()
    # Minimal valid-ish PDF content (extraction will return empty but upload succeeds)
    pdf_header = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    files = {"file": ("resume.pdf", io.BytesIO(pdf_header), "application/pdf")}
    auth_only = {"Authorization": headers["Authorization"]}
    resp = client.post(
        f"/api/v1/candidates/{cand['id']}/upload-cv",
        files=files,
        headers=auth_only,
    )
    assert resp.status_code in (200, 201)


def test_upload_cv_invalid_extension(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers).json()
    files = {"file": ("malware.exe", io.BytesIO(b"MZ...fake binary"), "application/octet-stream")}
    resp = client.post(
        f"/api/v1/candidates/{cand['id']}/upload-cv",
        files=files,
        headers=headers,
    )
    assert resp.status_code in (400, 422)


def test_upload_cv_no_auth_401(client):
    files = {"file": ("resume.txt", io.BytesIO(b"content"), "text/plain")}
    resp = client.post(
        f"/api/v1/candidates/99999/upload-cv",
        files=files,
    )
    assert resp.status_code == 401


def test_upload_cv_missing_file(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers).json()
    resp = client.post(
        f"/api/v1/candidates/{cand['id']}/upload-cv",
        headers=headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/candidates/{id}/upload-job-spec — Job spec upload
# ---------------------------------------------------------------------------


def test_upload_job_spec_txt_file(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers).json()
    files = {"file": ("spec.txt", io.BytesIO(b"Job spec content for extraction"), "text/plain")}
    auth_only = {"Authorization": headers["Authorization"]}
    resp = client.post(
        f"/api/v1/candidates/{cand['id']}/upload-job-spec",
        files=files,
        headers=auth_only,
    )
    assert resp.status_code in (200, 201)


def test_upload_job_spec_invalid_extension(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers).json()
    files = {"file": ("spec.exe", io.BytesIO(b"bad content"), "application/octet-stream")}
    resp = client.post(
        f"/api/v1/candidates/{cand['id']}/upload-job-spec",
        files=files,
        headers=headers,
    )
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_create_candidate_long_name(client):
    headers, _ = auth_headers(client)
    long_name = "A" * 201
    resp = create_candidate_via_api(client, headers, full_name=long_name)
    assert resp.status_code == 422


def test_list_candidates_search_no_results(client):
    headers, _ = auth_headers(client)
    create_candidate_via_api(client, headers, email="exists@example.com", full_name="Existing")
    resp = client.get("/api/v1/candidates/?q=zzzznonexistent", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    assert len(items) == 0
