"""Read-only contract smoke tests for the live production API.

These checks deliberately create no users, organizations, assessments, or
other customer data. Destructive registration happy paths belong in staging;
production smoke should be repeatable, exact, and cleanup-free.
"""

from __future__ import annotations

import os

import pytest
import requests


PROD_URL = os.getenv("TALI_PROD_URL", "").rstrip("/")
FRONTEND_URL = os.getenv("TALI_FRONTEND_URL", "https://www.taali.ai").rstrip("/")
API = f"{PROD_URL}/api/v1"

pytestmark = pytest.mark.production


@pytest.fixture(scope="session", autouse=True)
def _configured_production_url() -> None:
    assert PROD_URL.startswith("https://"), (
        "TALI_PROD_URL must be an explicit HTTPS deployment URL; production "
        "tests never fall back to a hard-coded environment"
    )


def _get(path: str) -> requests.Response:
    return requests.get(f"{PROD_URL}{path}", timeout=15)


class TestProductionHealth:
    def test_health_endpoint_is_live(self):
        response = _get("/health")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload.get("status") == "ok"

    def test_readiness_endpoint_is_healthy(self):
        """Fail the scheduled smoke when workers or critical dependencies die.

        Liveness alone deliberately remains 200 during a partial outage; the
        readiness contract is what proves the full hiring runtime can work.
        """
        response = _get("/ready")
        assert response.status_code == 200, response.text
        assert response.json().get("status") == "healthy"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/users/me",
            "/api/v1/assessments",
            "/api/v1/candidates/",
            "/api/v1/analytics/",
            "/api/v1/billing/usage",
            "/api/v1/organizations/me",
        ],
    )
    def test_protected_endpoint_requires_authentication(self, path: str):
        response = _get(path)
        assert response.status_code == 401, f"{path}: {response.status_code} {response.text}"

    def test_public_api_requires_an_api_key(self):
        response = _get("/public/v1/roles")
        assert response.status_code == 401, response.text


class TestProductionInputContracts:
    def test_registration_validation_is_exact_and_non_mutating(self):
        response = requests.post(
            f"{API}/auth/register",
            json={},
            timeout=15,
        )
        assert response.status_code == 422, response.text
        detail = response.json().get("detail")
        assert isinstance(detail, list) and detail

    def test_nonexistent_login_is_rejected(self):
        response = requests.post(
            f"{API}/auth/jwt/login",
            data={
                "username": "qa-smoke-nonexistent@example.invalid",
                "password": "NotARealProductionPassword-1!",
            },
            timeout=15,
        )
        assert response.status_code in {400, 401}, response.text

    def test_cors_preflight_for_canonical_frontend(self):
        response = requests.options(
            f"{API}/auth/register",
            headers={
                "Origin": FRONTEND_URL,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
            timeout=15,
        )
        assert response.status_code == 200, response.text
        assert response.headers.get("access-control-allow-origin") == FRONTEND_URL


class TestProductionSecurity:
    @pytest.mark.parametrize("path", ["/api/docs", "/api/openapi.json"])
    def test_development_schema_surfaces_are_disabled(self, path: str):
        response = _get(path)
        assert response.status_code == 404, f"{path}: {response.status_code} {response.text}"

    def test_security_headers(self):
        response = _get("/health")
        assert response.status_code == 200, response.text
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-frame-options") == "DENY"
        assert response.headers.get("referrer-policy")

    def test_sql_injection_payload_is_rejected(self):
        response = requests.post(
            f"{API}/auth/jwt/login",
            data={"username": "' OR 1=1 --", "password": "' OR 1=1 --"},
            timeout=15,
        )
        assert response.status_code in {400, 401, 422}, response.text
