from __future__ import annotations

import pytest

from app.components.integrations.e2b import service as e2b_service_module


class _Sandbox:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.id = "sandbox-security-test"
        self.timeouts = []

    def set_timeout(self, timeout_seconds):
        self.timeouts.append(timeout_seconds)


def test_candidate_sandbox_egress_defaults_off(monkeypatch):
    created = []

    def create(**kwargs):
        sandbox = _Sandbox(**kwargs)
        created.append(sandbox)
        return sandbox

    monkeypatch.delenv("E2B_SANDBOX_ALLOW_INTERNET", raising=False)
    monkeypatch.setattr(e2b_service_module, "Sandbox", create)

    service = e2b_service_module.E2BService("e2b-test-key")
    sandbox = service.create_sandbox()

    assert service.allow_internet_access is False
    assert sandbox is created[0]
    assert created[0].kwargs["secure"] is True
    assert created[0].kwargs["allow_internet_access"] is False


def test_invalid_candidate_sandbox_egress_setting_fails_closed(monkeypatch):
    created = []

    def create(**kwargs):
        created.append(kwargs)
        return _Sandbox(**kwargs)

    monkeypatch.setenv("E2B_SANDBOX_ALLOW_INTERNET", "definitely")
    monkeypatch.setattr(e2b_service_module, "Sandbox", create)

    service = e2b_service_module.E2BService("e2b-test-key")
    service.create_sandbox()

    assert service.allow_internet_access is False
    assert created == [
        {
            "api_key": "e2b-test-key",
            "timeout": service.sandbox_timeout_seconds,
            "secure": True,
            "allow_internet_access": False,
        }
    ]


def test_candidate_sandbox_egress_cannot_be_enabled_by_environment(monkeypatch):
    created = []

    def create(**kwargs):
        created.append(kwargs)
        return _Sandbox(**kwargs)

    monkeypatch.setenv("E2B_SANDBOX_ALLOW_INTERNET", "true")
    monkeypatch.setattr(e2b_service_module, "Sandbox", create)

    service = e2b_service_module.E2BService("e2b-test-key")
    service.create_sandbox()

    assert service.allow_internet_access is False
    assert created[0]["allow_internet_access"] is False


def test_candidate_sandbox_creation_does_not_retry_without_egress_policy(monkeypatch):
    calls = []

    def unsupported_sdk(**kwargs):
        calls.append(kwargs)
        raise TypeError("unexpected keyword argument 'allow_internet_access'")

    monkeypatch.delenv("E2B_SANDBOX_ALLOW_INTERNET", raising=False)
    monkeypatch.setattr(e2b_service_module, "Sandbox", unsupported_sdk)

    service = e2b_service_module.E2BService("e2b-test-key")
    with pytest.raises(RuntimeError, match="cannot enforce the sandbox network policy"):
        service.create_sandbox()

    assert len(calls) == 1
    assert calls[0]["secure"] is True
    assert calls[0]["allow_internet_access"] is False


def test_reconnect_never_falls_back_to_a_replacement_sandbox(monkeypatch):
    calls = []

    def unsupported_reconnect(**kwargs):
        calls.append(kwargs)
        raise TypeError("unexpected keyword argument 'sandbox_id'")

    monkeypatch.setattr(e2b_service_module, "Sandbox", unsupported_reconnect)

    service = e2b_service_module.E2BService("e2b-test-key")
    with pytest.raises(RuntimeError, match="cannot safely reconnect"):
        service.connect_sandbox("original-candidate-sandbox")

    assert calls == [
        {
            "api_key": "e2b-test-key",
            "sandbox_id": "original-candidate-sandbox",
        }
    ]


def test_explicit_keepalive_propagates_timeout_renewal_failure():
    class ExpiredSandbox:
        def set_timeout(self, _timeout_seconds):
            raise RuntimeError("sandbox expired")

    service = e2b_service_module.E2BService("e2b-test-key")

    with pytest.raises(RuntimeError, match="sandbox expired"):
        service.touch_sandbox(ExpiredSandbox())
