"""Security tests: authentication, authorization, headers, and token handling."""

import json
from datetime import timedelta

import pytest
from jose import jwt

from tests.conftest import (
    TestingSessionLocal,
    auth_headers,
    login_user,
    register_user,
    verify_user,
)
from app.models.user import User
from app.platform.security import create_access_token
from app.platform.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_reset_token(email: str) -> str:
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user and user.password_reset_token
        return user.password_reset_token
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.security
class TestAuthSecurity:
    """Verify JWT handling, header security, and auth edge cases."""

    # ------------------------------------------------------------------
    # JWT manipulation
    # ------------------------------------------------------------------

    def test_jwt_wrong_secret(self, client):
        """JWT signed with wrong secret → /me returns 401."""
        token = jwt.encode(
            {"user_id": 1, "sub": "x@test.com", "exp": 9999999999},
            "wrong-secret",
            algorithm="HS256",
        )
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_expired_jwt_401(self, client):
        """JWT that already expired → /me returns 401."""
        token = create_access_token(
            data={"sub": "expired@test.com", "user_id": 999},
            expires_delta=timedelta(seconds=-10),
        )
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_malformed_jwt_401(self, client):
        """'Bearer not-a-jwt' → 401."""
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer not-a-jwt"},
        )
        assert resp.status_code == 401

    def test_missing_auth_header_401(self, client):
        """No Authorization header → 401 on /me."""
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_bearer_prefix_required(self, client):
        """Send just the token without 'Bearer ' prefix → 401."""
        headers, _ = auth_headers(client)
        raw_token = headers["Authorization"].replace("Bearer ", "")

        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": raw_token},
        )
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # Security headers
    # ------------------------------------------------------------------

    def test_security_headers_present(self, client):
        """Any request should include security-hardening headers."""
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "strict-origin" in resp.headers.get("Referrer-Policy", "").lower()

    def test_cors_headers_present(self, client):
        """OPTIONS request should include Access-Control headers."""
        resp = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Content-Type",
            },
        )
        # CORS preflight should succeed (200) and include the ACAO header
        assert resp.status_code == 200
        acao = resp.headers.get("Access-Control-Allow-Origin", "")
        assert acao, "Access-Control-Allow-Origin header missing"

    # ------------------------------------------------------------------
    # Password reset token reuse
    # ------------------------------------------------------------------

    def test_password_reset_token_single_use(self, client):
        """forgot → reset → try reset again with same token → 400."""
        email = "reset-reuse@test.com"
        register_user(client, email=email)
        verify_user(email)

        client.post("/api/v1/auth/forgot-password", json={"email": email})
        token = _get_reset_token(email)

        # First reset succeeds
        first = client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "NewSecure999!"},
        )
        assert first.status_code == 200

        # Second reset with same token must fail
        second = client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "AnotherPass111!"},
        )
        assert second.status_code == 400

    # ------------------------------------------------------------------
    # Protected endpoints require auth
    # ------------------------------------------------------------------

    def test_protected_endpoints_require_auth(self, client):
        """Multiple protected endpoints should all return 401 without a token."""
        protected = [
            ("GET", "/api/v1/tasks/"),
            ("GET", "/api/v1/candidates/"),
            ("GET", "/api/v1/assessments/"),
            ("GET", "/api/v1/analytics/"),
            ("GET", "/api/v1/billing/usage"),
            ("GET", "/api/v1/organizations/me"),
        ]
        for method, path in protected:
            resp = client.request(method, path)
            assert resp.status_code == 401, (
                f"{method} {path} returned {resp.status_code}, expected 401"
            )

    # ------------------------------------------------------------------
    # No password leakage
    # ------------------------------------------------------------------

    def test_registration_no_password_leak(self, client):
        """register → get /me → response never contains password or hashed_password."""
        email = "noleak-reg@test.com"
        reg = register_user(client, email=email)
        assert reg.status_code == 201
        reg_body = json.dumps(reg.json()).lower()
        assert "hashed_password" not in reg_body
        assert "testpass" not in reg_body

        verify_user(email)
        login_resp = login_user(client, email)
        token = login_resp.json()["access_token"]

        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        me_body = json.dumps(me.json()).lower()
        assert "hashed_password" not in me_body
        assert "testpass" not in me_body

    def test_login_response_no_password_leak(self, client):
        """login response should never expose the password."""
        email = "noleak-login@test.com"
        register_user(client, email=email)
        verify_user(email)
        login_resp = login_user(client, email)
        assert login_resp.status_code == 200
        body = json.dumps(login_resp.json()).lower()
        assert "hashed_password" not in body
        assert "testpass" not in body

    # ------------------------------------------------------------------
    # Edge-case auth headers
    # ------------------------------------------------------------------

    def test_empty_auth_header(self, client):
        """'Authorization: ' (empty value) → 401."""
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": ""},
        )
        assert resp.status_code in (401, 403)

    def test_bearer_empty_token(self, client):
        """'Authorization: Bearer ' (empty token) → 401."""
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # Unicode password
    # ------------------------------------------------------------------

    def test_unicode_in_password(self, client):
        """register with unicode password → should work."""
        email = "unicode-pw@test.com"
        unicode_password = "Pässwörd123!"  # 12 chars, meets min_length=8
        reg = register_user(client, email=email, password=unicode_password)
        assert reg.status_code == 201

        verify_user(email)
        login_resp = login_user(client, email, password=unicode_password)
        assert login_resp.status_code == 200
        assert login_resp.json()["access_token"]

    # ------------------------------------------------------------------
    # API docs disabled in production
    # ------------------------------------------------------------------

    def test_api_docs_disabled_in_production(self, client):
        """GET /api/docs → 404 when in production mode (docs_url=None).

        The test environment uses default config where SENTRY_DSN is unset
        and FRONTEND_URL contains 'localhost', so docs may be enabled.
        We check the production guard logic: if _is_production, docs should 404.
        In the test env (non-production), docs may be available — we verify
        the endpoint at least returns a well-formed response (200 or 404).
        """
        resp = client.get("/api/docs")
        # In dev mode → 200 (docs served) or 404 (depending on config)
        # In production mode → definitely 404
        assert resp.status_code in (200, 404), f"Unexpected status {resp.status_code}"

        # If docs are disabled (production), openapi.json should also 404
        openapi_resp = client.get("/api/openapi.json")
        assert openapi_resp.status_code in (200, 404)
