from __future__ import annotations

import httpx
import pytest

from app.components.integrations.e2b import service as e2b_service


class _StreamingResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_verify_access_uses_only_bounded_read_only_list(monkeypatch):
    client_options: dict[str, object] = {}
    clients: list[httpx.Client] = []
    requests: list[httpx.Request] = []
    real_client = httpx.Client

    def _handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    def _client(**kwargs):
        client_options.update(kwargs)
        client = real_client(
            **kwargs,
            transport=httpx.MockTransport(_handle_request),
        )
        clients.append(client)
        return client

    class _ForbiddenSandbox:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("credential verification must not create a sandbox")

        @classmethod
        def list(cls, *_args, **_kwargs):
            raise AssertionError("credential verification must not use unbounded SDK list")

    monkeypatch.setenv("E2B_DOMAIN", "sandbox.example.test")
    monkeypatch.delenv("E2B_DEBUG", raising=False)
    monkeypatch.setattr(e2b_service.httpx, "Client", _client)
    monkeypatch.setattr(e2b_service, "Sandbox", _ForbiddenSandbox)

    result = e2b_service.E2BService("e2b-live").verify_access(
        request_timeout_seconds=7.5
    )

    assert result is True
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url == "https://api.sandbox.example.test/v2/sandboxes?limit=1"
    assert request.headers["X-API-KEY"] == "e2b-live"
    assert client_options["base_url"] == "https://api.sandbox.example.test"
    assert client_options["follow_redirects"] is False
    timeout = clients[0].timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 7.5
    assert timeout.read == 7.5
    assert timeout.write == 7.5
    assert timeout.pool == 7.5


def test_verify_access_rejects_provider_error_without_reading_or_leaking_body(
    monkeypatch,
):
    class _Response(_StreamingResponse):
        @property
        def content(self):
            raise AssertionError("credential verification must not read provider bodies")

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, *, params):
            assert (method, url, params) == (
                "GET",
                "/v2/sandboxes",
                {"limit": 1},
            )
            return _Response(401)

    monkeypatch.setattr(e2b_service.httpx, "Client", _Client)

    with pytest.raises(RuntimeError) as exc_info:
        e2b_service.E2BService("e2b-revoked-secret").verify_access()

    assert str(exc_info.value) == "E2B credential verification failed (HTTP 401)"
    assert "e2b-revoked-secret" not in str(exc_info.value)
    assert exc_info.value.__context__ is None


def test_verify_access_redacts_transport_failure_and_preserves_proxy(monkeypatch):
    client_options: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs):
            client_options.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            raise httpx.ReadTimeout("Bearer e2b-secret: provider response body")

    monkeypatch.setattr(e2b_service.httpx, "Client", _Client)

    with pytest.raises(RuntimeError) as exc_info:
        e2b_service.E2BService(
            "e2b-secret",
            proxy="http://proxy.example.test:8080",
        ).verify_access()

    assert str(exc_info.value) == "E2B credential verification timed out"
    assert "e2b-secret" not in str(exc_info.value)
    assert "provider response body" not in str(exc_info.value)
    assert exc_info.value.__context__ is None
    assert client_options["proxy"] == "http://proxy.example.test:8080"


@pytest.mark.parametrize("api_key", ["", "  ", "skip", "changeme", "your-key"])
def test_verify_access_rejects_placeholder_without_provider_call(
    monkeypatch, api_key
):
    called = False

    def _client(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(e2b_service.httpx, "Client", _client)

    with pytest.raises(ValueError, match="E2B_API_KEY is not configured"):
        e2b_service.E2BService(api_key).verify_access()

    assert called is False


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_verify_access_rejects_unbounded_timeout_without_provider_call(
    monkeypatch, timeout
):
    called = False

    def _client(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(e2b_service.httpx, "Client", _client)

    with pytest.raises(
        ValueError,
        match="request_timeout_seconds must be positive and finite",
    ):
        e2b_service.E2BService("e2b-live").verify_access(
            request_timeout_seconds=timeout
        )

    assert called is False


def test_create_sandbox_uses_pinned_sdk_contract_without_redundant_touch(
    monkeypatch,
):
    calls: list[dict[str, object]] = []

    class _Sandbox:
        sandbox_id = "sandbox-created"

        def __init__(self, **kwargs):
            calls.append(kwargs)

        def set_timeout(self, _timeout):
            raise AssertionError("creation timeout is already applied by constructor")

    monkeypatch.setattr(e2b_service, "Sandbox", _Sandbox)
    service = e2b_service.E2BService("e2b-live", template="assessment-v1")
    service.sandbox_timeout_seconds = 900
    service.allow_internet_access = False

    sandbox = service.create_sandbox()

    assert sandbox.sandbox_id == "sandbox-created"
    assert calls == [
        {
            "api_key": "e2b-live",
            "template": "assessment-v1",
            "timeout": 900,
            "allow_internet_access": False,
        }
    ]


def test_connect_failure_never_exposes_sdk_detail(monkeypatch, caplog):
    calls: list[dict[str, object]] = []
    secret_marker = "e2b-provider-body-secret-must-not-escape"

    class _Sandbox:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            raise TypeError(secret_marker)

    monkeypatch.setattr(e2b_service, "Sandbox", _Sandbox)
    service = e2b_service.E2BService("e2b-live")

    with pytest.raises(e2b_service.E2BProviderError) as exc_info:
        service.connect_sandbox("sandbox-existing")

    assert str(exc_info.value) == "e2b_connect_sandbox:TypeError"
    assert exc_info.value.__context__ is None
    assert secret_marker not in str(exc_info.value)
    assert secret_marker not in caplog.text
    assert calls == [
        {"api_key": "e2b-live", "sandbox_id": "sandbox-existing"}
    ]


def test_execute_and_test_sdk_failures_return_only_safe_codes(caplog):
    secret_marker = "e2b-runtime-provider-secret-must-not-escape"

    class _Files:
        @staticmethod
        def write(*_args, **_kwargs):
            return None

    class _Sandbox:
        files = _Files()

        @staticmethod
        def run_code(*_args, **_kwargs):
            raise RuntimeError(secret_marker)

    service = e2b_service.E2BService("e2b-live")
    execute_result = service.execute_code(_Sandbox(), "print('ok')")
    test_result = service.run_tests(_Sandbox(), "def test_ok(): assert True")

    assert execute_result["error"] == "e2b_execute_code:RuntimeError"
    assert test_result["error"] == "e2b_run_tests:RuntimeError"
    assert secret_marker not in str(execute_result)
    assert secret_marker not in str(test_result)
    assert secret_marker not in caplog.text


def test_structured_execution_detail_is_returned_but_never_logged(caplog):
    execution_detail = "candidate program raised with private input"

    class _Logs:
        stdout = []
        stderr = []

    class _Execution:
        logs = _Logs()
        results = []
        error = type(
            "ExecutionError",
            (),
            {"name": "RuntimeError", "value": execution_detail},
        )()

    class _Files:
        @staticmethod
        def write(*_args, **_kwargs):
            return None

    class _Sandbox:
        files = _Files()

        @staticmethod
        def run_code(*_args, **_kwargs):
            return _Execution()

    service = e2b_service.E2BService("e2b-live")
    execute_result = service.execute_code(_Sandbox(), "raise RuntimeError()")
    test_result = service.run_tests(_Sandbox(), "def test_private(): assert False")

    assert execution_detail in execute_result["error"]
    assert execution_detail in test_result["error"]
    assert "Code execution produced an error" in caplog.text
    assert "Test execution produced an error" in caplog.text
    assert execution_detail not in caplog.text
