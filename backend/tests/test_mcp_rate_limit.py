"""Rate-limit coverage for the public /mcp mount.

Since #890 the /mcp streamable-HTTP sub-app accepts tali_* public API keys, so
it is internet-facing with key auth. ASGI mounts bypass route-level deps, so the
limit lives in RateLimitMiddleware (which wraps the whole app, mounts included).

These pin:
- /mcp requests over the limit get a 429 with the shared middleware body
- different API-key prefixes get separate buckets (one key can't exhaust another)
- JWT/session (non-tali_) callers bucket per IP, not globally
- non-/mcp paths are unaffected by the /mcp limit
- MCP_RATE_LIMIT_PER_MINUTE=0 disables the /mcp limit

The middleware buckets on the tali_* prefix WITHOUT verifying the key, so these
tests can drive it with synthetic prefixes and never mint a real key.
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from fastapi.testclient import TestClient

from app.main import app
from app.platform import middleware as mw
from app.platform.config import settings
from app.services.rate_limit import reset_memory_buckets


MCP_HEADERS_BASE = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


@pytest.fixture(autouse=True)
def _reset_rate_state():
    reset_memory_buckets()
    yield
    reset_memory_buckets()


@pytest.fixture
def mcp_client():
    """Client that wraps the real app (middleware + /mcp mount). Server
    exceptions are surfaced as 500s (not raised) so a request the middleware
    lets THROUGH — which then hits the SSE session negotiation the bare
    TestClient can't complete — is simply "not 429", keeping these tests
    focused on the rate-limit boundary rather than MCP session plumbing.
    """
    reset_memory_buckets()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    reset_memory_buckets()


def _post_mcp(client, headers):
    return client.post("/mcp/", json=_BODY, headers={**MCP_HEADERS_BASE, **headers})


def test_mcp_over_limit_returns_429(mcp_client, monkeypatch):
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_PER_MINUTE", 3)
    headers = {"Authorization": "Bearer tali_live_aaaaaabbbbbb"}

    for _ in range(3):
        resp = _post_mcp(mcp_client, headers)
        assert resp.status_code != 429, resp.text

    resp = _post_mcp(mcp_client, headers)
    assert resp.status_code == 429
    assert resp.json() == {"detail": "Too many requests. Please try again later."}


def test_mcp_x_api_key_header_is_limited(mcp_client, monkeypatch):
    """A key in the X-API-Key slot is bucketed the same as the bearer slot."""
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_PER_MINUTE", 2)
    headers = {"X-API-Key": "tali_test_zzzzzz111111"}

    assert _post_mcp(mcp_client, headers).status_code != 429
    assert _post_mcp(mcp_client, headers).status_code != 429
    assert _post_mcp(mcp_client, headers).status_code == 429


def test_mcp_distinct_key_prefixes_have_separate_buckets(mcp_client, monkeypatch):
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_PER_MINUTE", 2)
    key_a = {"Authorization": "Bearer tali_live_aaaaaa000000_secretpartA"}
    key_b = {"Authorization": "Bearer tali_live_bbbbbb999999_secretpartB"}

    # Exhaust key A's bucket.
    assert _post_mcp(mcp_client, key_a).status_code != 429
    assert _post_mcp(mcp_client, key_a).status_code != 429
    assert _post_mcp(mcp_client, key_a).status_code == 429

    # key B is untouched — its own bucket is still open.
    assert _post_mcp(mcp_client, key_b).status_code != 429
    assert _post_mcp(mcp_client, key_b).status_code != 429
    assert _post_mcp(mcp_client, key_b).status_code == 429


def test_mcp_same_key_different_secret_shares_bucket(mcp_client, monkeypatch):
    """The bucket is the stable prefix slice, so the same key rotated through
    two request secrets (or a spoofed tail) shares one budget."""
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_PER_MINUTE", 2)
    h1 = {"Authorization": "Bearer tali_live_prefix00000_tailONE"}
    h2 = {"Authorization": "Bearer tali_live_prefix00000_tailTWO"}

    assert _post_mcp(mcp_client, h1).status_code != 429
    assert _post_mcp(mcp_client, h2).status_code != 429
    # Both mapped to key:tali_live_prefix00 -> third is over.
    assert _post_mcp(mcp_client, h1).status_code == 429


def test_mcp_zero_setting_disables_limit(mcp_client, monkeypatch):
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_PER_MINUTE", 0)
    headers = {"Authorization": "Bearer tali_live_aaaaaabbbbbb"}
    for _ in range(10):
        assert _post_mcp(mcp_client, headers).status_code != 429


def test_non_mcp_path_unaffected_by_mcp_limit(mcp_client, monkeypatch):
    """A tiny /mcp limit must not throttle other routes."""
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_PER_MINUTE", 1)
    for _ in range(5):
        resp = mcp_client.get("/health")
        assert resp.status_code != 429, resp.text


# ---------------------------------------------------------------------------
# Bucketing helper (unit) — key prefix vs IP fallback
# ---------------------------------------------------------------------------


class _FakeHeaders:
    def __init__(self, d):
        self._d = {k.lower(): v for k, v in d.items()}

    def get(self, k, default=None):
        return self._d.get(k.lower(), default)


class _FakeRequest:
    def __init__(self, headers, client_host="testclient"):
        self.headers = _FakeHeaders(headers)
        self.client = SimpleNamespace(host=client_host)


def test_buckets_key_scoped_by_ip_plus_ip_guard():
    req = _FakeRequest({"Authorization": "Bearer tali_live_abcdef123456_secret"})
    buckets = mw._mcp_buckets(req, "9.9.9.9")
    assert buckets[0][0] == "mcp:key:tali_live_abcdef:9.9.9.9"
    assert buckets[1][0] == "mcp:ip:9.9.9.9"
    assert buckets[1][1] == buckets[0][1] * 4


def test_buckets_x_api_key_slot():
    req = _FakeRequest({"X-API-Key": "tali_test_abcdef123456_secret"})
    assert mw._mcp_buckets(req, "9.9.9.9")[0][0] == "mcp:key:tali_test_abcdef:9.9.9.9"


def test_buckets_fall_back_to_single_ip_bucket_for_jwt():
    req = _FakeRequest({"Authorization": "Bearer eyJhbGciOi.jwt.token"})
    buckets = mw._mcp_buckets(req, "9.9.9.9")
    assert [k for k, _ in buckets] == ["mcp:ip:9.9.9.9"]


def test_buckets_fall_back_to_single_ip_bucket_when_no_auth():
    req = _FakeRequest({})
    buckets = mw._mcp_buckets(req, "1.2.3.4")
    assert [k for k, _ in buckets] == ["mcp:ip:1.2.3.4"]


# ---------------------------------------------------------------------------
# Spoofed-prefix attack paths (the P2): rotation must hit the IP guard, and a
# known display prefix from another IP must not burn the real key's bucket.
# ---------------------------------------------------------------------------


def test_spoofed_prefix_rotation_capped_by_ip_guard(mcp_client, monkeypatch):
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_PER_MINUTE", 3)
    statuses = [
        _post_mcp(mcp_client, {"Authorization": f"Bearer tali_live_spoof{i:04d}xx"}).status_code
        for i in range(3 * 4 + 2)
    ]
    assert 429 in statuses


def test_untrusted_forwarded_for_cannot_create_fresh_ip_buckets(mcp_client, monkeypatch):
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_PER_MINUTE", 3)
    for _ in range(6):
        _post_mcp(
            mcp_client,
            {"Authorization": "Bearer tali_live_abcdefSPOOF", "X-Forwarded-For": "6.6.6.6"},
        )
    resp = _post_mcp(
        mcp_client,
        {"Authorization": "Bearer tali_live_abcdefREAL", "X-Forwarded-For": "7.7.7.7"},
    )
    # TestClient is not a configured trusted proxy, so attacker-controlled XFF
    # values cannot split one real peer into unlimited limiter buckets.
    assert resp.status_code == 429


def test_client_ip_uses_forwarded_chain_only_from_trusted_proxy(monkeypatch):
    req = _FakeRequest(
        {"X-Forwarded-For": "198.51.100.20"},
        client_host="10.0.0.5",
    )
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "")
    assert mw.resolve_client_ip(req) == "10.0.0.5"

    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    assert mw.resolve_client_ip(req) == "198.51.100.20"


def test_railway_real_ip_separates_clients_and_ignores_spoofed_xff(
    mcp_client, monkeypatch
):
    monkeypatch.setattr(settings, "MCP_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "TRUST_RAILWAY_X_REAL_IP", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "")
    jwt = "Bearer eyJhbGciOi.jwt.token"

    first = _post_mcp(
        mcp_client,
        {
            "Authorization": jwt,
            "X-Real-IP": "198.51.100.20",
            "X-Forwarded-For": "6.6.6.6",
        },
    )
    spoofed_xff = _post_mcp(
        mcp_client,
        {
            "Authorization": jwt,
            "X-Real-IP": "198.51.100.20",
            "X-Forwarded-For": "7.7.7.7",
        },
    )
    distinct_client = _post_mcp(
        mcp_client,
        {
            "Authorization": jwt,
            "X-Real-IP": "198.51.100.21",
            "X-Forwarded-For": "6.6.6.6",
        },
    )

    assert first.status_code != 429
    assert spoofed_xff.status_code == 429
    assert distinct_client.status_code != 429


def test_railway_real_ip_is_opt_in_and_must_be_valid(monkeypatch):
    req = _FakeRequest(
        {"X-Real-IP": "198.51.100.20", "X-Forwarded-For": "203.0.113.4"},
        client_host="10.0.0.5",
    )
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "")
    monkeypatch.setattr(settings, "TRUST_RAILWAY_X_REAL_IP", False)
    assert mw.resolve_client_ip(req) == "10.0.0.5"

    monkeypatch.setattr(settings, "TRUST_RAILWAY_X_REAL_IP", True)
    assert mw.resolve_client_ip(req) == "198.51.100.20"

    invalid = _FakeRequest({"X-Real-IP": "not-an-ip"}, client_host="10.0.0.5")
    assert mw.resolve_client_ip(invalid) == "10.0.0.5"
