"""Comprehensive integration tests for the Auth API (/api/v1/auth/...)."""

import time

from jose import jwt

from tests.conftest import (
    TestingSessionLocal,
    auth_headers,
    login_user,
    register_user,
    verify_user,
)
from app.models.user import User

# The test environment uses the default dev secret key (no .env override).
SECRET_KEY = "dev-secret-key-change-in-production"
ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def _get_user_from_db(email: str) -> User | None:
    db = TestingSessionLocal()
    try:
        return db.query(User).filter(User.email == email).first()
    finally:
        db.close()


# ===== Registration =====


def test_register_success_with_org(client):
    resp = register_user(client, organization_name="Acme Inc")
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"]
    assert data["full_name"] == "Test User"
    assert data["organization_id"] is not None
    assert data["is_active"] is True


def test_register_success_without_org(client):
    resp = register_user(client)
    assert resp.status_code == 201
    data = resp.json()
    assert data["organization_id"] is None
    assert data["is_email_verified"] is False


def test_register_duplicate_email_400(client):
    email = "dup@test.com"
    first = register_user(client, email=email)
    assert first.status_code == 201
    second = register_user(client, email=email)
    assert second.status_code == 400
    assert "already registered" in second.json()["detail"].lower()


def test_register_duplicate_org_slug_reuses_org(client):
    """Two users registering with the same org name should share the org."""
    org_name = "Shared Corp"
    r1 = register_user(client, email="user1@test.com", organization_name=org_name)
    r2 = register_user(client, email="user2@test.com", organization_name=org_name)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["organization_id"] == r2.json()["organization_id"]


def test_register_short_password_422(client):
    resp = client.post("/api/v1/auth/register", json={
        "email": "short@test.com",
        "password": "Ab1!",
        "full_name": "Short Pass",
    })
    assert resp.status_code == 422


def test_register_missing_full_name_422(client):
    resp = client.post("/api/v1/auth/register", json={
        "email": "noname@test.com",
        "password": "TestPass123!",
    })
    assert resp.status_code == 422


def test_register_invalid_email_422(client):
    resp = client.post("/api/v1/auth/register", json={
        "email": "not-an-email",
        "password": "TestPass123!",
        "full_name": "Bad Email",
    })
    assert resp.status_code == 422


def test_register_empty_body_422(client):
    resp = client.post("/api/v1/auth/register", json={})
    assert resp.status_code == 422


def test_register_extra_fields_ignored(client):
    resp = client.post("/api/v1/auth/register", json={
        "email": "extra@test.com",
        "password": "TestPass123!",
        "full_name": "Extra Fields",
        "is_superuser": True,
        "nonexistent_field": "hello",
    })
    assert resp.status_code == 201
    data = resp.json()
    # Extra fields should not leak into the response or take effect
    assert "is_superuser" not in data or data.get("is_superuser") is not True


def test_register_response_has_is_email_verified_false(client):
    resp = register_user(client)
    assert resp.status_code == 201
    assert resp.json()["is_email_verified"] is False


def test_register_response_has_organization_id(client):
    resp = register_user(client, organization_name="OrgTest")
    assert resp.status_code == 201
    assert isinstance(resp.json()["organization_id"], int)


def test_register_long_valid_inputs(client):
    long_name = "A" * 200
    long_org = "B" * 200
    long_pass = "Aa1!" + "x" * 196  # 200 chars
    resp = client.post("/api/v1/auth/register", json={
        "email": "longuser@test.com",
        "password": long_pass,
        "full_name": long_name,
        "organization_name": long_org,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["full_name"] == long_name


def test_register_password_exactly_8_chars(client):
    resp = client.post("/api/v1/auth/register", json={
        "email": "exact8@test.com",
        "password": "Abcdefg1",
        "full_name": "Boundary",
    })
    assert resp.status_code == 201


# ===== Email Verification =====


def test_verify_email_success(client):
    email = "verify@test.com"
    register_user(client, email=email)
    user = _get_user_from_db(email)
    assert user is not None
    token = user.email_verification_token
    assert token is not None

    resp = client.get(f"/api/v1/auth/verify-email?token={token}")
    assert resp.status_code == 200
    assert "verified" in resp.json()["detail"].lower()

    # Confirm the DB flag is updated
    user_after = _get_user_from_db(email)
    assert user_after.is_email_verified is True
    assert user_after.email_verification_token is None


def test_verify_email_invalid_token_400(client):
    # Register a user so at least one record exists
    register_user(client)
    fake_token = "a" * 32  # valid length but wrong value
    resp = client.get(f"/api/v1/auth/verify-email?token={fake_token}")
    assert resp.status_code == 400
    assert "invalid" in resp.json()["detail"].lower()


def test_verify_email_already_used_token_400(client):
    email = "double-verify@test.com"
    register_user(client, email=email)
    user = _get_user_from_db(email)
    token = user.email_verification_token

    # First verification succeeds
    resp1 = client.get(f"/api/v1/auth/verify-email?token={token}")
    assert resp1.status_code == 200

    # Second attempt with same token should fail
    resp2 = client.get(f"/api/v1/auth/verify-email?token={token}")
    assert resp2.status_code == 400


def test_verify_email_short_token_422(client):
    resp = client.get("/api/v1/auth/verify-email?token=short")
    assert resp.status_code == 422


# ===== Login =====


def test_login_success_after_verification(client):
    email = "login@test.com"
    register_user(client, email=email)
    verify_user(email)
    resp = login_user(client, email)
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_unverified_user_403(client):
    email = "unverified@test.com"
    register_user(client, email=email)
    resp = login_user(client, email)
    assert resp.status_code == 403
    assert "verify" in resp.json()["detail"].lower()


def test_login_wrong_password_401(client):
    email = "wrongpw@test.com"
    register_user(client, email=email)
    verify_user(email)
    resp = login_user(client, email, password="WrongPassword999!")
    assert resp.status_code == 401


def test_login_nonexistent_email_401(client):
    resp = login_user(client, "ghost@nowhere.com")
    assert resp.status_code == 401


def test_login_response_has_valid_jwt(client):
    email = "jwtuser@test.com"
    register_user(client, email=email)
    verify_user(email)
    resp = login_user(client, email)
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert "exp" in payload
    assert payload["sub"] == email


def test_login_jwt_contains_correct_claims(client):
    email = "claims@test.com"
    reg = register_user(client, email=email, organization_name="ClaimsOrg")
    assert reg.status_code == 201
    user_id = reg.json()["id"]
    org_id = reg.json()["organization_id"]

    verify_user(email)
    resp = login_user(client, email)
    assert resp.status_code == 200

    token = resp.json()["access_token"]
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == email
    assert payload["user_id"] == user_id
    assert payload["org_id"] == org_id


# ===== Forgot / Reset Password =====


def test_forgot_password_existing_email_200(client):
    email = "forgot@test.com"
    register_user(client, email=email)
    verify_user(email)
    resp = client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200

    # Verify a reset token was stored
    user = _get_user_from_db(email)
    assert user.password_reset_token is not None


def test_forgot_password_nonexistent_email_200(client):
    resp = client.post("/api/v1/auth/forgot-password", json={"email": "nobody@test.com"})
    assert resp.status_code == 200  # prevents email enumeration


def test_reset_password_success(client):
    email = "reset@test.com"
    register_user(client, email=email)
    verify_user(email)
    client.post("/api/v1/auth/forgot-password", json={"email": email})
    user = _get_user_from_db(email)
    reset_token = user.password_reset_token
    assert reset_token is not None

    resp = client.post("/api/v1/auth/reset-password", json={
        "token": reset_token,
        "new_password": "NewSecure456!",
    })
    assert resp.status_code == 200
    assert "reset" in resp.json()["detail"].lower()


def test_reset_password_invalid_token_400(client):
    fake_token = "x" * 32
    resp = client.post("/api/v1/auth/reset-password", json={
        "token": fake_token,
        "new_password": "DoesntMatter1!",
    })
    assert resp.status_code == 400


def test_reset_password_login_with_new_password(client):
    email = "newpw@test.com"
    new_password = "BrandNew789!"
    register_user(client, email=email)
    verify_user(email)
    client.post("/api/v1/auth/forgot-password", json={"email": email})

    user = _get_user_from_db(email)
    reset_token = user.password_reset_token

    client.post("/api/v1/auth/reset-password", json={
        "token": reset_token,
        "new_password": new_password,
    })

    # Login with the new password should succeed
    resp = login_user(client, email, password=new_password)
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_reset_password_old_password_no_longer_works(client):
    email = "oldpw@test.com"
    old_password = "TestPass123!"
    new_password = "Changed999!"
    register_user(client, email=email, password=old_password)
    verify_user(email)

    # Trigger reset flow
    client.post("/api/v1/auth/forgot-password", json={"email": email})
    user = _get_user_from_db(email)
    reset_token = user.password_reset_token
    client.post("/api/v1/auth/reset-password", json={
        "token": reset_token,
        "new_password": new_password,
    })

    # Old password must fail
    resp = login_user(client, email, password=old_password)
    assert resp.status_code == 401


# ===== /me Endpoint =====


def test_me_with_valid_token(client):
    headers, email = auth_headers(client)
    resp = client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == email
    assert data["is_email_verified"] is True
    assert data["is_active"] is True


def test_me_without_token_401(client):
    resp = client.get("/api/v1/users/me")
    assert resp.status_code == 401


def test_me_with_expired_token_401(client):
    # Craft a token that expired 1 hour ago
    payload = {
        "sub": "expired@test.com",
        "user_id": 9999,
        "org_id": None,
        "exp": time.time() - 3600,
    }
    expired_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp.status_code == 401


def test_me_with_malformed_token_401(client):
    resp = client.get("/api/v1/users/me", headers={"Authorization": "Bearer not.a.valid.jwt"})
    assert resp.status_code == 401


# ===== Resend Verification =====


def test_resend_verification_success(client):
    email = "resend@test.com"
    register_user(client, email=email)
    user_before = _get_user_from_db(email)
    original_token = user_before.email_verification_token

    # The resend endpoint has a 60-second rate limit on re-sending.
    # Manually clear the sent_at timestamp to bypass it in tests.
    db = TestingSessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        u.email_verification_sent_at = None
        db.commit()
    finally:
        db.close()

    resp = client.post("/api/v1/auth/resend-verification", json={"email": email})
    assert resp.status_code == 200

    # A new token should have been generated
    user_after = _get_user_from_db(email)
    assert user_after.email_verification_token is not None
    assert user_after.email_verification_token != original_token


def test_resend_verification_nonexistent_email_200(client):
    resp = client.post("/api/v1/auth/resend-verification", json={"email": "nope@test.com"})
    assert resp.status_code == 200  # prevents email enumeration
