"""Per-org workspace provisioning is serialized + idempotent.

The race that produced duplicate ``taali-org-*`` workspaces: two concurrent
first-calls both see "no key" and each create a workspace. _provision_for_org_safe
now locks the org row and RE-CHECKS the key under the lock, so a second caller
finds the freshly-stored key and never provisions again.
"""
from __future__ import annotations

from app.models.organization import Organization
from app.platform.config import settings
from app.platform.secrets import encrypt_text
from app.services import claude_client_resolver as r


class _Provisioned:
    workspace_id = "wrkspc_new"
    api_key_plaintext = "sk-ant-new-key"


def test_skips_provision_when_key_already_present(db, monkeypatch):
    """The under-lock re-check: an org that already has a key must NOT
    provision again (this is what stops concurrent duplicates)."""
    org = Organization(name="HasKey", slug=f"haskey-{id(db)}")
    org.anthropic_workspace_id = "wrkspc_existing"
    org.anthropic_workspace_key_encrypted = encrypt_text(
        "sk-ant-existing", settings.SECRET_KEY
    )
    db.add(org)
    db.commit()

    monkeypatch.setattr(r, "admin_is_configured", lambda: True)

    def _must_not_provision(**_kw):
        raise AssertionError("provision_workspace_for_org must not be called")

    monkeypatch.setattr(r, "provision_workspace_for_org", _must_not_provision)

    assert r._provision_for_org_safe(org) == "sk-ant-existing"


def test_happy_path_persists_and_returns_key(db, monkeypatch):
    org = Organization(name="New", slug=f"new-{id(db)}")
    db.add(org)
    db.commit()

    monkeypatch.setattr(r, "admin_is_configured", lambda: True)
    monkeypatch.setattr(r, "provision_workspace_for_org", lambda **_kw: _Provisioned())

    assert r._provision_for_org_safe(org) == "sk-ant-new-key"
    db.refresh(org)
    assert org.anthropic_workspace_id == "wrkspc_new"
    assert org.anthropic_workspace_key_encrypted  # stored (encrypted)
    assert org.anthropic_workspace_provisioning_failed_at is None


def test_failure_stamps_and_returns_none(db, monkeypatch):
    org = Organization(name="Fail", slug=f"fail-{id(db)}")
    db.add(org)
    db.commit()

    monkeypatch.setattr(r, "admin_is_configured", lambda: True)

    def _boom(**_kw):
        raise RuntimeError("admin api down")

    monkeypatch.setattr(r, "provision_workspace_for_org", _boom)

    assert r._provision_for_org_safe(org) is None
    db.refresh(org)
    assert org.anthropic_workspace_provisioning_failed_at is not None
    assert org.anthropic_workspace_id is None


def test_no_provision_when_admin_unconfigured(db, monkeypatch):
    org = Organization(name="NoAdmin", slug=f"noadmin-{id(db)}")
    db.add(org)
    db.commit()
    monkeypatch.setattr(r, "admin_is_configured", lambda: False)
    assert r._provision_for_org_safe(org) is None
