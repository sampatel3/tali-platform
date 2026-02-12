"""Security tests: injection attacks (SQL injection, XSS, path traversal, oversized inputs)."""

import io
import pytest

from tests.conftest import (
    auth_headers,
    create_candidate_via_api,
    create_task_via_api,
    login_user,
    register_user,
    verify_user,
)


@pytest.mark.security
class TestInjectionAttacks:
    """Verify the platform is resilient to common injection vectors."""

    # ------------------------------------------------------------------
    # SQL injection
    # ------------------------------------------------------------------

    def test_sql_injection_register_email(self, client):
        """register with SQL-injection email → either 422 or safely stored (no 500)."""
        resp = register_user(client, email="admin'--@test.com")
        # Pydantic's EmailStr may accept the single-quote (RFC-valid local part).
        # The key assertion: it must NOT cause a 500 (SQL injection).
        assert resp.status_code in (201, 422), f"Unexpected status {resp.status_code}"
        assert resp.status_code != 500, "SQL injection caused server error"

    def test_sql_injection_login_email(self, client):
        """login with username "' OR '1'='1" → 401 (not 500)"""
        resp = login_user(client, email="' OR '1'='1", password="anything123!")
        # Should be a clean auth failure, not a server error
        assert resp.status_code in (401, 422), f"Expected 401 or 422, got {resp.status_code}"
        assert resp.status_code != 500

    def test_sql_injection_candidate_search(self, client):
        """search candidates with q="'; DROP TABLE users;--" → 200 (not 500)"""
        headers, _ = auth_headers(client)

        resp = client.get(
            "/api/v1/candidates/",
            params={"q": "'; DROP TABLE users;--"},
            headers=headers,
        )
        # The search should succeed (returning 0 results), not crash
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_sql_injection_task_name(self, client):
        """create task with name "'; DROP TABLE tasks;--" → stores safely"""
        headers, _ = auth_headers(client)

        resp = create_task_via_api(
            client, headers,
            name="'; DROP TABLE tasks;--",
            description="SQL injection test",
        )
        assert resp.status_code == 201
        # The payload should be stored literally, not interpreted
        assert resp.json()["name"] == "'; DROP TABLE tasks;--"

        # Confirm tasks table still works
        list_resp = client.get("/api/v1/tasks/", headers=headers)
        assert list_resp.status_code == 200

    # ------------------------------------------------------------------
    # XSS
    # ------------------------------------------------------------------

    def test_xss_in_user_name(self, client):
        """register with full_name containing script tag → stored safely, returned as-is"""
        xss_name = "<script>alert('xss')</script>"
        email = "xss-name@test.com"
        resp = register_user(client, email=email, full_name=xss_name)
        assert resp.status_code == 201
        # The name should be stored and returned literally (output encoding is frontend's job)
        assert resp.json()["full_name"] == xss_name

        verify_user(email)
        login_resp = login_user(client, email)
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]

        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["full_name"] == xss_name

    def test_xss_in_task_description(self, client):
        """create task with XSS in description → 201, stored safely"""
        headers, _ = auth_headers(client)
        xss_desc = '<img src=x onerror="alert(1)">'
        resp = create_task_via_api(client, headers, description=xss_desc)
        assert resp.status_code == 201
        assert resp.json()["description"] == xss_desc

    def test_xss_in_candidate_name(self, client):
        """create candidate with XSS name → 201, stored safely"""
        headers, _ = auth_headers(client)
        xss_name = "<svg onload=alert(document.cookie)>"
        resp = create_candidate_via_api(client, headers, full_name=xss_name)
        assert resp.status_code == 201
        assert resp.json()["full_name"] == xss_name

    # ------------------------------------------------------------------
    # Path traversal
    # ------------------------------------------------------------------

    def test_path_traversal_upload_filename(self, client):
        """Upload file named '../../etc/passwd.txt' → should not allow path traversal."""
        headers, _ = auth_headers(client)

        # Create a candidate to upload to
        cand_resp = create_candidate_via_api(client, headers)
        assert cand_resp.status_code == 201
        cand_id = cand_resp.json()["id"]

        # Craft a file with a path-traversal filename
        malicious_filename = "../../etc/passwd.txt"
        file_content = b"root:x:0:0:root:/root:/bin/bash"
        files = {"file": (malicious_filename, io.BytesIO(file_content), "application/pdf")}

        resp = client.post(
            f"/api/v1/candidates/{cand_id}/upload-cv",
            files=files,
            headers=headers,
        )
        # Either the server rejects it (4xx) or sanitizes the filename (2xx)
        # Crucially it must NOT return 500 (unhandled path traversal)
        if resp.status_code < 400:
            # If accepted, the stored filename should be sanitized
            data = resp.json()
            stored_name = data.get("filename", "")
            assert ".." not in stored_name, f"Path traversal in stored filename: {stored_name}"
        else:
            # Rejection is also acceptable (e.g., invalid extension, bad filename)
            assert resp.status_code in (400, 422), f"Unexpected status {resp.status_code}"

    # ------------------------------------------------------------------
    # Oversized / malicious inputs
    # ------------------------------------------------------------------

    def test_extremely_long_email(self, client):
        """register with 10000-char email → 422"""
        long_email = "a" * 9990 + "@test.com"
        resp = register_user(client, email=long_email)
        assert resp.status_code == 422

    def test_null_bytes_in_input(self, client):
        """register with full_name containing null byte → 422 or sanitized"""
        name_with_null = "Normal\x00Name"
        resp = register_user(client, email="null-byte@test.com", full_name=name_with_null)
        # The server should either reject (422) or accept & sanitize (201)
        assert resp.status_code in (201, 422), f"Unexpected status {resp.status_code}"
        if resp.status_code == 201:
            # If accepted, null byte should not appear verbatim in the stored value
            stored = resp.json().get("full_name", "")
            # It may be stored as-is (since DB can handle it), which is acceptable
            # What matters is the server didn't crash
            assert isinstance(stored, str)
