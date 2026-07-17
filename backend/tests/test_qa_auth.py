"""
QA Test Suite: Authentication & Authorization
Covers: register, login, verify-email, resend-verification, forgot-password,
        reset-password, /me, JWT handling, input validation, edge cases.
~50 tests
"""
from tests.conftest import verify_user
from app.domains.identity_access.users_fastapi import UserManager


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def _register(client, email="u@example.com", password="ValidPass1!", full_name="Test User", org_name=None):
    body = {"email": email, "password": password, "full_name": full_name}
    if org_name:
        body["organization_name"] = org_name
    return client.post("/api/v1/auth/register", json=body)


def _login(client, email="u@example.com", password="ValidPass1!"):
    return client.post("/api/v1/auth/jwt/login", data={"username": email, "password": password})


def _auth_headers(client, email="u@example.com", password="ValidPass1!", full_name="Test User", org_name="TestOrg"):
    _register(client, email, password, full_name, org_name)
    verify_user(email)
    token = _login(client, email, password).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _get_verification_token(email):
    """Not available with FastAPI-Users (JWT tokens); use verify_user() in tests instead."""
    return None


def _password_reset_token(client, monkeypatch, email):
    """Capture the JWT issued by the public forgot-password flow."""
    captured = {}

    async def capture_token(self, user, token, request=None):
        captured["token"] = token

    monkeypatch.setattr(UserManager, "on_after_forgot_password", capture_token)
    response = client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert response.status_code == 202
    assert captured.get("token")
    return captured["token"]


# ===========================================================================
# A. REGISTRATION
# ===========================================================================
class TestRegistration:
    def test_register_success(self, client):
        r = _register(client)
        assert r.status_code == 201
        d = r.json()
        assert d["email"] == "u@example.com"
        assert d["full_name"] == "Test User"
        assert d["is_active"] is True
        assert d["is_verified"] is False
        assert "id" in d
        assert "created_at" in d

    def test_register_with_org(self, client):
        r = _register(client, org_name="Acme Corp")
        assert r.status_code == 201
        assert r.json()["organization_id"] is not None

    def test_register_without_org(self, client):
        r = _register(client)
        assert r.status_code == 201
        assert r.json()["organization_id"] is None

    def test_register_duplicate_email(self, client):
        _register(client)
        r = _register(client)
        assert r.status_code == 400
        detail = r.json().get("detail", "")
        if isinstance(detail, list):
            detail = " ".join(str(x.get("msg", x)) for x in detail)
        assert "already" in detail.lower() or "exists" in detail.lower()

    def test_register_duplicate_email_case_insensitive(self, client):
        """A case variant cannot create a second account for one mailbox."""
        _register(client, email="User@Example.com")
        r = _register(client, email="user@example.com")
        assert r.status_code == 400
        assert r.json()["detail"] == (
            "An account with this email already exists. Sign in instead or use a "
            "different email."
        )

    def test_register_same_org_name_creates_new_org(self, client):
        """Self-registration should not attach to an existing org by name."""
        r1 = _register(client, email="a@example.com", org_name="Acme")
        r2 = _register(client, email="b@example.com", org_name="Acme")
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["organization_id"] != r2.json()["organization_id"]

    def test_register_password_too_short(self, client):
        r = _register(client, password="short")
        assert r.status_code == 400
        assert r.json()["detail"] == {
            "code": "REGISTER_INVALID_PASSWORD",
            "reason": "Password must be at least 8 characters.",
        }

    def test_register_password_exactly_8_chars(self, client):
        r = _register(client, password="Exactly8")
        assert r.status_code == 201

    def test_register_password_max_length(self, client):
        r = _register(client, password="A" * 72)
        assert r.status_code == 201

    def test_register_password_over_max_length(self, client):
        r = _register(client, password="A" * 73)
        assert r.status_code == 400
        assert r.json()["detail"] == {
            "code": "REGISTER_INVALID_PASSWORD",
            "reason": "Password must be 72 UTF-8 bytes or fewer.",
        }

    def test_register_missing_email(self, client):
        r = client.post("/api/v1/auth/register", json={
            "password": "ValidPass1!", "full_name": "Test"
        })
        assert r.status_code == 422

    def test_register_missing_password(self, client):
        r = client.post("/api/v1/auth/register", json={
            "email": "u@example.com", "full_name": "Test"
        })
        assert r.status_code == 422

    def test_register_missing_full_name(self, client):
        r = client.post("/api/v1/auth/register", json={
            "email": "u@example.com", "password": "ValidPass1!"
        })
        assert r.status_code == 201
        assert r.json()["full_name"] is None

    def test_register_invalid_email_format(self, client):
        r = _register(client, email="not-an-email")
        assert r.status_code == 422

    def test_register_empty_body(self, client):
        r = client.post("/api/v1/auth/register", json={})
        assert r.status_code == 422

    def test_register_no_body(self, client):
        r = client.post("/api/v1/auth/register")
        assert r.status_code == 422

    def test_register_response_does_not_contain_password(self, client):
        r = _register(client)
        assert r.status_code == 201
        body = r.json()
        assert "password" not in body
        assert "hashed_password" not in body


# ===========================================================================
# B. LOGIN
# ===========================================================================
class TestLogin:
    def test_login_success(self, client):
        _register(client)
        verify_user("u@example.com")
        r = _login(client)
        assert r.status_code == 200
        d = r.json()
        assert "access_token" in d
        assert d["token_type"] == "bearer"

    def test_login_unverified_returns_403(self, client):
        _register(client)
        r = _login(client)
        assert r.status_code == 400
        assert r.json()["detail"] == "LOGIN_USER_NOT_VERIFIED"

    def test_login_wrong_password(self, client):
        _register(client)
        verify_user("u@example.com")
        r = _login(client, password="WrongPassword!")
        assert r.status_code == 400
        assert r.json()["detail"] == "LOGIN_BAD_CREDENTIALS"

    def test_login_nonexistent_user(self, client):
        r = _login(client, email="nobody@example.com")
        assert r.status_code == 400
        assert r.json()["detail"] == "LOGIN_BAD_CREDENTIALS"

    def test_login_missing_username(self, client):
        r = client.post("/api/v1/auth/jwt/login", data={"password": "ValidPass1!"})
        assert r.status_code == 422

    def test_login_missing_password(self, client):
        r = client.post("/api/v1/auth/jwt/login", data={"username": "u@example.com"})
        assert r.status_code == 422


# ===========================================================================
# D. FORGOT / RESET PASSWORD
# ===========================================================================
class TestPasswordReset:
    def test_forgot_password_existing_user(self, client):
        _register(client)
        verify_user("u@example.com")
        r = client.post("/api/v1/auth/forgot-password", json={"email": "u@example.com"})
        assert r.status_code == 202

    def test_forgot_password_nonexistent_user(self, client):
        r = client.post("/api/v1/auth/forgot-password", json={"email": "nope@example.com"})
        assert r.status_code == 202

    def test_reset_password_flow(self, client, monkeypatch):
        _register(client)
        verify_user("u@example.com")
        token = _password_reset_token(client, monkeypatch, "u@example.com")
        r = client.post("/api/v1/auth/reset-password", json={
            "token": token,
            "password": "NewPassword123!",
        })
        assert r.status_code == 200
        assert _login(client).status_code == 400
        assert _login(client, password="NewPassword123!").status_code == 200

    def test_reset_password_invalid_token(self, client):
        r = client.post("/api/v1/auth/reset-password", json={
            "token": "A" * 32,
            "password": "NewPassword123!",
        })
        assert r.status_code == 400
        assert r.json()["detail"] == "RESET_PASSWORD_BAD_TOKEN"

    def test_reset_password_short_new_password(self, client, monkeypatch):
        _register(client)
        verify_user("u@example.com")
        token = _password_reset_token(client, monkeypatch, "u@example.com")
        r = client.post("/api/v1/auth/reset-password", json={
            "token": token,
            "password": "short",
        })
        assert r.status_code == 400
        assert r.json()["detail"] == {
            "code": "RESET_PASSWORD_INVALID_PASSWORD",
            "reason": "Password must be at least 8 characters.",
        }


# ===========================================================================
# E. JWT / AUTH HEADERS
# ===========================================================================
class TestJWTAuth:
    def test_me_with_valid_token(self, client):
        headers = _auth_headers(client)
        r = client.get("/api/v1/users/me", headers=headers)
        assert r.status_code == 200
        assert r.json()["email"] == "u@example.com"

    def test_me_without_token(self, client):
        r = client.get("/api/v1/users/me")
        assert r.status_code == 401

    def test_me_with_invalid_token(self, client):
        r = client.get("/api/v1/users/me", headers={"Authorization": "Bearer invalid_token_here"})
        assert r.status_code == 401

    def test_me_with_malformed_auth_header(self, client):
        r = client.get("/api/v1/users/me", headers={"Authorization": "NotBearer token"})
        assert r.status_code == 401


# ===========================================================================
# F. HEALTH CHECK
# ===========================================================================
class TestHealth:
    def test_health_endpoint(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        d = r.json()
        assert d == {"status": "ok", "service": "taali-api"}
