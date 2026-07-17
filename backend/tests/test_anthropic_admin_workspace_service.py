from __future__ import annotations

import pytest

from app.components.integrations.anthropic_admin import service


class _Response:
    def __init__(self, *, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    def post(self, *_args, **_kwargs):
        raise AssertionError("workspace lookup must never call a POST endpoint")


def _install_client(monkeypatch, response):
    client = _Client(response)
    monkeypatch.setattr(service.httpx, "Client", lambda **_kwargs: client)
    monkeypatch.setattr(
        service.settings, "ANTHROPIC_ADMIN_API_KEY", "admin-secret-for-test"
    )
    return client


def test_compatibility_provision_reuses_exact_workspace_via_get_only(monkeypatch):
    client = _install_client(
        monkeypatch,
        _Response(
            payload={
                "data": [
                    {"id": "wrkspc_other", "name": "other"},
                    {"id": "wrkspc_exact", "name": "taali-org-acme-12"},
                ]
            }
        ),
    )

    result = service.provision_workspace_for_org(org_id=12, org_slug="acme")

    assert result.workspace_id == "wrkspc_exact"
    assert result.api_key_plaintext is None
    assert [method for method, *_rest in client.calls] == ["GET"]
    assert client.calls[0][1].endswith("/v1/organizations/workspaces")
    assert client.calls[0][2]["params"]["include_archived"] == "false"


def test_missing_or_duplicate_workspace_never_creates_one(monkeypatch):
    client = _install_client(monkeypatch, _Response(payload={"data": []}))
    with pytest.raises(service.AnthropicAdminError, match="not preconfigured"):
        service.provision_workspace_for_org(org_id=12, org_slug="acme")
    assert [method for method, *_rest in client.calls] == ["GET"]

    client.response = _Response(
        payload={
            "data": [
                {"id": "wrkspc_one", "name": "taali-org-acme-12"},
                {"id": "wrkspc_two", "name": "taali-org-acme-12"},
            ]
        }
    )
    with pytest.raises(service.AnthropicAdminError, match="duplicate"):
        service.provision_workspace_for_org(org_id=12, org_slug="acme")


def test_provider_errors_redact_response_body_and_admin_key(monkeypatch):
    secret_body = "provider-body-secret"
    _install_client(
        monkeypatch,
        _Response(status_code=503, payload={}, text=secret_body),
    )

    with pytest.raises(service.AnthropicAdminError) as captured:
        service.provision_workspace_for_org(org_id=12, org_slug="acme")

    message = str(captured.value)
    assert "503" in message
    assert secret_body not in message
    assert "admin-secret-for-test" not in message
