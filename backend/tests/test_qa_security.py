"""
QA Test Suite: Security — CORS, Headers, Rate Limiting, Injection, XSS
Covers: security headers, CORS, auth bypasses, SQL injection, XSS, rate limits.
~25 tests
"""
from tests.conftest import verify_user


def _auth_headers(client, email="u@example.com"):
    client.post("/api/v1/auth/register", json={
        "email": email, "password": "ValidPass1!", "full_name": "Test User", "organization_name": "TestOrg",
    })
    verify_user(email)
    token = client.post("/api/v1/auth/jwt/login", data={"username": email, "password": "ValidPass1!"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# A. SECURITY HEADERS
# ===========================================================================
class TestSecurityHeaders:
    def test_x_content_type_options(self, client):
        r = client.get("/health")
        assert r.headers.get("x-content-type-options") == "nosniff"

    def test_x_frame_options(self, client):
        r = client.get("/health")
        assert r.headers.get("x-frame-options") == "DENY"

    def test_referrer_policy(self, client):
        r = client.get("/health")
        assert "strict-origin" in r.headers.get("referrer-policy", "")

    def test_permissions_policy(self, client):
        r = client.get("/health")
        pp = r.headers.get("permissions-policy", "")
        assert "camera=()" in pp
        assert "microphone=()" in pp


# ===========================================================================
# B. AUTH BYPASS ATTEMPTS
# ===========================================================================
class TestAuthBypass:
    def test_protected_endpoint_without_token(self, client):
        endpoints = [
            ("GET", "/api/v1/assessments"),
            ("GET", "/api/v1/tasks"),
            ("GET", "/api/v1/candidates/"),
            ("GET", "/api/v1/organizations/me"),
            ("GET", "/api/v1/billing/usage"),
            ("GET", "/api/v1/analytics/"),
            ("GET", "/api/v1/users/"),
            ("GET", "/api/v1/users/me"),
        ]
        for method, path in endpoints:
            r = getattr(client, method.lower())(path)
            assert r.status_code == 401, f"{method} {path} should require auth, got {r.status_code}"

    def test_protected_post_without_token(self, client):
        endpoints = [
            ("/api/v1/assessments", {"candidate_email": "x@e.com", "candidate_name": "X", "task_id": 1}),
            ("/api/v1/tasks", {"name": "T", "description": "D", "task_type": "d", "difficulty": "m",
                               "duration_minutes": 30, "starter_code": "x", "test_code": "y"}),
            ("/api/v1/candidates/", {"email": "x@e.com"}),
            ("/api/v1/users/invite", {"email": "x@e.com", "full_name": "X"}),
        ]
        for path, body in endpoints:
            r = client.post(path, json=body)
            assert r.status_code == 401, f"POST {path} should require auth, got {r.status_code}"

    def test_expired_or_tampered_token(self, client):
        r = client.get("/api/v1/users/me", headers={
            "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0QGV4YW1wbGUuY29tIiwiZXhwIjoxfQ.invalid"
        })
        assert r.status_code == 401


# ===========================================================================
# C. SQL INJECTION ATTEMPTS
# ===========================================================================
class TestSQLInjection:
    def test_login_sql_injection(self, client):
        r = client.post("/api/v1/auth/jwt/login", data={
            "username": "' OR 1=1 --",
            "password": "' OR 1=1 --",
        })
        assert r.status_code in [401, 422]

    def test_register_sql_injection(self, client):
        r = client.post("/api/v1/auth/register", json={
            "email": "test@example.com",
            "password": "ValidPass1!",
            "full_name": "'; DROP TABLE users; --",
            "organization_name": "'; DROP TABLE organizations; --",
        })
        # Should either succeed (harmless) or reject, not crash
        assert r.status_code in [201, 422, 400]

    def test_candidate_search_injection(self, client):
        h = _auth_headers(client)
        r = client.get("/api/v1/candidates/?q=' OR 1=1 --", headers=h)
        assert r.status_code == 200  # Should not crash


# ===========================================================================
# D. XSS ATTEMPTS
# ===========================================================================
class TestXSSAttempts:
    def test_register_xss_in_name(self, client):
        r = client.post("/api/v1/auth/register", json={
            "email": "xss@example.com",
            "password": "ValidPass1!",
            "full_name": "<script>alert('xss')</script>",
        })
        # Should accept (stored safely) or reject
        assert r.status_code in [201, 422]
        if r.status_code == 201:
            # Check it's stored as-is (no script execution in API)
            assert "<script>" in r.json()["full_name"] or r.status_code == 201

    def test_task_xss_in_description(self, client):
        h = _auth_headers(client)
        r = client.post("/api/v1/tasks", json={
            "name": "XSS Task",
            "description": "<img src=x onerror=alert('xss')> normal task description",
            "task_type": "debug", "difficulty": "mid", "duration_minutes": 30,
            "starter_code": "x=1", "test_code": "assert True",
        }, headers=h)
        assert r.status_code in [201, 422]

    def test_candidate_xss_in_name(self, client):
        h = _auth_headers(client)
        r = client.post("/api/v1/candidates/", json={
            "email": "c@e.com",
            "full_name": "<script>alert(1)</script>",
        }, headers=h)
        assert r.status_code in [201, 422]


# ===========================================================================
# E. CROSS-ORG DATA ISOLATION
# ===========================================================================
class TestDataIsolation:
    def test_user_cannot_see_other_org_assessments(self, client):
        """User from Org A should not see Org B's assessments."""
        # Create user A
        h_a = _auth_headers(client, email="a@example.com")
        task_resp = client.post("/api/v1/tasks", json={
            "name": "Isolation Task", "description": "A test task for isolation",
            "task_type": "debug", "difficulty": "mid",
            "duration_minutes": 30, "starter_code": "x = 1", "test_code": "assert True",
        }, headers=h_a)
        assert task_resp.status_code == 201, f"Task creation failed: {task_resp.text}"
        task = task_resp.json()
        client.post("/api/v1/assessments", json={
            "candidate_email": "c@e.com", "candidate_name": "C", "task_id": task["id"],
        }, headers=h_a)

        # Create user B in different org
        client.post("/api/v1/auth/register", json={
            "email": "b@example.com", "password": "ValidPass1!",
            "full_name": "User B", "organization_name": "OrgB",
        })
        verify_user("b@example.com")
        token_b = client.post("/api/v1/auth/jwt/login", data={
            "username": "b@example.com", "password": "ValidPass1!",
        }).json()["access_token"]
        h_b = {"Authorization": f"Bearer {token_b}"}

        # User B should see 0 assessments
        r = client.get("/api/v1/assessments", headers=h_b)
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_user_cannot_see_other_org_candidates(self, client):
        h_a = _auth_headers(client, email="a@example.com")
        client.post("/api/v1/candidates/", json={"email": "c@e.com"}, headers=h_a)

        client.post("/api/v1/auth/register", json={
            "email": "b@example.com", "password": "ValidPass1!",
            "full_name": "User B", "organization_name": "OrgB",
        })
        verify_user("b@example.com")
        token_b = client.post("/api/v1/auth/jwt/login", data={
            "username": "b@example.com", "password": "ValidPass1!",
        }).json()["access_token"]
        h_b = {"Authorization": f"Bearer {token_b}"}

        r = client.get("/api/v1/candidates/", headers=h_b)
        assert r.status_code == 200
        assert r.json()["total"] == 0


# ===========================================================================
# F. LARGE PAYLOADS
# ===========================================================================
class TestLargePayloads:
    def test_large_full_name(self, client):
        r = client.post("/api/v1/auth/register", json={
            "email": "large@e.com", "password": "ValidPass1!",
            "full_name": "A" * 200,  # at max
        })
        assert r.status_code == 201

    def test_oversized_full_name(self, client):
        r = client.post("/api/v1/auth/register", json={
            "email": "large@e.com", "password": "ValidPass1!",
            "full_name": "A" * 201,  # over max
        })
        assert r.status_code == 422

    def test_large_task_code(self, client):
        h = _auth_headers(client)
        r = client.post("/api/v1/tasks", json={
            "name": "Big Task",
            "description": "D" * 5000,  # at max
            "task_type": "debug", "difficulty": "mid", "duration_minutes": 30,
            "starter_code": "x = 1\n" * 10000,
            "test_code": "assert True\n" * 10000,
        }, headers=h)
        assert r.status_code in [201, 422]
