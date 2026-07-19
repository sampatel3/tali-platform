"""Legacy workspace credentials stay reusable without remote provisioning."""

from __future__ import annotations

from unittest.mock import patch

from app.models.organization import Organization
from app.platform.config import settings
from app.platform.secrets import encrypt_text
from app.services import claude_client_resolver as r


def test_compatibility_recheck_returns_existing_encrypted_key(db):
    org = Organization(name="HasKey", slug=f"haskey-{id(db)}")
    org.anthropic_workspace_id = "wrkspc_existing"
    org.anthropic_workspace_key_encrypted = encrypt_text(
        "sk-ant-existing", settings.SECRET_KEY
    )
    db.add(org)
    db.commit()

    assert r._provision_for_org_safe(org) == "sk-ant-existing"


def test_compatibility_recheck_never_calls_admin_api(db):
    org = Organization(name="NoKey", slug=f"nokey-{id(db)}")
    db.add(org)
    db.commit()

    with patch(
        "app.components.integrations.anthropic_admin.service.provision_workspace_for_org"
    ) as provision:
        assert r._provision_for_org_safe(org) is None

    provision.assert_not_called()
    db.refresh(org)
    assert org.anthropic_workspace_id is None
    assert org.anthropic_workspace_key_encrypted is None
    assert org.anthropic_workspace_provisioning_failed_at is None


def test_malformed_stored_ciphertext_fails_closed_without_leaking(db, caplog):
    org = Organization(name="BadKey", slug=f"badkey-{id(db)}")
    org.anthropic_workspace_id = "wrkspc_existing"
    org.anthropic_workspace_key_encrypted = "secret-ciphertext-that-must-not-log"
    db.add(org)
    db.commit()

    assert r._provision_for_org_safe(org) is None
    assert "secret-ciphertext-that-must-not-log" not in caplog.text


def test_compatibility_result_redacts_optional_plaintext():
    from app.components.integrations.anthropic_admin.service import ProvisionedWorkspace

    result = ProvisionedWorkspace(
        workspace_id="wrkspc_existing",
        api_key_plaintext="sk-ant-must-not-appear",
    )

    assert "sk-ant-must-not-appear" not in repr(result)
