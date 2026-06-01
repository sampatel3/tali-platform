"""Gated per-org Anthropic workspace-key routing.

``get_metered_client`` is the single entry point. The ANTHROPIC_WORKSPACE_KEYS_
ENABLED flag gates whether a billable call routes through the org's own
workspace key (true per-org reconciliation) or the shared Taali key. OFF is the
default and must be byte-equivalent to the old get_shared_client behaviour.
"""
from __future__ import annotations

from unittest.mock import patch

from app.services import claude_client_resolver as r


def test_flag_off_uses_shared_client_even_with_org(monkeypatch):
    monkeypatch.setattr(r.settings, "ANTHROPIC_WORKSPACE_KEYS_ENABLED", False)
    with patch.object(r, "get_shared_client") as shared, patch.object(
        r, "get_client_for_org"
    ) as per_org:
        r.get_metered_client(organization_id=42)
    shared.assert_called_once_with(organization_id=42)
    per_org.assert_not_called()  # never touches the per-org path / provisioning


def test_flag_off_no_org_uses_shared(monkeypatch):
    monkeypatch.setattr(r.settings, "ANTHROPIC_WORKSPACE_KEYS_ENABLED", False)
    with patch.object(r, "get_shared_client") as shared:
        r.get_metered_client(organization_id=None)
    shared.assert_called_once_with(organization_id=None)


def test_flag_on_with_org_routes_per_org(monkeypatch, db):
    from app.models.organization import Organization

    org = Organization(name="Routed", slug=f"routed-{id(db)}")
    db.add(org)
    db.commit()

    monkeypatch.setattr(r.settings, "ANTHROPIC_WORKSPACE_KEYS_ENABLED", True)
    # SessionLocal inside get_metered_client must find the org — point it at
    # the test session's bind.
    with patch.object(r, "get_client_for_org") as per_org, patch.object(
        r, "get_shared_client"
    ) as shared:
        r.get_metered_client(organization_id=int(org.id))
    per_org.assert_called_once()
    assert int(per_org.call_args.args[0].id) == int(org.id)
    shared.assert_not_called()


def test_flag_on_no_org_falls_back_to_shared(monkeypatch):
    monkeypatch.setattr(r.settings, "ANTHROPIC_WORKSPACE_KEYS_ENABLED", True)
    with patch.object(r, "get_shared_client") as shared, patch.object(
        r, "get_client_for_org"
    ) as per_org:
        r.get_metered_client(organization_id=None)
    shared.assert_called_once_with(organization_id=None)
    per_org.assert_not_called()


def test_flag_on_unknown_org_falls_back_to_shared(monkeypatch):
    monkeypatch.setattr(r.settings, "ANTHROPIC_WORKSPACE_KEYS_ENABLED", True)
    with patch.object(r, "get_shared_client") as shared, patch.object(
        r, "get_client_for_org"
    ) as per_org:
        r.get_metered_client(organization_id=999_999_999)  # no such org
    shared.assert_called_once_with(organization_id=999_999_999)
    per_org.assert_not_called()
