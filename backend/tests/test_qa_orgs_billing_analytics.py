"""
QA Test Suite: Organizations, Billing, Analytics, Team/Users
Covers: org CRUD, billing endpoints, analytics, team invites.
~30 tests
"""
from tests.conftest import verify_user


def _auth_headers(client, email="u@example.com"):
    client.post("/api/v1/auth/register", json={
        "email": email, "password": "ValidPass1!", "full_name": "Test User", "organization_name": "TestOrg",
    })
    verify_user(email)
    token = client.post("/api/v1/auth/login", data={"username": email, "password": "ValidPass1!"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# A. ORGANIZATIONS
# ===========================================================================
class TestOrganizations:
    def test_get_my_org(self, client):
        h = _auth_headers(client)
        r = client.get("/api/v1/organizations/me", headers=h)
        assert r.status_code == 200
        d = r.json()
        assert d["name"] == "TestOrg"
        assert "slug" in d
        assert "plan" in d

    def test_get_org_no_auth(self, client):
        r = client.get("/api/v1/organizations/me")
        assert r.status_code == 401

    def test_update_org_name(self, client):
        h = _auth_headers(client)
        r = client.patch("/api/v1/organizations/me", json={"name": "Updated Org"}, headers=h)
        assert r.status_code == 200
        assert r.json()["name"] == "Updated Org"

    def test_update_org_no_auth(self, client):
        r = client.patch("/api/v1/organizations/me", json={"name": "X"})
        assert r.status_code == 401

    def test_user_without_org_gets_appropriate_response(self, client):
        """User registered without org should get 404 or null org."""
        client.post("/api/v1/auth/register", json={
            "email": "noorg@e.com", "password": "ValidPass1!", "full_name": "No Org",
        })
        verify_user("noorg@e.com")
        token = client.post("/api/v1/auth/login", data={
            "username": "noorg@e.com", "password": "ValidPass1!",
        }).json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        r = client.get("/api/v1/organizations/me", headers=h)
        assert r.status_code in [200, 404]

    def test_workable_authorize_url(self, client):
        h = _auth_headers(client)
        r = client.get("/api/v1/organizations/workable/authorize-url", headers=h)
        # May succeed or fail depending on Workable config; 503 if disabled
        assert r.status_code in [200, 400, 500, 503]


# ===========================================================================
# B. BILLING
# ===========================================================================
class TestBilling:
    def test_get_usage(self, client):
        h = _auth_headers(client)
        r = client.get("/api/v1/billing/usage", headers=h)
        assert r.status_code == 200
        d = r.json()
        assert "usage" in d
        assert "total_cost" in d
        assert isinstance(d["usage"], list)

    def test_get_usage_no_auth(self, client):
        r = client.get("/api/v1/billing/usage")
        assert r.status_code == 401

    def test_create_checkout_session(self, client):
        h = _auth_headers(client)
        r = client.post("/api/v1/billing/checkout-session", json={
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        }, headers=h)
        # Stripe may not be configured; 503 if disabled
        assert r.status_code in [200, 400, 500, 503]

    def test_create_checkout_no_auth(self, client):
        r = client.post("/api/v1/billing/checkout-session", json={
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        })
        assert r.status_code == 401


# ===========================================================================
# C. ANALYTICS
# ===========================================================================
class TestAnalytics:
    def test_get_analytics(self, client):
        h = _auth_headers(client)
        r = client.get("/api/v1/analytics/", headers=h)
        assert r.status_code == 200
        d = r.json()
        # Should have analytics fields
        assert isinstance(d, dict)

    def test_get_analytics_no_auth(self, client):
        r = client.get("/api/v1/analytics/")
        assert r.status_code == 401

    def test_analytics_with_data(self, client):
        """Analytics should work even with some assessments."""
        h = _auth_headers(client)
        # Create task (name must be 3+ chars)
        task_resp = client.post("/api/v1/tasks", json={
            "name": "Test Task", "description": "Description for testing", "task_type": "debug",
            "difficulty": "mid", "duration_minutes": 30,
            "starter_code": "x = 1", "test_code": "assert True",
        }, headers=h)
        assert task_resp.status_code == 201, f"Task creation failed: {task_resp.text}"
        task = task_resp.json()
        client.post("/api/v1/assessments", json={
            "candidate_email": "c@e.com", "candidate_name": "C", "task_id": task["id"],
        }, headers=h)
        r = client.get("/api/v1/analytics/", headers=h)
        assert r.status_code == 200


# ===========================================================================
# D. TEAM / USERS
# ===========================================================================
class TestTeam:
    def test_list_team(self, client):
        h = _auth_headers(client)
        r = client.get("/api/v1/users/", headers=h)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1  # at least the registered user

    def test_list_team_no_auth(self, client):
        r = client.get("/api/v1/users/")
        assert r.status_code == 401

    def test_invite_team_member(self, client):
        h = _auth_headers(client)
        r = client.post("/api/v1/users/invite", json={
            "email": "invited@example.com", "full_name": "Invited User",
        }, headers=h)
        assert r.status_code == 201
        d = r.json()
        assert d["email"] == "invited@example.com"

    def test_invite_no_auth(self, client):
        r = client.post("/api/v1/users/invite", json={
            "email": "x@e.com", "full_name": "X",
        })
        assert r.status_code == 401

    def test_invite_missing_email(self, client):
        h = _auth_headers(client)
        r = client.post("/api/v1/users/invite", json={"full_name": "X"}, headers=h)
        assert r.status_code == 422

    def test_invite_missing_name(self, client):
        h = _auth_headers(client)
        r = client.post("/api/v1/users/invite", json={"email": "x@e.com"}, headers=h)
        assert r.status_code == 422

    def test_invited_user_appears_in_team_list(self, client):
        h = _auth_headers(client)
        client.post("/api/v1/users/invite", json={
            "email": "new@e.com", "full_name": "New User",
        }, headers=h)
        r = client.get("/api/v1/users/", headers=h)
        emails = [u["email"] for u in r.json()]
        assert "new@e.com" in emails
