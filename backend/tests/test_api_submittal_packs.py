"""API tests for curated multi-candidate client submittal packs (WS2).

Covers:
- POST   /api/v1/roles/{id}/submittal-packs → mint (frozen snapshot)
- GET    /api/v1/roles/{id}/submittal-packs → audit list
- DELETE /api/v1/submittal-packs/{id} → revoke
- GET    /submittal/{token} → public view, gated by expiry + revoke
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from app.models.submittal_pack import SubmittalPack
from tests.conftest import auth_headers, TestingSessionLocal


def _make_role(client, headers, role_name="Backend Engineer"):
    role_resp = client.post(
        "/api/v1/roles",
        json={"name": role_name},
        headers=headers,
    )
    assert role_resp.status_code == 201, role_resp.text
    role = role_resp.json()
    job_spec_file = {
        "file": ("job-spec.txt", io.BytesIO(b"Backend role requirements"), "text/plain"),
    }
    spec_resp = client.post(
        f"/api/v1/roles/{role['id']}/upload-job-spec",
        files=job_spec_file,
        headers=headers,
    )
    assert spec_resp.status_code == 200, spec_resp.text
    return role


def _add_application(client, headers, role_id, email, name="Candidate One"):
    app_resp = client.post(
        f"/api/v1/roles/{role_id}/applications",
        json={
            "candidate_email": email,
            "candidate_name": name,
            "candidate_position": "Engineer",
        },
        headers=headers,
    )
    assert app_resp.status_code == 201, app_resp.text
    return app_resp.json()


def test_mint_happy_path_snapshot_shape(client):
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    a1 = _add_application(client, headers, role["id"], "one@example.com", "Alice One")
    a2 = _add_application(client, headers, role["id"], "two@example.com", "Bob Two")

    create = client.post(
        f"/api/v1/roles/{role['id']}/submittal-packs",
        json={
            "application_ids": [a2["id"], a1["id"]],
            "title": "Shortlist for client",
            "notes": {str(a2["id"]): "Strongest systems-design signal."},
            "expires_in": "7d",
        },
        headers=headers,
    )
    assert create.status_code == 200, create.text
    body = create.json()
    assert body["token"].startswith("sub_")
    assert body["url_path"] == f"/submittal/{body['token']}"
    assert body["expires_at"]
    assert body["id"]

    # Public roundtrip returns the frozen snapshot verbatim, ordered as
    # submitted (a2 first, then a1).
    view = client.get(body["url_path"])
    assert view.status_code == 200, view.text
    pub = view.json()
    assert pub["title"] == "Shortlist for client"
    assert pub["role"]["title"] == "Backend Engineer"
    assert "organization" in pub
    cands = pub["candidates"]
    assert len(cands) == 2
    assert cands[0]["candidate_name"] == "Bob Two"
    assert cands[0]["note"] == "Strongest systems-design signal."
    assert cands[1]["candidate_name"] == "Alice One"
    assert cands[1]["note"] is None
    # Client-safe header fields present.
    for c in cands:
        assert "verdict" in c
        assert "verdict_band" in c
        assert "highlights" in c
        assert isinstance(c["client_share_summary"], dict)


def test_snapshot_contains_no_stripped_internals(client):
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    a1 = _add_application(client, headers, role["id"], "internal@example.com")

    create = client.post(
        f"/api/v1/roles/{role['id']}/submittal-packs",
        json={"application_ids": [a1["id"]]},
        headers=headers,
    )
    assert create.status_code == 200, create.text
    view = client.get(create.json()["url_path"])
    assert view.status_code == 200
    entry = view.json()["candidates"][0]
    # Recruiter-internal fields stripped by application_detail_payload
    # (client_safe=True) must never appear in the frozen entry.
    for banned in (
        "candidate_interview_kit",
        "notes",
        "recruiter_notes",
        "interview_feedback",
        "workable_comments",
        "screening_pack",
        "tech_interview_pack",
    ):
        assert banned not in entry, f"leaked internal field: {banned}"


def test_mint_rejects_more_than_twenty(client):
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    ids = list(range(1, 22))  # 21 ids — over the cap; count check trips first
    resp = client.post(
        f"/api/v1/roles/{role['id']}/submittal-packs",
        json={"application_ids": ids},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text


def test_mint_rejects_empty_and_bad_expiry(client):
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    a1 = _add_application(client, headers, role["id"], "e@example.com")

    empty = client.post(
        f"/api/v1/roles/{role['id']}/submittal-packs",
        json={"application_ids": []},
        headers=headers,
    )
    assert empty.status_code == 400

    bad_expiry = client.post(
        f"/api/v1/roles/{role['id']}/submittal-packs",
        json={"application_ids": [a1["id"]], "expires_in": "1y"},
        headers=headers,
    )
    assert bad_expiry.status_code == 400


def test_mint_rejects_foreign_role_application(client):
    headers, _ = auth_headers(client)
    role_a = _make_role(client, headers, role_name="Role A")
    role_b = _make_role(client, headers, role_name="Role B")
    a_in_b = _add_application(client, headers, role_b["id"], "wrongrole@example.com")

    # Application belongs to role B — minting for role A must 404.
    resp = client.post(
        f"/api/v1/roles/{role_a['id']}/submittal-packs",
        json={"application_ids": [a_in_b["id"]]},
        headers=headers,
    )
    assert resp.status_code == 404, resp.text


def test_mint_rejects_cross_org_application(client):
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")
    role_a = _make_role(client, headers_a, role_name="OrgA Role")
    role_b = _make_role(client, headers_b, role_name="OrgB Role")
    app_b = _add_application(client, headers_b, role_b["id"], "orgb@example.com")

    # Org A cannot reference Org B's application, even against Org A's own role.
    resp = client.post(
        f"/api/v1/roles/{role_a['id']}/submittal-packs",
        json={"application_ids": [app_b["id"]]},
        headers=headers_a,
    )
    assert resp.status_code == 404, resp.text


def test_public_view_bumps_view_count_and_list(client):
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    a1 = _add_application(client, headers, role["id"], "count@example.com")

    create = client.post(
        f"/api/v1/roles/{role['id']}/submittal-packs",
        json={"application_ids": [a1["id"]], "expires_in": "30d"},
        headers=headers,
    )
    assert create.status_code == 200
    url_path = create.json()["url_path"]

    for _ in range(3):
        assert client.get(url_path).status_code == 200

    listing = client.get(
        f"/api/v1/roles/{role['id']}/submittal-packs",
        headers=headers,
    )
    assert listing.status_code == 200, listing.text
    rows = listing.json()["packs"]
    assert len(rows) == 1
    row = rows[0]
    assert row["view_count"] == 3
    assert row["last_viewed_at"] is not None
    assert row["candidate_count"] == 1
    assert row["active"] is True
    assert row["revoked"] is False


def test_revoke_blocks_public_view(client):
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    a1 = _add_application(client, headers, role["id"], "revoke@example.com")

    create = client.post(
        f"/api/v1/roles/{role['id']}/submittal-packs",
        json={"application_ids": [a1["id"]]},
        headers=headers,
    )
    assert create.status_code == 200
    pack_id = create.json()["id"]
    url_path = create.json()["url_path"]

    assert client.get(url_path).status_code == 200

    revoke = client.delete(
        f"/api/v1/submittal-packs/{pack_id}",
        headers=headers,
    )
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["revoked"] is True
    assert revoke.json()["active"] is False

    assert client.get(url_path).status_code == 410


def test_expired_pack_returns_410(client):
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    a1 = _add_application(client, headers, role["id"], "expire@example.com")

    create = client.post(
        f"/api/v1/roles/{role['id']}/submittal-packs",
        json={"application_ids": [a1["id"]], "expires_in": "24h"},
        headers=headers,
    )
    assert create.status_code == 200
    pack_id = create.json()["id"]
    url_path = create.json()["url_path"]

    db = TestingSessionLocal()
    try:
        pack = db.query(SubmittalPack).filter(SubmittalPack.id == pack_id).first()
        assert pack is not None
        pack.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    assert client.get(url_path).status_code == 410


def test_missing_token_returns_404(client):
    resp = client.get("/submittal/sub_does_not_exist")
    assert resp.status_code == 404


def test_packs_are_org_scoped(client):
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")
    role_a = _make_role(client, headers_a, role_name="OrgA Role")
    app_a = _add_application(client, headers_a, role_a["id"], "scoped@example.com")

    create = client.post(
        f"/api/v1/roles/{role_a['id']}/submittal-packs",
        json={"application_ids": [app_a["id"]]},
        headers=headers_a,
    )
    assert create.status_code == 200
    pack_id = create.json()["id"]

    # Org B cannot list (role 404) or revoke (pack 404).
    list_b = client.get(
        f"/api/v1/roles/{role_a['id']}/submittal-packs",
        headers=headers_b,
    )
    assert list_b.status_code == 404

    revoke_b = client.delete(
        f"/api/v1/submittal-packs/{pack_id}",
        headers=headers_b,
    )
    assert revoke_b.status_code == 404
