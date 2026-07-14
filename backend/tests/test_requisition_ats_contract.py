"""Provider-neutral requisition publication contract."""

from __future__ import annotations

import pytest

from app.models.organization import Organization
from app.models.user import User
from app.services.role_brief_service import find_ref_code
from tests.conftest import auth_headers
from tests.test_api_requisition_job_bridge import _make_requisition, _publish


@pytest.mark.parametrize("provider", ["workable", "bullhorn"])
def test_publish_describes_the_active_ats_without_changing_native_intake(
    client, db, monkeypatch, provider
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    org = db.query(Organization).filter(Organization.id == user.organization_id).one()

    if provider == "workable":
        org.workable_connected = True
        org.workable_access_token = "tenant-token"
        org.workable_subdomain = "tenant"
    else:
        monkeypatch.setattr(
            "app.domains.identity_access.organization_serialization.settings.BULLHORN_ENABLED",
            True,
        )
        org.bullhorn_connected = True
        org.bullhorn_username = "api-user"
        org.bullhorn_client_id = "client-id"
        org.bullhorn_refresh_token = "encrypted-refresh"
    db.commit()

    brief_id = _make_requisition(client, headers, title="Provider-neutral role")
    published = _publish(
        client,
        headers,
        brief_id,
        jd="# Provider-neutral role\n\nRun the same autonomous hiring workflow.",
    )

    assert published["ats_provider"] == provider
    assert published["ats_spec"] == published["workable_spec"]
    assert find_ref_code(published["ats_spec"]) == published["ref_code"]

    # Publishing always creates the native preview first. ATS adoption happens
    # asynchronously when the provider sync sees the reference, so neither
    # connector is required for the public Taali job page to exist.
    assert published["url"].endswith(f"/job/{published['token']}")
    brief = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert brief["job"]["ats_provider"] is None
    assert brief["job"]["external_job_id"] is None


def test_publish_uses_workable_precedence_for_a_dual_connected_migration_edge(
    client, db, monkeypatch
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    org = db.query(Organization).filter(Organization.id == user.organization_id).one()
    monkeypatch.setattr(
        "app.domains.identity_access.organization_serialization.settings.BULLHORN_ENABLED",
        True,
    )
    org.workable_connected = True
    org.workable_access_token = "tenant-token"
    org.workable_subdomain = "tenant"
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_refresh_token = "encrypted-refresh"
    db.commit()

    published = _publish(
        client,
        headers,
        _make_requisition(client, headers, title="Migration edge"),
    )

    assert published["ats_provider"] == "workable"
