"""Read-only contract smoke tests for the live production API.

These checks use only GET requests plus a CORS preflight on non-rate-limited
``/health``. Auth POSTs belong in local or staging tests because even rejected
login attempts create audit rows and consume shared Redis rate-limit state.
Production smoke must be repeatable, exact, and cleanup-free.
"""

from __future__ import annotations

import os

import pytest
import requests


PROD_URL = os.getenv("TALI_PROD_URL", "").rstrip("/")
FRONTEND_URL = os.getenv("TALI_FRONTEND_URL", "https://www.taali.ai").rstrip("/")

pytestmark = pytest.mark.production

_ALLOWED_PRODUCTION_REQUESTS = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/ready"),
        ("GET", "/admin/health"),
        ("GET", "/admin/health/graphiti"),
        ("GET", "/healthz/graphiti"),
        ("GET", "/admin/health/github"),
        ("GET", "/healthz/github"),
        ("GET", "/api/v1/users/me"),
        ("GET", "/api/v1/assessments/"),
        ("GET", "/api/v1/candidates/"),
        ("GET", "/api/v1/analytics/"),
        ("GET", "/api/v1/billing/usage"),
        ("GET", "/api/v1/organizations/me"),
        ("GET", "/public/v1/roles"),
        ("GET", "/api/docs"),
        ("GET", "/api/openapi.json"),
        ("OPTIONS", "/health"),
    }
)


@pytest.fixture(scope="session", autouse=True)
def _configured_production_url() -> None:
    assert PROD_URL.startswith("https://"), (
        "TALI_PROD_URL must be an explicit HTTPS deployment URL; production "
        "tests never fall back to a hard-coded environment"
    )


def _request(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """Execute only the exact reviewed, read-only production network surface."""
    request_key = (str(method).upper(), str(path))
    if request_key not in _ALLOWED_PRODUCTION_REQUESTS:
        raise AssertionError(f"unreviewed production request blocked: {request_key!r}")
    return requests.request(
        method=request_key[0],
        url=f"{PROD_URL}{request_key[1]}",
        headers=headers,
        timeout=15,
        allow_redirects=False,
    )


def _get(path: str) -> requests.Response:
    return _request("GET", path)


def test_production_network_allowlist_fails_closed_before_transport():
    with pytest.raises(AssertionError, match="unreviewed production request blocked"):
        _request("POST", "/health")
    with pytest.raises(AssertionError, match="unreviewed production request blocked"):
        _request("GET", "/unreviewed")


class TestProductionHealth:
    def test_health_endpoint_is_live(self):
        response = _get("/health")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok", "service": "taali-api"}

    def test_readiness_endpoint_is_healthy(self):
        """Fail the scheduled smoke when workers or critical dependencies die.

        Liveness alone deliberately remains 200 during a partial outage; the
        readiness contract is what proves the full hiring runtime can work.
        """
        response = _get("/ready")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "healthy", "service": "taali-api"}

    @pytest.mark.parametrize(
        "path",
        [
            "/admin/health",
            "/admin/health/graphiti",
            "/healthz/graphiti",
            "/admin/health/github",
            "/healthz/github",
        ],
    )
    def test_operator_health_probes_require_admin_authentication(self, path: str):
        response = _get(path)
        assert response.status_code == 403, (
            f"{path}: {response.status_code} {response.text}"
        )
        assert response.json() == {"detail": "Forbidden"}

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/users/me",
            "/api/v1/assessments/",
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


class TestProductionCors:
    def test_cors_preflight_for_canonical_frontend(self):
        response = _request(
            "OPTIONS",
            "/health",
            headers={
                "Origin": FRONTEND_URL,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
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
