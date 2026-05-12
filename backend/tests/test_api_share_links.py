"""API tests for the multi-link share contract (HANDOFF v2 §3).

Covers:
- POST  /api/v1/applications/{id}/share-links → create with mode + expiry
- GET   /api/v1/applications/{id}/share-links → list
- DELETE /api/v1/share-links/{id} → revoke
- GET   /share/{token} → public view, gated by expiry + view count
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from app.models.share_link import ShareLink
from tests.conftest import auth_headers, TestingSessionLocal


def _make_role_and_application(client, headers, candidate_email="share-link@example.com"):
    role_resp = client.post(
        "/api/v1/roles",
        json={"name": "Backend Engineer"},
        headers=headers,
    )
    assert role_resp.status_code == 201, role_resp.text
    role = role_resp.json()
    # The create-application route requires a job spec on file before
    # accepting applications, so upload a placeholder spec first.
    job_spec_file = {
        "file": ("job-spec.txt", io.BytesIO(b"Backend role requirements"), "text/plain"),
    }
    spec_resp = client.post(
        f"/api/v1/roles/{role['id']}/upload-job-spec",
        files=job_spec_file,
        headers=headers,
    )
    assert spec_resp.status_code == 200, spec_resp.text
    app_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={
            "candidate_email": candidate_email,
            "candidate_name": "Share Link",
            "candidate_position": "Engineer",
        },
        headers=headers,
    )
    assert app_resp.status_code == 201, app_resp.text
    return role, app_resp.json()


def test_create_list_revoke_share_link(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "7d"},
        headers=headers,
    )
    assert create.status_code == 200, create.text
    link = create.json()
    assert link["mode"] == "client"
    assert link["expiry_preset"] == "7d"
    assert link["active"] is True
    assert link["revoked"] is False
    assert link["expired"] is False
    assert link["token"].startswith("shr_")
    assert link["expires_at"]

    listing = client.get(
        f"/api/v1/applications/{application['id']}/share-links",
        headers=headers,
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert len(body["links"]) == 1
    assert body["links"][0]["id"] == link["id"]

    # Mint a second link in a different mode + expiry to confirm
    # multiple active links per application is the new contract.
    second = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "recruiter", "expiry": "24h"},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    listing2 = client.get(
        f"/api/v1/applications/{application['id']}/share-links",
        headers=headers,
    )
    assert listing2.status_code == 200
    assert len(listing2.json()["links"]) == 2

    # Revoke the first link → it stays in the list but flips to
    # revoked / inactive so the report footer can render audit history.
    revoke = client.delete(
        f"/api/v1/share-links/{link['id']}",
        headers=headers,
    )
    assert revoke.status_code == 200, revoke.text
    revoked_payload = revoke.json()
    assert revoked_payload["revoked"] is True
    assert revoked_payload["active"] is False

    listing3 = client.get(
        f"/api/v1/applications/{application['id']}/share-links",
        headers=headers,
    )
    assert listing3.status_code == 200
    by_id = {row["id"]: row for row in listing3.json()["links"]}
    assert by_id[link["id"]]["revoked"] is True
    assert by_id[second.json()["id"]]["revoked"] is False


def test_create_share_link_rejects_invalid_mode_or_expiry(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    bad_mode = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "internal", "expiry": "7d"},
        headers=headers,
    )
    assert bad_mode.status_code == 400

    bad_expiry = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "1y"},
        headers=headers,
    )
    assert bad_expiry.status_code == 400

    # ``expiry_days`` rejects out-of-range values (1-30 only).
    bad_days = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry_days": 0},
        headers=headers,
    )
    assert bad_days.status_code == 422
    too_long = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry_days": 60},
        headers=headers,
    )
    assert too_long.status_code == 422


def test_create_share_link_with_custom_expiry_days(client):
    """``expiry_days`` (1-30) is the new custom-expiry path used by the
    client-share dialog. Stores ``expiry_preset = '<N>d'`` for display.
    """
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry_days": 3},
        headers=headers,
    )
    assert create.status_code == 200, create.text
    link = create.json()
    assert link["expiry_preset"] == "3d"
    assert link["active"] is True

    # ``expiry_days`` wins over ``expiry`` when both are sent.
    both = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "recruiter", "expiry": "30d", "expiry_days": 7},
        headers=headers,
    )
    assert both.status_code == 200, both.text
    assert both.json()["expiry_preset"] == "7d"


def test_public_share_view_short_circuits_single_view_after_first_get(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "single-view", "expiry": "single-view"},
        headers=headers,
    )
    assert create.status_code == 200, create.text
    token = create.json()["token"]

    first = client.get(f"/share/{token}")
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["mode"] == "single-view"
    assert payload["application_id"] == application["id"]

    # Second GET against a single-view link returns 410 Gone.
    second = client.get(f"/share/{token}")
    assert second.status_code == 410


def test_public_share_view_rejects_revoked_and_expired_links(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "30d"},
        headers=headers,
    )
    assert create.status_code == 200
    link_id = create.json()["id"]
    token = create.json()["token"]

    revoke = client.delete(
        f"/api/v1/share-links/{link_id}",
        headers=headers,
    )
    assert revoke.status_code == 200
    revoked_view = client.get(f"/share/{token}")
    assert revoked_view.status_code == 410

    # Manually expire a fresh link in DB and confirm 410.
    fresh = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "24h"},
        headers=headers,
    )
    assert fresh.status_code == 200
    fresh_token = fresh.json()["token"]
    fresh_id = fresh.json()["id"]
    db = TestingSessionLocal()
    try:
        link = db.query(ShareLink).filter(ShareLink.id == fresh_id).first()
        assert link is not None
        link.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    expired_view = client.get(f"/share/{fresh_token}")
    assert expired_view.status_code == 410


def test_public_share_view_bumps_view_count(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "30d"},
        headers=headers,
    )
    assert create.status_code == 200
    token = create.json()["token"]

    for _ in range(3):
        view = client.get(f"/share/{token}")
        assert view.status_code == 200

    listing = client.get(
        f"/api/v1/applications/{application['id']}/share-links",
        headers=headers,
    )
    assert listing.status_code == 200
    row = listing.json()["links"][0]
    assert row["view_count"] == 3
    assert row["last_viewed_at"] is not None


def test_share_links_resolve_via_legacy_applications_share_route(client):
    """A token minted via the multi-link contract must resolve through
    the same ``GET /api/v1/applications/share/{token}`` route the SPA
    already uses for the legacy single-link tokens. The mode on the
    ``share_links`` row dictates whether the response is client-safe,
    regardless of the URL ``?view=`` param.
    """
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    client_link = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry_days": 7},
        headers=headers,
    )
    assert client_link.status_code == 200, client_link.text
    client_token = client_link.json()["token"]

    # Even when the URL asks for ``view=interview``, a client-mode token
    # is forced into the client-safe payload.
    forced_client = client.get(
        f"/api/v1/applications/share/{client_token}?view=interview",
    )
    assert forced_client.status_code == 200, forced_client.text
    assert forced_client.json()["id"] == application["id"]

    recruiter_link = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "recruiter", "expiry_days": 7},
        headers=headers,
    )
    assert recruiter_link.status_code == 200, recruiter_link.text
    recruiter_token = recruiter_link.json()["token"]

    recruiter_view = client.get(
        f"/api/v1/applications/share/{recruiter_token}",
    )
    assert recruiter_view.status_code == 200, recruiter_view.text

    # Each resolve bumps view_count on the share_links row so the
    # recruiter-facing UI can show 'seen' state.
    listing = client.get(
        f"/api/v1/applications/{application['id']}/share-links",
        headers=headers,
    )
    by_token = {row["token"]: row for row in listing.json()["links"]}
    assert by_token[client_token]["view_count"] == 1
    assert by_token[recruiter_token]["view_count"] == 1


def test_share_links_legacy_route_rejects_revoked_and_expired_tokens(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry_days": 7},
        headers=headers,
    )
    link_id = create.json()["id"]
    token = create.json()["token"]

    client.delete(f"/api/v1/share-links/{link_id}", headers=headers)
    revoked = client.get(f"/api/v1/applications/share/{token}")
    assert revoked.status_code == 410

    fresh = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry_days": 1},
        headers=headers,
    )
    fresh_id = fresh.json()["id"]
    fresh_token = fresh.json()["token"]
    db = TestingSessionLocal()
    try:
        link = db.query(ShareLink).filter(ShareLink.id == fresh_id).first()
        link.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    expired = client.get(f"/api/v1/applications/share/{fresh_token}")
    assert expired.status_code == 410


def test_share_links_are_org_scoped(client):
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")

    _, application_a = _make_role_and_application(client, headers_a)

    # Org A mints a link.
    create = client.post(
        f"/api/v1/applications/{application_a['id']}/share-links",
        json={"mode": "client", "expiry": "7d"},
        headers=headers_a,
    )
    assert create.status_code == 200
    link_id = create.json()["id"]

    # Org B cannot see, list, or revoke.
    list_b = client.get(
        f"/api/v1/applications/{application_a['id']}/share-links",
        headers=headers_b,
    )
    assert list_b.status_code == 404

    revoke_b = client.delete(
        f"/api/v1/share-links/{link_id}",
        headers=headers_b,
    )
    assert revoke_b.status_code == 404
