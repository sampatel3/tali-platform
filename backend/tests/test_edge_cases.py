"""Edge case tests — Unicode, boundary values, malformed requests, concurrent behavior."""
import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import io
import pytest
from tests.conftest import auth_headers, create_task_via_api, create_candidate_via_api, create_assessment_via_api


# ===================================================================
# UNICODE HANDLING
# ===================================================================

class TestUnicode:
    def test_unicode_in_user_name(self, client):
        headers, _ = auth_headers(client, full_name="日本語テスト", organization_name="TestOrg")
        resp = client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 200
        assert "日本語テスト" in resp.json()["full_name"]

    def test_unicode_in_task_name(self, client):
        headers, _ = auth_headers(client)
        resp = create_task_via_api(client, headers, name="Tâche de développement")
        assert resp.status_code == 201
        assert "Tâche" in resp.json()["name"]

    def test_unicode_in_candidate_name(self, client):
        headers, _ = auth_headers(client)
        resp = create_candidate_via_api(client, headers, full_name="André François-Müller")
        assert resp.status_code == 201
        assert "André" in resp.json()["full_name"]

    def test_emoji_in_task_description(self, client):
        headers, _ = auth_headers(client)
        resp = create_task_via_api(client, headers, description="Build a sorting algorithm 🚀✨")
        assert resp.status_code == 201


# ===================================================================
# EMPTY STRING VS NULL
# ===================================================================

class TestEmptyVsNull:
    def test_empty_org_name_on_register(self, client):
        from tests.conftest import register_user
        resp = register_user(client, organization_name="")
        # Empty string for optional org — should either create with empty or treat as None
        assert resp.status_code in (201, 422)

    def test_none_position_on_candidate(self, client):
        headers, _ = auth_headers(client)
        resp = client.post("/api/v1/candidates/", json={"email": "test@example.com"}, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data.get("position") is None


# ===================================================================
# BOUNDARY VALUES
# ===================================================================

class TestBoundaryValues:
    def test_max_length_full_name(self, client):
        from tests.conftest import register_user
        name = "A" * 200
        resp = register_user(client, full_name=name)
        assert resp.status_code == 201
        assert resp.json()["full_name"] == name

    def test_max_length_password(self, client):
        from tests.conftest import register_user, verify_user, login_user
        password = "P" * 200
        email = "maxpass@test.com"
        resp = register_user(client, email=email, password=password)
        assert resp.status_code == 201
        verify_user(email)
        login_resp = login_user(client, email, password)
        assert login_resp.status_code == 200

    def test_max_length_task_description(self, client):
        headers, _ = auth_headers(client)
        desc = "D" * 5000
        resp = create_task_via_api(client, headers, description=desc)
        assert resp.status_code == 201

    def test_over_max_task_description(self, client):
        headers, _ = auth_headers(client)
        desc = "D" * 5001
        resp = create_task_via_api(client, headers, description=desc)
        assert resp.status_code == 422


# ===================================================================
# MALFORMED REQUESTS
# ===================================================================

class TestMalformedRequests:
    def test_malformed_json_body(self, client):
        headers, _ = auth_headers(client)
        resp = client.post(
            "/api/v1/tasks/",
            content=b"{not valid json}",
            headers={**headers, "Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_wrong_content_type(self, client):
        headers, _ = auth_headers(client)
        # Send form-urlencoded data to a JSON endpoint — should fail
        resp = client.post(
            "/api/v1/tasks/",
            data={"name": "test"},
            headers={"Authorization": headers["Authorization"]},
        )
        assert resp.status_code == 422

    def test_extra_unexpected_fields_ignored(self, client):
        headers, _ = auth_headers(client)
        payload = {
            "name": "Test Task",
            "description": "A test task",
            "task_type": "python",
            "difficulty": "medium",
            "starter_code": "# start",
            "test_code": "# test",
            "nonexistent_field": "should be ignored",
            "another_fake": 42,
        }
        resp = client.post("/api/v1/tasks/", json=payload, headers=headers)
        assert resp.status_code == 201


# ===================================================================
# DOUBLE OPERATIONS
# ===================================================================

class TestDoubleOperations:
    def test_delete_then_recreate_same_email(self, client):
        headers, _ = auth_headers(client)
        email = "reuse@test.com"
        # Create
        resp1 = create_candidate_via_api(client, headers, email=email)
        assert resp1.status_code == 201
        cand_id = resp1.json()["id"]
        # Delete
        del_resp = client.delete(f"/api/v1/candidates/{cand_id}", headers=headers)
        assert del_resp.status_code in (200, 204)
        # Recreate with same email
        resp2 = create_candidate_via_api(client, headers, email=email)
        assert resp2.status_code == 201

    def test_delete_already_deleted(self, client):
        headers, _ = auth_headers(client)
        resp = create_task_via_api(client, headers)
        assert resp.status_code == 201
        task_id = resp.json()["id"]
        # Delete once
        client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
        # Delete again
        resp2 = client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
        assert resp2.status_code == 404

    def test_get_after_delete(self, client):
        headers, _ = auth_headers(client)
        resp = create_candidate_via_api(client, headers)
        assert resp.status_code == 201
        cand_id = resp.json()["id"]
        client.delete(f"/api/v1/candidates/{cand_id}", headers=headers)
        get_resp = client.get(f"/api/v1/candidates/{cand_id}", headers=headers)
        assert get_resp.status_code == 404


# ===================================================================
# FILE UPLOADS
# ===================================================================

class TestFileUploadEdgeCases:
    def test_empty_file_upload(self, client):
        headers, _ = auth_headers(client)
        resp = create_candidate_via_api(client, headers)
        cand_id = resp.json()["id"]
        files = {"file": ("empty.txt", io.BytesIO(b""), "text/plain")}
        upload_resp = client.post(f"/api/v1/candidates/{cand_id}/upload-cv", files=files, headers=headers)
        assert upload_resp.status_code == 400  # Empty file rejected

    def test_file_no_extension(self, client):
        headers, _ = auth_headers(client)
        resp = create_candidate_via_api(client, headers)
        cand_id = resp.json()["id"]
        files = {"file": ("noext", io.BytesIO(b"some content"), "application/octet-stream")}
        upload_resp = client.post(f"/api/v1/candidates/{cand_id}/upload-cv", files=files, headers=headers)
        assert upload_resp.status_code == 400  # No valid extension


# ===================================================================
# NONEXISTENT RESOURCES
# ===================================================================

class TestNonexistentResources:
    def test_get_nonexistent_task(self, client):
        headers, _ = auth_headers(client)
        resp = client.get("/api/v1/tasks/99999", headers=headers)
        assert resp.status_code == 404

    def test_get_nonexistent_candidate(self, client):
        headers, _ = auth_headers(client)
        resp = client.get("/api/v1/candidates/99999", headers=headers)
        assert resp.status_code == 404

    def test_get_nonexistent_assessment(self, client):
        headers, _ = auth_headers(client)
        resp = client.get("/api/v1/assessments/99999", headers=headers)
        assert resp.status_code == 404

    def test_update_nonexistent_task(self, client):
        headers, _ = auth_headers(client)
        resp = client.patch("/api/v1/tasks/99999", json={"name": "Updated"}, headers=headers)
        assert resp.status_code == 404
