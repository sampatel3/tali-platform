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


def test_health_endpoint(client, monkeypatch):
    """Public health is a cheap, exact liveness contract."""
    def fail_if_detailed_health_runs():
        raise AssertionError("public liveness must not build protected diagnostics")

    monkeypatch.setattr("app.main._health_payload", fail_if_detailed_health_runs)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "taali-api"}


@pytest.mark.parametrize(
    ("runtime_status", "expected_status_code"),
    [("degraded", 503), ("healthy", 200)],
)
def test_readiness_endpoint_redacts_protected_diagnostics(
    client,
    monkeypatch,
    runtime_status,
    expected_status_code,
):
    def detailed_health_payload(*, include_s3):
        assert include_s3 is False
        return {
            "status": runtime_status,
            "service": "sentinel-service-must-not-leak",
            "queue_capabilities": "sentinel-queue-must-not-leak",
            "models": "sentinel-models-must-not-leak",
            "resend_probe_email_id": "sentinel-email-id-must-not-leak",
            "integrations": "sentinel-integrations-must-not-leak",
            "usage_meter": "sentinel-usage-meter-must-not-leak",
        }

    monkeypatch.setattr(
        "app.main._health_payload",
        detailed_health_payload,
    )

    resp = client.get("/ready")

    assert resp.status_code == expected_status_code
    assert resp.json() == {"status": runtime_status, "service": "taali-api"}


def test_readiness_keeps_critical_checks_but_skips_optional_s3(client, monkeypatch):
    from app.platform.config import settings

    critical = {"database": True, "redis": True, "worker": True}
    s3_calls = []

    class DatabaseProbe:
        def execute(self, _query):
            if not critical["database"]:
                raise RuntimeError("database unavailable")

        def close(self):
            pass

    class RedisProbe:
        def ping(self):
            return critical["redis"]

        def close(self):
            pass

    monkeypatch.setattr("app.platform.database.SessionLocal", DatabaseProbe)
    monkeypatch.setattr("redis.from_url", lambda *_args, **_kwargs: RedisProbe())
    monkeypatch.setattr(
        "app.services.agent_worker_health.worker_beat_status",
        lambda **_kwargs: {"ready": critical["worker"]},
    )
    monkeypatch.setattr(
        "app.services.s3_service.s3_status",
        lambda: s3_calls.append("called") or {"available": True},
    )
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "production")
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    monkeypatch.setattr(
        settings,
        "USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY",
        False,
    )

    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "taali-api"}

    for dependency in critical:
        critical[dependency] = False
        assert client.get("/ready").status_code == 503
        critical[dependency] = True

    monkeypatch.setattr(settings, "USAGE_METER_LIVE", False)
    assert client.get("/ready").status_code == 503
    assert s3_calls == []


def test_detailed_health_defaults_to_skipping_optional_s3(monkeypatch):
    from app.main import _health_payload

    s3_calls = []
    monkeypatch.setattr(
        "app.services.s3_service.s3_status",
        lambda: s3_calls.append("called") or {"available": True},
    )

    _health_payload()

    assert s3_calls == []


def test_admin_health_retains_optional_s3_diagnostics(client, monkeypatch):
    from app.platform.config import settings

    monkeypatch.setattr(settings, "S3_DISABLED", False)
    monkeypatch.setattr(settings, "AWS_ACCESS_KEY_ID", "configured-access-key")
    monkeypatch.setattr(settings, "AWS_SECRET_ACCESS_KEY", "configured-secret-key")
    monkeypatch.setattr(settings, "AWS_S3_BUCKET", "sentinel-s3-bucket")
    monkeypatch.setattr(settings, "AWS_REGION", "eu-west-2")
    s3_diagnostics = {
        "available": True,
        "ok": True,
        "configured": True,
        "bucket": "sentinel-s3-bucket",
        "region": "eu-west-2",
        "status": "ok",
        "reason": "ok",
    }
    monkeypatch.setattr(
        "app.services.s3_service.s3_status",
        lambda: s3_diagnostics,
    )

    response = client.get(
        "/admin/health",
        headers={"X-Admin-Secret": "test-admin-secret"},
    )

    assert response.status_code == 200
    assert response.json()["s3"] == s3_diagnostics


def test_admin_health_authenticates_before_building_diagnostics(client, monkeypatch):
    probe_calls = []
    payload = {
        "status": "healthy",
        "service": "taali-api",
        "database": True,
        "redis": True,
        "agent_worker": {
            "ready": True,
            "reason": None,
            "age_seconds": 1.5,
            "provider_detail": "preserved",
        },
        "s3": {
            "available": True,
            "ok": True,
            "configured": True,
            "bucket": "sentinel-s3-bucket",
            "region": "eu-west-2",
            "status": "ok",
            "reason": "ok",
        },
        "usage_meter": {
            "mode": "live",
            "live": True,
            "ready": True,
            "production_emergency_override": False,
            "provider_detail": "preserved",
        },
        "integrations": {
            "e2b_configured": True,
            "claude_configured": True,
            "workable_configured": True,
            "workable_connector_enabled": True,
            "workable_oauth_app_configured": True,
            "bullhorn_connector_enabled": True,
            "stripe_configured": True,
            "resend_configured": True,
            "provider_detail": "preserved",
        },
        "deployment_detail": "preserved",
    }

    def detailed_health(*, include_s3):
        probe_calls.append(include_s3)
        return payload

    monkeypatch.setattr("app.main._health_payload", detailed_health)

    for headers in ({}, {"X-Admin-Secret": "wrong-admin-secret"}):
        blocked = client.get("/admin/health", headers=headers)
        assert blocked.status_code == 403
        assert blocked.json() == {"detail": "Forbidden"}
    assert probe_calls == []

    response = client.get(
        "/admin/health",
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    assert response.status_code == 200
    assert response.json() == payload
    assert probe_calls == [True]


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
    probe_calls = []

    def github_probe(**kwargs):
        probe_calls.append(kwargs)
        return probe

    monkeypatch.setattr(
        "app.services.github_credentials.verify_github_credentials",
        github_probe,
    )

    for path in ("/admin/health/github", "/healthz/github"):
        previous_calls = len(probe_calls)
        for headers in ({}, {"X-Admin-Secret": "wrong-admin-secret"}):
            blocked = client.get(path, headers=headers)
            assert blocked.status_code == 403
            assert blocked.json() == {"detail": "Forbidden"}
        assert len(probe_calls) == previous_calls
        response = client.get(
            path,
            headers={"X-Admin-Secret": "test-admin-secret"},
        )
        assert response.status_code == 200
        assert response.json() == probe

    assert len(probe_calls) == 2


def test_github_health_response_preserves_mock_payload_shape(client, monkeypatch):
    probe = {
        "ok": True,
        "mock": True,
        "detail": "GITHUB_MOCK_MODE",
        "org": "test",
        "provider_detail": "preserved",
    }
    monkeypatch.setattr(
        "app.services.github_credentials.verify_github_credentials",
        lambda **_kwargs: probe,
    )

    response = client.get(
        "/admin/health/github",
        headers={"X-Admin-Secret": "test-admin-secret"},
    )

    assert response.status_code == 200
    assert response.json() == probe


def test_admin_and_github_health_openapi_document_auth_and_payloads():
    from app.main import app

    schema = app.openapi()

    assert "/healthz/github" not in schema["paths"]
    for path, response_schema in (
        ("/admin/health", "AdminHealthResponse"),
        ("/admin/health/github", "GithubHealthResponse"),
    ):
        operation = schema["paths"][path]["get"]
        assert operation["security"] == [{"AdminSecret": []}]
        assert set(operation["responses"]) == {"200", "403"}
        assert operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ] == {"$ref": f"#/components/schemas/{response_schema}"}
        assert operation["responses"]["403"]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/AdminForbiddenResponse"}

    admin_schema = schema["components"]["schemas"]["AdminHealthResponse"]
    assert set(admin_schema["required"]) == {
        "status",
        "service",
        "database",
        "redis",
        "agent_worker",
        "s3",
        "usage_meter",
        "integrations",
    }
    github_schema = schema["components"]["schemas"]["GithubHealthResponse"]
    assert set(github_schema["required"]) == {"ok", "detail", "org"}


def test_graphiti_health_legacy_alias_is_hidden_and_reuses_canonical_handler():
    from app.main import app

    routes = {route.path: route for route in app.routes if hasattr(route, "path")}
    canonical = routes["/admin/health/graphiti"]
    legacy = routes["/healthz/graphiti"]

    assert canonical.include_in_schema is True
    assert legacy.include_in_schema is False
    assert legacy.deprecated is True
    assert legacy.endpoint is canonical.endpoint


def test_graphiti_health_openapi_documents_admin_auth_and_status_contract():
    from app.main import app

    schema = app.openapi()
    operation = schema["paths"]["/admin/health/graphiti"]["get"]

    assert "/healthz/graphiti" not in schema["paths"]
    assert operation["security"] == [{"AdminSecret": []}]
    assert schema["components"]["securitySchemes"]["AdminSecret"] == {
        "type": "apiKey",
        "description": "Dedicated operator secret for admin-only routes.",
        "in": "header",
        "name": "X-Admin-Secret",
    }
    assert set(operation["responses"]) == {"200", "403", "503"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GraphitiHealthResponse"
    }
    assert operation["responses"]["403"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AdminForbiddenResponse"
    }
    assert operation["responses"]["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GraphitiHealthResponse"
    }
    assert schema["components"]["schemas"]["GraphitiHealthResponse"]["properties"][
        "status"
    ]["enum"] == ["ok", "initializing", "unconfigured", "error"]


def test_graphiti_health_has_authenticated_canonical_and_legacy_routes(
    client,
    monkeypatch,
):
    probe_calls = []

    def probe():
        probe_calls.append("called")
        return {"status": "ok"}

    monkeypatch.setattr("app.candidate_graph.client.healthcheck", probe)

    for path in ("/admin/health/graphiti", "/healthz/graphiti"):
        previous_calls = len(probe_calls)
        for headers in ({}, {"X-Admin-Secret": "wrong-admin-secret"}):
            blocked = client.get(path, headers=headers)
            assert blocked.status_code == 403
            assert blocked.json() == {"detail": "Forbidden"}
        assert len(probe_calls) == previous_calls
        response = client.get(
            path,
            headers={"X-Admin-Secret": "test-admin-secret"},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    assert probe_calls == ["called", "called"]


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
        # Match the live smoke's redirect-disabled transport so a non-canonical
        # path cannot hide a 307 behind the eventual authentication response.
        resp = client.request(method, path, follow_redirects=False)
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
