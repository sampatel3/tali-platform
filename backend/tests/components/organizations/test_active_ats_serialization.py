"""``active_ats`` mirrors the ATS resolver's precedence exactly.

The unified Integrations settings surface reads ``OrgResponse.active_ats`` to
label which ATS the org is on. That label must never disagree with what
``resolve_ats_provider`` actually dispatches reads/writes to, so
``resolve_active_ats`` is kept in lock-step with the resolver's connection
checks (Workable wins; Bullhorn only when ``BULLHORN_ENABLED`` and connected;
else standalone).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.domains.identity_access import organization_serialization as ser
from app.domains.identity_access.organization_serialization import resolve_active_ats


def _org(**overrides):
    base = dict(
        workable_connected=False,
        workable_access_token=None,
        workable_subdomain=None,
        bullhorn_connected=False,
        bullhorn_client_id=None,
        bullhorn_refresh_token=None,
        bullhorn_username=None,
        sync_mode="standalone",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_workable_connected_org_is_active_ats_workable():
    org = _org(
        workable_connected=True,
        workable_access_token="tok",
        workable_subdomain="acme",
    )
    assert resolve_active_ats(org) == "workable"


def test_unconnected_org_is_standalone():
    assert resolve_active_ats(_org()) == "standalone"


def test_workable_flagged_connected_but_missing_token_is_standalone():
    # Mirrors the resolver: connection requires token + subdomain, not just the
    # boolean flag.
    org = _org(workable_connected=True, workable_access_token=None, workable_subdomain="acme")
    assert resolve_active_ats(org) == "standalone"


def test_bullhorn_connected_org_is_active_ats_bullhorn_when_flag_on(monkeypatch):
    monkeypatch.setattr(ser.settings, "BULLHORN_ENABLED", True)
    org = _org(
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rtok",
        bullhorn_username="taali.api",
    )
    assert resolve_active_ats(org) == "bullhorn"


def test_bullhorn_connected_org_is_standalone_when_flag_off(monkeypatch):
    monkeypatch.setattr(ser.settings, "BULLHORN_ENABLED", False)
    org = _org(
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rtok",
        bullhorn_username="taali.api",
    )
    assert resolve_active_ats(org) == "standalone"


def test_workable_wins_over_bullhorn_when_both_connected(monkeypatch):
    monkeypatch.setattr(ser.settings, "BULLHORN_ENABLED", True)
    org = _org(
        workable_connected=True,
        workable_access_token="tok",
        workable_subdomain="acme",
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rtok",
        bullhorn_username="taali.api",
    )
    assert resolve_active_ats(org) == "workable"
