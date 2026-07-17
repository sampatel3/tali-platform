from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.orm import object_session

from app.models.organization import Organization
from app.platform.secrets import encrypt_text
from app.services import claude_client_resolver as resolver
from app.services.anthropic_workspace_auth import (
    WorkspaceAuthConfigurationError,
    build_workspace_wif_credentials,
    workspace_auth_enabled,
    workspace_auth_readiness,
    workspace_wif_configuration,
)


_ORG_UUID = "00000000-0000-4000-8000-000000000001"


def _set_valid_wif(monkeypatch, token_file: Path) -> None:
    values = {
        "ANTHROPIC_WORKSPACE_AUTH_ENABLED": True,
        "ANTHROPIC_WORKSPACE_WIF_ENABLED": True,
        "ANTHROPIC_FEDERATION_RULE_ID": "fdrl_test",
        "ANTHROPIC_ORGANIZATION_ID": _ORG_UUID,
        "ANTHROPIC_SERVICE_ACCOUNT_ID": "svac_test",
        "ANTHROPIC_IDENTITY_TOKEN_FILE": str(token_file),
        "ANTHROPIC_API_KEY": "sk-ant-shared",
    }
    for name, value in values.items():
        monkeypatch.setattr(resolver.settings, name, value)


def _org(*, org_id: int = 42, workspace_id: str = "wrkspc_test") -> Organization:
    return Organization(
        id=org_id,
        name="Workspace Auth",
        slug=f"workspace-auth-{org_id}",
        anthropic_workspace_id=workspace_id,
    )


def test_wif_uses_rotating_token_file_without_storing_token(tmp_path, monkeypatch):
    token_file = tmp_path / "identity.jwt"
    token_file.write_text("first-secret-jwt", encoding="utf-8")
    _set_valid_wif(monkeypatch, token_file)
    config = workspace_wif_configuration(_org(), settings_obj=resolver.settings)
    credentials = build_workspace_wif_credentials(config)

    assert credentials._identity_token_provider() == "first-secret-jwt"
    token_file.write_text("rotated-secret-jwt", encoding="utf-8")
    assert credentials._identity_token_provider() == "rotated-secret-jwt"
    assert "first-secret-jwt" not in repr(config)
    assert "rotated-secret-jwt" not in repr(config)
    assert "rotated-secret-jwt" not in repr(credentials)


def test_wif_client_is_workspace_scoped_and_preserves_metering(tmp_path, monkeypatch):
    token_file = tmp_path / "identity.jwt"
    token_file.write_text("secret-jwt", encoding="utf-8")
    _set_valid_wif(monkeypatch, token_file)
    captured = {}

    def _anthropic(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(messages=object())

    monkeypatch.setattr(resolver, "Anthropic", _anthropic)
    client = resolver.get_client_for_org(_org(org_id=77))

    credentials = captured["credentials"]
    assert credentials._federation_rule_id == "fdrl_test"
    assert credentials._organization_id == _ORG_UUID
    assert credentials._service_account_id == "svac_test"
    assert credentials._workspace_id == "wrkspc_test"
    assert client.organization_id == 77
    assert "api_key" not in captured


def test_explicit_key_client_uses_canonical_timeout_and_retry_bounds(monkeypatch):
    captured = {}
    inner = SimpleNamespace(messages=object())

    def _anthropic(**kwargs):
        captured.update(kwargs)
        return inner

    monkeypatch.setattr(resolver, "Anthropic", _anthropic)

    assert resolver.build_bounded_anthropic_client("sk-explicit") is inner
    assert captured["api_key"] == "sk-explicit"
    assert captured["timeout"] == resolver._REQUEST_TIMEOUT_SECONDS
    assert captured["max_retries"] == resolver._MAX_RETRIES
    assert captured["default_headers"]["anthropic-beta"] == (
        resolver._ANTHROPIC_BETA_HEADER
    )


def test_existing_encrypted_workspace_key_still_wins(monkeypatch):
    org = _org()
    org.anthropic_workspace_key_encrypted = encrypt_text(
        "sk-ant-existing", resolver.settings.SECRET_KEY
    )
    inner = SimpleNamespace(messages=object())

    with patch.object(resolver, "_build_inner_client", return_value=inner) as legacy, patch.object(
        resolver, "_build_workspace_wif_inner_client"
    ) as wif:
        client = resolver.get_client_for_org(org)

    legacy.assert_called_once_with("sk-ant-existing")
    wif.assert_not_called()
    assert client.organization_id == 42


def test_incomplete_wif_falls_back_to_shared_metered_auth(monkeypatch):
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_WORKSPACE_AUTH_ENABLED", True)
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_WORKSPACE_WIF_ENABLED", True)
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_FEDERATION_RULE_ID", "")
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_API_KEY", "shared-key")
    inner = SimpleNamespace(messages=object())

    with patch.object(resolver, "_build_inner_client", return_value=inner) as shared:
        client = resolver.get_client_for_org(_org())

    shared.assert_called_once_with("shared-key")
    assert client.organization_id == 42


def test_wif_failure_log_never_includes_provider_secret(monkeypatch, caplog):
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_WORKSPACE_AUTH_ENABLED", True)
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_API_KEY", "shared-key")
    secret = "jwt-that-must-never-be-logged"
    inner = SimpleNamespace(messages=object())
    caplog.set_level(logging.INFO)

    with patch.object(
        resolver,
        "_build_workspace_wif_inner_client",
        side_effect=RuntimeError(secret),
    ), patch.object(resolver, "_build_inner_client", return_value=inner):
        resolver.get_client_for_org(_org())

    assert secret not in caplog.text


def test_master_gate_off_never_constructs_wif(monkeypatch):
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_WORKSPACE_AUTH_ENABLED", False)
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_WORKSPACE_WIF_ENABLED", True)
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_API_KEY", "shared-key")
    inner = SimpleNamespace(messages=object())

    with patch.object(resolver, "_build_workspace_wif_inner_client") as wif, patch.object(
        resolver, "_build_inner_client", return_value=inner
    ) as shared:
        client = resolver.get_client_for_org(_org())

    wif.assert_not_called()
    shared.assert_called_once_with("shared-key")
    assert client.organization_id == 42


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("ANTHROPIC_FEDERATION_RULE_ID", "wrong", "must start with fdrl_"),
        ("ANTHROPIC_ORGANIZATION_ID", "not-a-uuid", "must be a UUID"),
        ("ANTHROPIC_SERVICE_ACCOUNT_ID", "wrong", "must start with svac_"),
    ],
)
def test_malformed_wif_ids_fail_closed(
    tmp_path, monkeypatch, setting, value, message
):
    token_file = tmp_path / "identity.jwt"
    token_file.write_text("secret-jwt", encoding="utf-8")
    _set_valid_wif(monkeypatch, token_file)
    monkeypatch.setattr(resolver.settings, setting, value)

    with pytest.raises(WorkspaceAuthConfigurationError, match=message):
        workspace_wif_configuration(_org(), settings_obj=resolver.settings)


def test_token_file_must_be_absolute_regular_readable_and_nonempty(
    tmp_path, monkeypatch
):
    token_file = tmp_path / "identity.jwt"
    token_file.write_text("secret-jwt", encoding="utf-8")
    _set_valid_wif(monkeypatch, token_file)

    monkeypatch.setattr(resolver.settings, "ANTHROPIC_IDENTITY_TOKEN_FILE", "relative.jwt")
    with pytest.raises(WorkspaceAuthConfigurationError, match="absolute path"):
        workspace_wif_configuration(_org(), settings_obj=resolver.settings)

    monkeypatch.setattr(
        resolver.settings,
        "ANTHROPIC_IDENTITY_TOKEN_FILE",
        str(tmp_path / "missing.jwt"),
    )
    with pytest.raises(WorkspaceAuthConfigurationError, match="unavailable"):
        workspace_wif_configuration(_org(), settings_obj=resolver.settings)

    monkeypatch.setattr(resolver.settings, "ANTHROPIC_IDENTITY_TOKEN_FILE", str(tmp_path))
    with pytest.raises(WorkspaceAuthConfigurationError, match="regular file"):
        workspace_wif_configuration(_org(), settings_obj=resolver.settings)

    token_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        resolver.settings, "ANTHROPIC_IDENTITY_TOKEN_FILE", str(token_file)
    )
    with pytest.raises(WorkspaceAuthConfigurationError, match="empty"):
        workspace_wif_configuration(_org(), settings_obj=resolver.settings)


def test_preferred_gate_overrides_legacy_name():
    assert workspace_auth_enabled(
        SimpleNamespace(
            ANTHROPIC_WORKSPACE_AUTH_ENABLED=False,
            ANTHROPIC_WORKSPACE_KEYS_ENABLED=True,
        )
    ) is False
    assert workspace_auth_enabled(
        SimpleNamespace(
            ANTHROPIC_WORKSPACE_AUTH_ENABLED=None,
            ANTHROPIC_WORKSPACE_KEYS_ENABLED=True,
        )
    ) is True


def test_enabled_auth_readiness_requires_exact_workspace_wif(tmp_path):
    token_file = tmp_path / "identity.jwt"
    token_file.write_text("secret-jwt", encoding="utf-8")
    settings_obj = SimpleNamespace(
        ANTHROPIC_WORKSPACE_AUTH_ENABLED=True,
        ANTHROPIC_WORKSPACE_KEYS_ENABLED=False,
        ANTHROPIC_WORKSPACE_WIF_ENABLED=True,
        ANTHROPIC_FEDERATION_RULE_ID="fdrl_test",
        ANTHROPIC_ORGANIZATION_ID=_ORG_UUID,
        ANTHROPIC_SERVICE_ACCOUNT_ID="svac_test",
        ANTHROPIC_IDENTITY_TOKEN_FILE=str(token_file),
    )

    assert workspace_auth_readiness(_org(), settings_obj=settings_obj) == (True, None)
    ready, reason = workspace_auth_readiness(
        _org(workspace_id=""), settings_obj=settings_obj
    )
    assert ready is False
    assert "wrkspc_" in str(reason)


def test_metered_resolver_detaches_org_before_auth_resolution(db, monkeypatch):
    org = Organization(name="Detached", slug=f"detached-{id(db)}")
    db.add(org)
    db.commit()
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_WORKSPACE_AUTH_ENABLED", True)
    observed = {}

    def _resolve(detached_org):
        observed["object_session"] = object_session(detached_org)
        return "per-org-client"

    with patch.object(resolver, "get_client_for_org", side_effect=_resolve), patch.object(
        resolver, "get_shared_client"
    ) as shared:
        result = resolver.get_metered_client(organization_id=int(org.id))

    assert result == "per-org-client"
    assert observed["object_session"] is None
    shared.assert_not_called()


def test_metered_fallback_log_redacts_resolution_exception(db, monkeypatch, caplog):
    org = Organization(name="Redacted", slug=f"redacted-{id(db)}")
    db.add(org)
    db.commit()
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_WORKSPACE_AUTH_ENABLED", True)
    secret = "provider-secret-that-must-not-log"
    caplog.set_level(logging.WARNING)

    with patch.object(
        resolver, "get_client_for_org", side_effect=RuntimeError(secret)
    ), patch.object(resolver, "get_shared_client", return_value="shared"):
        assert resolver.get_metered_client(organization_id=int(org.id)) == "shared"

    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text
