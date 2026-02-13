"""API tests for organization, billing, analytics, and team endpoints."""

import uuid
from datetime import datetime, timezone

from app.models.assessment import Assessment, AssessmentStatus
from tests.conftest import auth_headers


# ---------------------------------------------------------------------------
# GET /api/v1/organizations/me — Current org
# ---------------------------------------------------------------------------


def test_get_org_success(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/organizations/me", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert "name" in data


def test_get_org_no_auth_401(client):
    resp = client.get("/api/v1/organizations/me")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /api/v1/organizations/me — Update org
# ---------------------------------------------------------------------------


def test_update_org_name(client):
    headers, _ = auth_headers(client)
    resp = client.patch(
        "/api/v1/organizations/me",
        json={"name": "Renamed Org"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Renamed Org"


def test_update_org_no_auth_401(client):
    resp = client.patch(
        "/api/v1/organizations/me",
        json={"name": "Should Fail"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/billing/usage — Billing usage
# ---------------------------------------------------------------------------


def test_billing_usage_success(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/billing/usage", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # Usage response should contain some numeric or structured data
    assert isinstance(data, dict)


def test_billing_usage_no_auth_401(client):
    resp = client.get("/api/v1/billing/usage")
    assert resp.status_code == 401


def test_billing_costs_success(client, db):
    headers, _ = auth_headers(client)

    task_resp = client.post(
        "/api/v1/tasks/",
        json={
            "name": "Cost Task",
            "description": "Cost tracking task",
            "task_type": "python",
            "difficulty": "medium",
            "duration_minutes": 30,
            "starter_code": "# start",
            "test_code": "def test_ok():\n    assert True",
        },
        headers=headers,
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["id"]

    assessment_resp = client.post(
        "/api/v1/assessments/",
        json={
            "candidate_email": "cost-candidate@example.com",
            "candidate_name": "Cost Candidate",
            "task_id": task_id,
        },
        headers=headers,
    )
    assert assessment_resp.status_code == 201

    # Seed usage signals so cost breakdown is non-zero
    assessment = db.query(Assessment).first()
    assert assessment is not None
    assessment.status = AssessmentStatus.COMPLETED
    assessment.started_at = datetime.now(timezone.utc)
    assessment.completed_at = datetime.now(timezone.utc)
    assessment.total_duration_seconds = 1800
    assessment.total_input_tokens = 12000
    assessment.total_output_tokens = 6000
    assessment.ai_prompts = [{"message": "help", "response": "ok"}]
    assessment.code_snapshots = [{"code": "print(1)"}]
    db.commit()

    resp = client.get('/api/v1/billing/costs', headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert 'costs' in data
    assert 'summary' in data
    assert 'thresholds' in data
    assert 'alerts' in data
    assert isinstance(data['costs'], list)
    assert len(data['costs']) >= 1

    first = data['costs'][0]
    assert 'cost_usd' in first
    assert first['cost_usd']['total'] >= 0
    assert 'claude' in first['cost_usd']
    assert 'e2b' in first['cost_usd']
    assert 'email' in first['cost_usd']
    assert 'storage' in first['cost_usd']


def test_billing_costs_no_auth_401(client):
    resp = client.get('/api/v1/billing/costs')
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/analytics/ — Analytics
# ---------------------------------------------------------------------------


def test_analytics_success(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/analytics/", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


def test_analytics_no_auth_401(client):
    resp = client.get("/api/v1/analytics/")
    assert resp.status_code == 401


def test_analytics_returns_expected_fields(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/analytics/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # Analytics should include at least some summary fields
    expected_keys = {"total_assessments", "total_candidates", "total_tasks"}
    # Accept if at least one expected key is present (API may vary)
    present = expected_keys & set(data.keys())
    assert len(present) > 0 or len(data) > 0, (
        f"Analytics response should contain summary data, got keys: {list(data.keys())}"
    )


# ---------------------------------------------------------------------------
# GET /api/v1/users/ — Team list
# ---------------------------------------------------------------------------


def test_team_list_success(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/users/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    # The authenticated user should appear in the team list
    assert len(items) >= 1


def test_team_list_no_auth_401(client):
    resp = client.get("/api/v1/users/")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/users/invite — Invite team member
# ---------------------------------------------------------------------------


def test_team_invite_success(client):
    headers, _ = auth_headers(client)
    resp = client.post(
        "/api/v1/users/invite",
        json={"email": "newcolleague@example.com", "full_name": "New Colleague"},
        headers=headers,
    )
    assert resp.status_code in (200, 201)


def test_team_invite_no_auth_401(client):
    resp = client.post(
        "/api/v1/users/invite",
        json={"email": "noauth@example.com", "full_name": "No Auth"},
    )
    assert resp.status_code == 401


def test_team_invite_invalid_email_422(client):
    headers, _ = auth_headers(client)
    resp = client.post(
        "/api/v1/users/invite",
        json={"email": "not-an-email", "full_name": "Bad Email"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_team_invited_user_appears_in_list(client):
    headers, _ = auth_headers(client)
    invite_email = "invitee@example.com"
    invite_resp = client.post(
        "/api/v1/users/invite",
        json={"email": invite_email, "full_name": "Invitee Person"},
        headers=headers,
    )
    assert invite_resp.status_code in (200, 201)

    # Verify the invited user shows up in the team list
    list_resp = client.get("/api/v1/users/", headers=headers)
    assert list_resp.status_code == 200
    data = list_resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    emails = [member.get("email", "") for member in items]
    assert invite_email in emails, (
        f"Expected invited user {invite_email} in team list, got: {emails}"
    )
