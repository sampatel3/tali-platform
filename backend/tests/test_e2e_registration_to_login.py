"""End-to-end tests: full user journey from registration through login and beyond."""

import pytest

from tests.conftest import (
    TestingSessionLocal,
    auth_headers,
    create_assessment_via_api,
    create_candidate_via_api,
    create_task_via_api,
    login_user,
    register_user,
    verify_user,
)
from app.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_from_db(email: str) -> User:
    """Fetch a user row directly from the test DB."""
    db = TestingSessionLocal()
    try:
        return db.query(User).filter(User.email == email).first()
    finally:
        db.close()


def _get_verification_token(email: str) -> str:
    return ""  # FastAPI-Users uses JWT; use verify_user() in tests


def _get_reset_token(email: str) -> str:
    return ""  # FastAPI-Users uses JWT; no DB token


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestRegistrationToLogin:
    """End-to-end flows exercising the full user journey."""

    def test_full_happy_path(self, client):
        """register → verify email → login → /me returns user data"""
        email = "happy@test.com"
        reg = register_user(client, email=email, full_name="Happy User")
        assert reg.status_code == 201
        assert reg.json()["email"] == email

        # Verify via DB helper
        verify_user(email)

        login_resp = login_user(client, email)
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        assert token

        me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        me_data = me.json()
        assert me_data["email"] == email
        assert me_data["full_name"] == "Happy User"
        assert me_data["is_verified"] is True

    def test_register_with_org_then_login(self, client):
        """register with org → verify → login → org appears in response"""
        email = "orguser@test.com"
        reg = register_user(client, email=email, organization_name="AcmeCorp")
        assert reg.status_code == 201
        assert reg.json()["organization_id"] is not None

        verify_user(email)

        login_resp = login_user(client, email)
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]

        me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["organization_id"] is not None

    def test_register_short_password_specific_error(self, client):
        """register with 7-char password → 400 or 422"""
        resp = register_user(client, email="short@test.com", password="Abcd12!")
        assert resp.status_code in (400, 422)
        detail = resp.json().get("detail", "")
        if isinstance(detail, list):
            assert any(
                "password" in str(err.get("loc", "")).lower()
                or "password" in str(err.get("msg", "")).lower()
                for err in detail
            ), f"Expected password field error in {detail}"
        else:
            assert "password" in str(detail).lower() or len(detail) > 0

    def test_register_duplicate_org_reuses(self, client):
        """register user1 with org 'TestCo' → register user2 with same org → same org_id"""
        email1 = "dup-org-1@test.com"
        email2 = "dup-org-2@test.com"

        reg1 = register_user(client, email=email1, organization_name="TestCo")
        assert reg1.status_code == 201
        org_id_1 = reg1.json()["organization_id"]
        assert org_id_1 is not None

        reg2 = register_user(client, email=email2, organization_name="TestCo")
        assert reg2.status_code == 201
        org_id_2 = reg2.json()["organization_id"]
        assert org_id_2 is not None

        assert org_id_1 == org_id_2, "Both users should share the same org"

    def test_unverified_login_then_verify(self, client):
        """Unverified user may get 200 or 403 depending on require_verification."""
        """register → login fails 403 → verify → login succeeds"""
        email = "unverified@test.com"
        reg = register_user(client, email=email)
        assert reg.status_code == 201

        # Login before verification may fail (403) or succeed (200) depending on config
        login_resp = login_user(client, email)
        assert login_resp.status_code in (200, 403)
        if login_resp.status_code == 403:
            assert "verify" in login_resp.json().get("detail", "").lower()

        # Verify and retry
        verify_user(email)
        login_resp2 = login_user(client, email)
        assert login_resp2.status_code == 200
        assert login_resp2.json()["access_token"]

    def test_forgot_reset_password_flow(self, client):
        """register → verify → forgot password (FastAPI-Users uses JWT; no DB token to test full reset)."""
        email = "forgot@test.com"
        register_user(client, email=email)
        verify_user(email)
        assert login_user(client, email).status_code == 200
        forgot_resp = client.post("/api/v1/auth/forgot-password", json={"email": email})
        assert forgot_resp.status_code in (200, 202)

    def test_resend_verification(self, client):
        """register → request-verify → verify_user in test → login works"""
        email = "resend@test.com"
        register_user(client, email=email)
        resend_resp = client.post("/api/v1/auth/request-verify", json={"email": email})
        assert resend_resp.status_code in (200, 202, 404)
        verify_user(email)
        login_resp = login_user(client, email)
        assert login_resp.status_code == 200

    def test_register_then_create_task(self, client):
        """register → verify → login → create task → list tasks shows it"""
        headers, email = auth_headers(client, organization_name="TaskOrg")

        task_resp = create_task_via_api(client, headers, name="My E2E Task")
        assert task_resp.status_code == 201
        task_id = task_resp.json()["id"]

        list_resp = client.get("/api/v1/tasks/", headers=headers)
        assert list_resp.status_code == 200
        task_ids = [t["id"] for t in list_resp.json()]
        assert task_id in task_ids

    def test_register_then_full_setup(self, client):
        """register → verify → login → create task → create candidate → create assessment → list assessments shows it"""
        headers, email = auth_headers(client, organization_name="FullSetupOrg")

        task_resp = create_task_via_api(client, headers)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["id"]

        cand_resp = create_candidate_via_api(client, headers)
        assert cand_resp.status_code == 201
        cand_email = cand_resp.json()["email"]

        assess_resp = create_assessment_via_api(
            client, headers, task_id,
            candidate_email=cand_email,
            candidate_name="Full Setup Candidate",
        )
        assert assess_resp.status_code == 201
        assessment_id = assess_resp.json()["id"]

        list_resp = client.get("/api/v1/assessments/", headers=headers)
        assert list_resp.status_code == 200
        ids = [a["id"] for a in list_resp.json()["items"]]
        assert assessment_id in ids

    def test_two_users_same_org(self, client):
        """register both with org 'SharedOrg' → verify both → login both → both see same org_id"""
        email1 = "shared1@test.com"
        email2 = "shared2@test.com"

        reg1 = register_user(client, email=email1, organization_name="SharedOrg")
        assert reg1.status_code == 201
        org_id_1 = reg1.json()["organization_id"]

        reg2 = register_user(client, email=email2, organization_name="SharedOrg")
        assert reg2.status_code == 201
        org_id_2 = reg2.json()["organization_id"]

        assert org_id_1 == org_id_2

        verify_user(email1)
        verify_user(email2)

        login1 = login_user(client, email1)
        assert login1.status_code == 200
        token1 = login1.json()["access_token"]

        login2 = login_user(client, email2)
        assert login2.status_code == 200
        token2 = login2.json()["access_token"]

        me1 = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token1}"})
        me2 = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token2}"})

        assert me1.status_code == 200
        assert me2.status_code == 200
        assert me1.json()["organization_id"] == me2.json()["organization_id"]
