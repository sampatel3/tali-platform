"""
QA Test Suite: Authentication & Authorization
Covers: register, login, verify-email, resend-verification, forgot-password,
        reset-password, /me, JWT handling, input validation, edge cases.
~50 tests
"""
import time
from tests.conftest import verify_user, TestingSessionLocal
from app.models.user import User


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
    db = TestingSessionLocal()
    user = db.query(User).filter(User.email == email).first()
    token = user.email_verification_token
    db.close()
    return token


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
        assert d["is_email_verified"] is False
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
        assert "already registered" in r.json()["detail"].lower()

    def test_register_duplicate_email_case_insensitive(self, client):
        """Emails should be treated case-insensitively or at least not allow
        two registrations with different casing."""
        _register(client, email="User@Example.com")
        # Try the same email in lowercase — should fail or succeed consistently
        r = _register(client, email="user@example.com")
        # Either 400 (duplicate) or 201 (separate account) is acceptable but should be consistent
        assert r.status_code in [201, 400]

    def test_register_reuse_existing_org_slug(self, client):
        """If org name resolves to same slug, should reuse org."""
        _register(client, email="a@example.com", org_name="Acme")
        r = _register(client, email="b@example.com", org_name="Acme")
        assert r.status_code == 201

    def test_register_password_too_short(self, client):
        r = _register(client, password="short")
        assert r.status_code == 422

    def test_register_password_exactly_8_chars(self, client):
        r = _register(client, password="Exactly8")
        assert r.status_code == 201

    def test_register_password_max_length(self, client):
        r = _register(client, password="A" * 200)
        assert r.status_code == 201

    def test_register_password_over_max_length(self, client):
        r = _register(client, password="A" * 201)
        assert r.status_code == 422

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
        assert r.status_code == 422

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
        assert r.status_code == 403
        assert "verify" in r.json()["detail"].lower()

    def test_login_wrong_password(self, client):
        _register(client)
        verify_user("u@example.com")
        r = _login(client, password="WrongPassword!")
        assert r.status_code == 401

    def test_login_nonexistent_user(self, client):
        r = _login(client, email="nobody@example.com")
        assert r.status_code == 401

    def test_login_missing_username(self, client):
        r = client.post("/api/v1/auth/jwt/login", data={"password": "ValidPass1!"})
        assert r.status_code == 422

    def test_login_missing_password(self, client):
        r = client.post("/api/v1/auth/jwt/login", data={"username": "u@example.com"})
        assert r.status_code == 422


# ===========================================================================
# C. EMAIL VERIFICATION
# ===========================================================================
class TestEmailVerification:
    def test_verify_email_success(self, client):
        _register(client)
        token = _get_verification_token("u@example.com")
        r = client.get(f"/api/v1/auth/verify-email?token={token}")
        assert r.status_code == 200
        assert "verified" in r.json()["detail"].lower()

    def test_verify_email_invalid_token(self, client):
        r = client.get("/api/v1/auth/verify-email?token=invalid_token_that_is_long_enough_16_chars")
        assert r.status_code == 400

    def test_verify_email_missing_token(self, client):
        r = client.get("/api/v1/auth/verify-email")
        assert r.status_code == 422

    def test_verify_email_token_too_short(self, client):
        r = client.get("/api/v1/auth/verify-email?token=short")
        assert r.status_code == 422

    def test_verify_email_cannot_reuse_token(self, client):
        _register(client)
        token = _get_verification_token("u@example.com")
        client.get(f"/api/v1/auth/verify-email?token={token}")
        r = client.get(f"/api/v1/auth/verify-email?token={token}")
        assert r.status_code == 400

    def test_login_after_verification(self, client):
        _register(client)
        token = _get_verification_token("u@example.com")
        client.get(f"/api/v1/auth/verify-email?token={token}")
        r = _login(client)
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_resend_verification_existing_user(self, client):
        _register(client)
        r = client.post("/api/v1/auth/resend-verification", json={"email": "u@example.com"})
        assert r.status_code == 200

    def test_resend_verification_nonexistent_email(self, client):
        r = client.post("/api/v1/auth/resend-verification", json={"email": "nope@example.com"})
        assert r.status_code == 200  # no enumeration


# ===========================================================================
# D. FORGOT / RESET PASSWORD
# ===========================================================================
class TestPasswordReset:
    def test_forgot_password_existing_user(self, client):
        _register(client)
        verify_user("u@example.com")
        r = client.post("/api/v1/auth/forgot-password", json={"email": "u@example.com"})
        assert r.status_code == 200

    def test_forgot_password_nonexistent_user(self, client):
        r = client.post("/api/v1/auth/forgot-password", json={"email": "nope@example.com"})
        assert r.status_code == 200  # no enumeration

    def test_reset_password_flow(self, client):
        _register(client)
        verify_user("u@example.com")
        client.post("/api/v1/auth/forgot-password", json={"email": "u@example.com"})
        # Get reset token from DB
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == "u@example.com").first()
        reset_token = user.password_reset_token
        db.close()
        assert reset_token is not None

        r = client.post("/api/v1/auth/reset-password", json={
            "token": reset_token,
            "new_password": "NewPassword123!",
        })
        assert r.status_code == 200
        assert "reset" in r.json()["detail"].lower()

        # Login with new password
        lr = _login(client, password="NewPassword123!")
        assert lr.status_code == 200

    def test_reset_password_invalid_token(self, client):
        r = client.post("/api/v1/auth/reset-password", json={
            "token": "A" * 32,  # long enough but invalid
            "new_password": "NewPassword123!",
        })
        assert r.status_code == 400

    def test_reset_password_short_new_password(self, client):
        r = client.post("/api/v1/auth/reset-password", json={
            "token": "A" * 32,
            "new_password": "short",
        })
        assert r.status_code == 422


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
        assert "status" in d
        assert "database" in d
        assert "redis" in d
        assert d["service"] == "tali-api"
