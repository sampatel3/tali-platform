"""Unit tests for the Workable client's shared rate limiter + 429 backoff.

Regression for the 2026-05-24 incident: an uncoordinated 0.3s per-call sleep
let a sync's prefetch thread-pool burst past Workable's 10 req/10s limit and
trip 429s, and the 429 handler blindly slept a hardcoded 11s (ignoring
Retry-After) with only one retry. These cover the replacements: a shared
sliding-window limiter and Retry-After-aware bounded backoff.
"""
from __future__ import annotations

import httpx
import pytest

from app.components.integrations.workable import service as svc


def _req() -> httpx.Request:
    return httpx.Request("GET", "https://x.workable.com/spi/v3/jobs")


# --- _retry_after_seconds ---------------------------------------------------


def test_retry_after_seconds_honors_numeric_header():
    resp = httpx.Response(429, headers={"Retry-After": "7"}, request=_req())
    assert svc._retry_after_seconds(resp, 0) == 7.0


def test_retry_after_seconds_caps_oversized_header():
    resp = httpx.Response(429, headers={"Retry-After": "9999"}, request=_req())
    assert svc._retry_after_seconds(resp, 0) == svc.WORKABLE_BACKOFF_CAP_SEC


def test_retry_after_seconds_exponential_backoff_without_header():
    resp = httpx.Response(429, request=_req())
    assert svc._retry_after_seconds(resp, 0) == svc.WORKABLE_BACKOFF_BASE_SEC
    assert svc._retry_after_seconds(resp, 1) == svc.WORKABLE_BACKOFF_BASE_SEC * 2
    assert svc._retry_after_seconds(resp, 2) == svc.WORKABLE_BACKOFF_BASE_SEC * 4


def test_retry_after_seconds_non_numeric_header_falls_back_to_backoff():
    # Retry-After can be an HTTP-date; we don't parse it, just back off.
    resp = httpx.Response(
        429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, request=_req()
    )
    assert svc._retry_after_seconds(resp, 0) == svc.WORKABLE_BACKOFF_BASE_SEC


# --- _WorkableRateLimiter ---------------------------------------------------


def test_rate_limiter_caps_burst_within_window(monkeypatch):
    """The (max+1)th call in a window blocks until the oldest call ages out."""
    clock = {"t": 1000.0}
    sleeps: list[float] = []
    monkeypatch.setattr(svc.time, "monotonic", lambda: clock["t"])

    def _sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(svc.time, "sleep", _sleep)

    lim = svc._WorkableRateLimiter(max_requests=2, window_sec=10.0)
    lim.acquire()
    lim.acquire()
    assert sleeps == []  # two slots free — no wait
    lim.acquire()  # window full → wait the full window for the oldest to expire
    assert sleeps == [10.0]


def test_get_rate_limiter_shared_per_subdomain():
    a = svc._get_rate_limiter("acme")
    b = svc._get_rate_limiter("ACME")  # case-insensitive — same token budget
    c = svc._get_rate_limiter("other")
    assert a is b
    assert a is not c


# --- _request 429 retry (end-to-end through the client) ---------------------


def test_request_retries_on_429_then_succeeds(monkeypatch):
    req = _req()
    responses = [
        httpx.Response(429, headers={"Retry-After": "2"}, request=req),
        httpx.Response(200, json={"ok": 1}, request=req),
    ]

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, *a, **k):
            return responses.pop(0)

    monkeypatch.setattr(svc.httpx, "Client", _FakeClient)
    slept: list[float] = []
    monkeypatch.setattr(svc.time, "sleep", lambda s: slept.append(s))

    client = svc.WorkableService("tk", "rl-retry-then-ok")
    out = client._request("GET", "/jobs")

    assert out == {"ok": 1}
    assert slept == [2.0]  # honored Retry-After, retried once, then succeeded


def test_request_raises_after_exhausting_429_retries(monkeypatch):
    req = _req()

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, *a, **k):
            return httpx.Response(429, request=req)

    monkeypatch.setattr(svc.httpx, "Client", _FakeClient)
    calls = {"n": 0}
    monkeypatch.setattr(svc.time, "sleep", lambda s: calls.__setitem__("n", calls["n"] + 1))

    client = svc.WorkableService("tk", "rl-always-429")
    with pytest.raises(httpx.HTTPStatusError):
        client._request("GET", "/jobs")
    # WORKABLE_MAX_ATTEMPTS total tries → MAX_ATTEMPTS-1 backoff sleeps.
    assert calls["n"] == svc.WORKABLE_MAX_ATTEMPTS - 1


def _status_error(status: int, body: str = "provider-secret") -> httpx.HTTPStatusError:
    response = httpx.Response(status, text=body, request=_req())
    return httpx.HTTPStatusError("unsafe provider detail", request=_req(), response=response)


def test_optional_request_only_swallows_expected_not_found(monkeypatch):
    client = svc.WorkableService("tk", "optional-not-found")

    def missing(*_args, **_kwargs):
        raise _status_error(404)

    monkeypatch.setattr(client, "_request", missing)
    assert client._request_optional("GET", "/optional") == {}


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_optional_request_surfaces_auth_and_provider_failures(monkeypatch, status):
    client = svc.WorkableService("tk", f"optional-failure-{status}")

    def failed(*_args, **_kwargs):
        raise _status_error(status)

    monkeypatch.setattr(client, "_request", failed)
    with pytest.raises(httpx.HTTPStatusError):
        client._request_optional("GET", "/optional")


def test_optional_request_surfaces_network_failures(monkeypatch):
    client = svc.WorkableService("tk", "optional-network")

    def failed(*_args, **_kwargs):
        raise httpx.ConnectError("private proxy address", request=_req())

    monkeypatch.setattr(client, "_request", failed)
    with pytest.raises(httpx.ConnectError):
        client._request_optional("GET", "/optional")


def test_workable_failure_result_never_echoes_provider_body():
    client = svc.WorkableService("tk", "safe-failure-result")
    result = client._failure_result(_status_error(401, "token=super-secret"))

    assert result["error_code"] == "workable_authorization_failed"
    assert "Reconnect Workable" in result["error"]
    assert "super-secret" not in str(result)


def test_workable_write_failure_never_logs_provider_body(monkeypatch, caplog):
    client = svc.WorkableService("tk", "safe-write-failure")
    secret_marker = "workable-write-provider-secret-must-not-escape"

    def failed(*_args, **_kwargs):
        raise _status_error(401, secret_marker)

    monkeypatch.setattr(client, "_request", failed)

    result = client.post_candidate_comment("candidate-1", "member-1", "note")

    assert result["success"] is False
    assert secret_marker not in str(result)
    assert secret_marker not in caplog.text
    assert "workable_post_comment:HTTPStatusError" in caplog.text
