"""In-process application smoke tests.

These use the shared TestClient and test database; live deployment checks live
in ``test_qa_production_smoke.py`` behind the ``production`` marker. Select
this fast local contract with ``-m smoke``.
"""
import pytest
from tests.conftest import auth_headers, register_user, verify_user, login_user


pytestmark = pytest.mark.smoke


# ===================================================================
# HEALTH & BASIC CONNECTIVITY
# ===================================================================


def test_health_endpoint(client):
    """Public health is a cheap, exact liveness contract."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "taali-api"}


def test_readiness_endpoint_returns_503_for_degraded_runtime(client, monkeypatch):
    monkeypatch.setattr(
        "app.main._health_payload",
        lambda: {"status": "degraded", "service": "taali-api"},
    )

    resp = client.get("/ready")

    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"


def test_readiness_endpoint_returns_200_for_healthy_runtime(client, monkeypatch):
    monkeypatch.setattr(
        "app.main._health_payload",
        lambda: {"status": "healthy", "service": "taali-api"},
    )

    resp = client.get("/ready")

    assert resp.status_code == 200


def test_health_exposes_local_shadow_usage_meter_mode(client, monkeypatch):
    from app.platform.config import settings

    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "development")
    monkeypatch.setattr(settings, "SENTRY_DSN", None)
    monkeypatch.setattr(settings, "FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", False)
    monkeypatch.setattr(
        settings,
        "USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY",
        False,
    )

    payload = client.get("/admin/health", headers={"X-Admin-Secret": "test-admin-secret"}).json()

    assert payload["usage_meter"] == {
        "mode": "shadow",
        "live": False,
        "ready": True,
        "production_emergency_override": False,
    }


def test_health_marks_production_shadow_emergency_override_unready(
    client, monkeypatch
):
    from app.platform.config import settings

    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "production")
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", False)
    monkeypatch.setattr(
        settings,
        "USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY",
        True,
    )

    payload = client.get("/admin/health", headers={"X-Admin-Secret": "test-admin-secret"}).json()

    assert payload["status"] == "degraded"
    assert payload["usage_meter"] == {
        "mode": "shadow_emergency_override",
        "live": False,
        "ready": False,
        "production_emergency_override": True,
    }


def test_health_exposes_live_production_usage_meter_mode(client, monkeypatch):
    from app.platform.config import settings

    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "production")
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    monkeypatch.setattr(
        settings,
        "USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY",
        False,
    )

    payload = client.get("/admin/health", headers={"X-Admin-Secret": "test-admin-secret"}).json()

    assert payload["usage_meter"] == {
        "mode": "live",
        "live": True,
        "ready": True,
        "production_emergency_override": False,
    }


def test_health_names_connector_availability_separately_from_workable_oauth(
    client, monkeypatch
):
    from app.platform.config import settings

    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(settings, "WORKABLE_CLIENT_ID", "")
    monkeypatch.setattr(settings, "WORKABLE_CLIENT_SECRET", "")
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", False)

    payload = client.get("/admin/health", headers={"X-Admin-Secret": "test-admin-secret"}).json()
    integrations = payload["integrations"]

    # The legacy key remains, but reflects connector availability. A deployment
    # can therefore serve org-scoped direct-token Workable connections even when
    # it has no global OAuth app configured.
    assert integrations["workable_configured"] is True
    assert integrations["workable_connector_enabled"] is True
    assert integrations["workable_oauth_app_configured"] is False
    assert integrations["bullhorn_connector_enabled"] is False
    assert all("connected_org" not in key for key in integrations)


def test_github_health_legacy_alias_is_hidden_and_reuses_canonical_handler():
    from app.main import app

    routes = {route.path: route for route in app.routes if hasattr(route, "path")}
    canonical = routes["/admin/health/github"]
    legacy = routes["/healthz/github"]

    assert canonical.include_in_schema is True
    assert legacy.include_in_schema is False
    assert legacy.deprecated is True
    assert legacy.endpoint is canonical.endpoint


def test_github_health_has_one_canonical_handler_and_authenticated_legacy_alias(
    client, monkeypatch
):
    probe = {"ok": True, "status_code": 200, "detail": "ok", "org": "test"}
    monkeypatch.setattr(
        "app.services.github_credentials.verify_github_credentials",
        lambda **_kwargs: probe,
    )

    for path in ("/admin/health/github", "/healthz/github"):
        assert client.get(path).status_code == 403
        response = client.get(
            path,
            headers={"X-Admin-Secret": "test-admin-secret"},
        )
        assert response.status_code == 200
        assert response.json() == probe


# ===================================================================
# REGISTRATION SMOKE
# ===================================================================


def test_register_valid_data_201(client):
    """Registration with valid data should return 201."""
    resp = register_user(client, organization_name="SmokeTestOrg")
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert "email" in data
    assert data.get("is_verified") is False


def test_register_short_password_422(client):
    """Registration with short password should return 400 or 422."""
    resp = register_user(client, password="short")
    assert resp.status_code in (400, 422)
    data = resp.json()
    assert "detail" in data


def test_register_invalid_email_422(client):
    """Registration with invalid email should return 422."""
    resp = client.post("/api/v1/auth/register", json={
        "email": "not-an-email",
        "password": "TestPass123!",
        "full_name": "Test User",
    })
    assert resp.status_code == 422


# ===================================================================
# LOGIN SMOKE
# ===================================================================


def test_login_after_verification(client):
    """Full register → verify → login flow."""
    email = "smoke-login@test.com"
    register_user(client, email=email, organization_name="SmokeLogin")
    verify_user(email)
    resp = login_user(client, email)
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_unverified_is_rejected(client):
    """Login requires email verification."""
    email = "unverified-smoke@test.com"
    register_user(client, email=email)
    resp = login_user(client, email)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "LOGIN_USER_NOT_VERIFIED"


# ===================================================================
# AUTH PROTECTION
# ===================================================================


def test_protected_endpoints_require_auth(client):
    """All major protected endpoints should return 401 without token."""
    endpoints = [
        ("GET", "/api/v1/users/me"),
        ("GET", "/api/v1/tasks/"),
        ("GET", "/api/v1/candidates/"),
        ("GET", "/api/v1/assessments/"),
        ("GET", "/api/v1/analytics/"),
        ("GET", "/api/v1/billing/usage"),
        ("GET", "/api/v1/organizations/me"),
    ]
    for method, path in endpoints:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}, expected 401"


# ===================================================================
# SECURITY HEADERS
# ===================================================================


def test_security_headers_present(client):
    """Responses should include key security headers."""
    resp = client.get("/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert "strict-origin" in resp.headers.get("referrer-policy", "").lower()


def test_cors_headers_on_options(client):
    """OPTIONS request should include CORS headers."""
    resp = client.options(
        "/api/v1/auth/register",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


# ===================================================================
# REGISTRATION ERROR MESSAGES
# ===================================================================


def test_register_422_has_readable_errors(client):
    """Validation error (400 or 422) should include readable detail."""
    resp = register_user(client, password="short")
    assert resp.status_code in (400, 422)
    data = resp.json()
    detail = data.get("detail", [])
    if isinstance(detail, list):
        assert len(detail) > 0
        for err in detail:
            assert "msg" in err or "message" in err
    else:
        assert isinstance(detail, (str, dict))


def test_register_duplicate_email_400(client):
    """Registering the same email twice should return 400."""
    email = "dupe-smoke@test.com"
    register_user(client, email=email)
    resp = register_user(client, email=email)
    assert resp.status_code == 400


def test_register_response_shape(client):
    """Registration response should have expected fields."""
    resp = register_user(client, organization_name="ShapeTest")
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert "email" in data
    assert "full_name" in data
    assert "is_active" in data
    assert "created_at" in data


# ===================================================================
# RESOURCE CREATION SMOKE
# ===================================================================


def test_create_task_smoke(client):
    """Creating a task after auth should work."""
    headers, _ = auth_headers(client)
    from tests.conftest import create_task_via_api
    resp = create_task_via_api(client, headers)
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_create_candidate_smoke(client):
    """Creating a candidate after auth should work."""
    headers, _ = auth_headers(client)
    from tests.conftest import create_candidate_via_api
    resp = create_candidate_via_api(client, headers)
    assert resp.status_code == 201
    assert "id" in resp.json()
