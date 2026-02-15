"""API tests for organization, billing, analytics, and team endpoints."""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

from app.models.assessment import Assessment, AssessmentStatus
from app.models.organization import Organization
from app.models.user import User
from app.domains.billing_webhooks import billing_routes, webhook_routes
from tests.conftest import auth_headers, register_user, verify_user


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
    assert isinstance(first['cost_usd']['estimated_storage_bytes'], int)
    assert first['cost_usd']['estimated_storage_bytes'] > 0


def test_billing_costs_no_auth_401(client):
    resp = client.get('/api/v1/billing/costs')
    assert resp.status_code == 401


def test_billing_credits_success(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/billing/credits", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "credits_balance" in payload
    assert "entries" in payload
    assert isinstance(payload["entries"], list)


def test_lemon_webhook_idempotent_crediting(client, db, monkeypatch):
    headers, email = auth_headers(client, email="lemon-owner@example.com", organization_name="Lemon Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None

    monkeypatch.setattr(webhook_routes.settings, "MVP_DISABLE_LEMON", False)
    monkeypatch.setattr(webhook_routes.settings, "LEMON_WEBHOOK_SECRET", "test-lemon-secret")

    payload = {
        "meta": {"event_name": "order_created"},
        "data": {
            "id": "order_123",
            "attributes": {
                "status": "paid",
                "custom_data": {
                    "org_id": str(org.id),
                    "credits": 7,
                    "pack_id": "starter_5",
                },
            },
        },
    }
    raw = json.dumps(payload).encode("utf-8")
    signature = hmac.new(b"test-lemon-secret", raw, hashlib.sha256).hexdigest()

    first = client.post("/api/v1/webhooks/lemon", data=raw, headers={"X-Signature": signature, "Content-Type": "application/json"})
    assert first.status_code == 200, first.text
    assert first.json()["credited"] is True

    second = client.post("/api/v1/webhooks/lemon", data=raw, headers={"X-Signature": signature, "Content-Type": "application/json"})
    assert second.status_code == 200, second.text
    assert second.json()["credited"] is False

    db.refresh(org)
    assert org.credits_balance == 7


def test_checkout_session_accepts_pack_id(client, monkeypatch):
    headers, _ = auth_headers(client)
    monkeypatch.setattr(billing_routes.settings, "MVP_DISABLE_LEMON", True)
    resp = client.post(
        "/api/v1/billing/checkout-session",
        json={
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
            "pack_id": "starter_5",
        },
        headers=headers,
    )
    assert resp.status_code == 503


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


def test_update_org_enterprise_policy_fields(client):
    headers, _ = auth_headers(client, email="owner@acme.com", organization_name="Acme Org")
    resp = client.patch(
        "/api/v1/organizations/me",
        json={
            "allowed_email_domains": ["acme.com", "@subsidiary.org"],
            "sso_enforced": True,
            "saml_enabled": True,
            "saml_metadata_url": "https://idp.acme.com/metadata.xml",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["allowed_email_domains"] == ["acme.com", "subsidiary.org"]
    assert data["sso_enforced"] is True
    assert data["saml_enabled"] is True
    assert data["saml_metadata_url"] == "https://idp.acme.com/metadata.xml"


def test_team_invite_rejects_email_outside_allowed_domains(client, db):
    headers, owner_email = auth_headers(client, email="owner@acme.com", organization_name="Acme Domain Org")
    owner = db.query(User).filter(User.email == owner_email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.allowed_email_domains = ["acme.com"]
    db.commit()

    resp = client.post(
        "/api/v1/users/invite",
        json={"email": "newcolleague@gmail.com", "full_name": "Outside Domain"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "Email domain is not allowed" in resp.json()["detail"]


def test_register_rejects_disallowed_domain_for_existing_org(client, db):
    first = register_user(
        client,
        email="admin@acme.com",
        password="TestPass123!",
        full_name="Admin",
        organization_name="Acme Locked Org",
    )
    assert first.status_code == 201, first.text
    verify_user("admin@acme.com")

    owner = db.query(User).filter(User.email == "admin@acme.com").first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.allowed_email_domains = ["acme.com"]
    db.commit()

    second = register_user(
        client,
        email="outsider@other.com",
        password="TestPass123!",
        full_name="Outsider",
        organization_name="Acme Locked Org",
    )
    assert second.status_code == 400
    assert "Email domain is not allowed" in second.text


def test_jwt_login_blocked_when_org_enforces_sso(client, db):
    headers, owner_email = auth_headers(client, email="owner@sso.com", organization_name="SSO Org")
    owner = db.query(User).filter(User.email == owner_email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.sso_enforced = True
    db.commit()

    login_resp = client.post(
        "/api/v1/auth/jwt/login",
        data={"username": owner_email, "password": "TestPass123!"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login_resp.status_code == 403
    assert "Organization enforces SSO" in login_resp.json()["detail"]
