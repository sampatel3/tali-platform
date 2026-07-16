from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.components.integrations.workable.service import WorkableService
from app.components.integrations.workable.url_security import (
    validate_workable_callback_url,
)
from app.domains.identity_access.organization_routes import (
    _mint_workable_oauth_state,
    _verify_workable_oauth_state,
)
from app.platform.admin_auth import verify_admin_secret
from app.platform.config import settings
from app.platform.secrets import (
    decrypt_integration_secret,
    encrypt_integration_secret,
)


def test_workable_oauth_state_is_bound_to_user_and_organization():
    user = SimpleNamespace(id=11)
    org = SimpleNamespace(id=22)
    state = _mint_workable_oauth_state(user, org)

    _verify_workable_oauth_state(state, user, org)
    with pytest.raises(HTTPException) as exc:
        _verify_workable_oauth_state(state, SimpleNamespace(id=12), org)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        _verify_workable_oauth_state(state, user, SimpleNamespace(id=23))


@pytest.mark.parametrize(
    "url",
    (
        "http://acme.workable.com/callback",
        "https://evil.example/callback",
        "https://acme.workable.com:8443/callback",
        "https://user:password@acme.workable.com/callback",
    ),
)
def test_workable_callback_rejects_unsafe_origins(url):
    with pytest.raises(ValueError):
        validate_workable_callback_url(url)


def test_workable_pagination_cannot_change_origin():
    service = WorkableService("legacy-plaintext-token", "acme")
    with pytest.raises(ValueError):
        service._get_next_page("https://attacker.example/spi/v3/jobs?page=2")


def test_integration_key_rotation_reads_previous_ciphertext(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_ENCRYPTION_KEY", "first-key")
    monkeypatch.setattr(settings, "INTEGRATION_ENCRYPTION_KEY_PREVIOUS", "")
    ciphertext = encrypt_integration_secret("provider-secret")
    assert "provider-secret" not in ciphertext

    monkeypatch.setattr(settings, "INTEGRATION_ENCRYPTION_KEY", "second-key")
    monkeypatch.setattr(settings, "INTEGRATION_ENCRYPTION_KEY_PREVIOUS", "first-key")
    assert decrypt_integration_secret(ciphertext) == "provider-secret"


def test_admin_secret_is_dedicated_and_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "jwt-key")
    monkeypatch.setattr(settings, "ADMIN_SECRET", "operator-key")
    with pytest.raises(HTTPException):
        verify_admin_secret("jwt-key")
    verify_admin_secret("operator-key")

    monkeypatch.setattr(settings, "ADMIN_SECRET", "")
    with pytest.raises(HTTPException):
        verify_admin_secret("")


def test_graphiti_admin_probe_does_not_return_provider_diagnostics(
    client, monkeypatch
):
    from app.candidate_graph import client as graph_client

    monkeypatch.setattr(settings, "ADMIN_SECRET", "operator-key")
    monkeypatch.setattr(graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        graph_client,
        "get_graphiti",
        lambda: (_ for _ in ()).throw(
            RuntimeError("neo4j+s://internal-user:secret@private-host")
        ),
    )

    response = client.post(
        "/admin/graphiti/test-episode",
        headers={"X-Admin-Secret": "operator-key"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Graphiti test episode failed; see server logs."
    assert "private-host" not in response.text
    assert "secret" not in response.text


def test_graphiti_search_debug_does_not_return_provider_diagnostics(
    client, monkeypatch
):
    from app.candidate_graph import client as graph_client

    monkeypatch.setattr(settings, "ADMIN_SECRET", "operator-key")
    monkeypatch.setattr(graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        graph_client,
        "get_graphiti",
        lambda: (_ for _ in ()).throw(
            RuntimeError("neo4j+s://internal-user:secret@private-host")
        ),
    )

    response = client.get(
        "/admin/graphiti/search-debug",
        headers={"X-Admin-Secret": "operator-key"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Graph search is temporarily unavailable"
    assert "private-host" not in response.text
    assert "secret" not in response.text


def test_graphiti_cypher_debug_does_not_return_provider_diagnostics(
    client, monkeypatch
):
    from app.candidate_graph import client as graph_client

    monkeypatch.setattr(settings, "ADMIN_SECRET", "operator-key")
    monkeypatch.setattr(graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        graph_client,
        "get_graphiti",
        lambda: (_ for _ in ()).throw(
            RuntimeError("neo4j+s://internal-user:secret@private-host")
        ),
    )

    response = client.get(
        "/admin/graphiti/cypher-debug",
        headers={"X-Admin-Secret": "operator-key"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Graph database is temporarily unavailable"
    assert "private-host" not in response.text
    assert "secret" not in response.text
