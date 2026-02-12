from tests.conftest import verify_user


def test_register(client):
    response = client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
        "organization_name": "Test Org",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["full_name"] == "Test User"
    assert data["is_email_verified"] is False
    assert "id" in data

def test_register_duplicate(client):
    client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
    })
    response = client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
    })
    assert response.status_code == 400

def test_login_unverified_blocked(client):
    """Unverified user should receive 403 on login."""
    client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
    })
    response = client.post("/api/v1/auth/jwt/login", data={
        "username": "test@example.com",
        "password": "testpass123",
    })
    assert response.status_code == 403
    assert "verify" in response.json()["detail"].lower()

def test_login(client):
    # Register first
    client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
    })
    verify_user("test@example.com")
    # Login
    response = client.post("/api/v1/auth/jwt/login", data={
        "username": "test@example.com",
        "password": "testpass123",
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

def test_login_wrong_password(client):
    client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
    })
    verify_user("test@example.com")
    response = client.post("/api/v1/auth/jwt/login", data={
        "username": "test@example.com",
        "password": "wrongpassword",
    })
    assert response.status_code == 401

def test_verify_email(client):
    """Verify-email endpoint should mark user as verified."""
    resp = client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
    })
    assert resp.status_code == 201
    # Grab the verification token from the DB
    from tests.conftest import TestingSessionLocal
    from app.models.user import User
    db = TestingSessionLocal()
    user = db.query(User).filter(User.email == "test@example.com").first()
    token = user.email_verification_token
    db.close()
    assert token is not None

    # Verify
    vr = client.get(f"/api/v1/auth/verify-email?token={token}")
    assert vr.status_code == 200
    assert "verified" in vr.json()["detail"].lower()

    # Now login should work
    lr = client.post("/api/v1/auth/jwt/login", data={
        "username": "test@example.com",
        "password": "testpass123",
    })
    assert lr.status_code == 200
    assert "access_token" in lr.json()

def test_resend_verification(client):
    """Resend verification endpoint should always return 200."""
    client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
    })
    resp = client.post("/api/v1/auth/resend-verification", json={"email": "test@example.com"})
    assert resp.status_code == 200

    # Nonexistent email should also return 200 (no enumeration)
    resp2 = client.post("/api/v1/auth/resend-verification", json={"email": "nonexistent@example.com"})
    assert resp2.status_code == 200

def test_me(client):
    # Register
    client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
    })
    verify_user("test@example.com")
    # Login
    login_resp = client.post("/api/v1/auth/jwt/login", data={
        "username": "test@example.com",
        "password": "testpass123",
    })
    token = login_resp.json()["access_token"]
    
    # Get me
    response = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "test@example.com"
    assert response.json()["is_email_verified"] is True

def test_me_no_auth(client):
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["healthy", "degraded"]
    assert "database" in data
    assert "redis" in data
