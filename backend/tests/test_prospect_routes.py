"""Tests for prospect CRUD + CSV import.

Covers create/dup/candidate-linking, CSV happy path + dupes + invalid rows +
row cap, list-with-suppression-flags, and org isolation on every route.
"""

from __future__ import annotations

import io

from tests.conftest import auth_headers, create_candidate_via_api


def _csv_bytes(text: str) -> tuple[str, io.BytesIO, str]:
    return ("prospects.csv", io.BytesIO(text.encode("utf-8")), "text/csv")


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_prospect(client):
    headers, _ = auth_headers(client)
    resp = client.post(
        "/api/v1/prospects",
        json={"full_name": "Jane Doe", "email": "Jane@Example.com", "position": "SWE"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == "jane@example.com"  # normalized
    assert body["full_name"] == "Jane Doe"
    assert body["status"] == "new"
    assert body["source_name"] == "manual"
    assert body["suppressed"] is None


def test_create_duplicate_409(client):
    headers, _ = auth_headers(client)
    payload = {"full_name": "Jane", "email": "dup@example.com"}
    assert client.post("/api/v1/prospects", json=payload, headers=headers).status_code == 200
    resp = client.post("/api/v1/prospects", json=payload, headers=headers)
    assert resp.status_code == 409


def test_create_links_to_existing_candidate(client):
    headers, _ = auth_headers(client)
    cand = create_candidate_via_api(client, headers, email="match@example.com").json()
    resp = client.post(
        "/api/v1/prospects",
        json={"full_name": "Match Person", "email": "Match@example.com"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["candidate_id"] == cand["id"]


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------


def test_import_happy_path(client):
    headers, _ = auth_headers(client)
    csv_text = (
        "full_name,email,position\n"
        "Alice,alice@example.com,Engineer\n"
        "Bob,bob@example.com,Designer\n"
    )
    resp = client.post(
        "/api/v1/prospects/import",
        files={"file": _csv_bytes(csv_text)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 2
    assert body["invalid_rows"] == []
    assert body["duplicates_in_file"] == 0


def test_import_case_insensitive_headers_and_dupes_and_invalid(client):
    headers, _ = auth_headers(client)
    csv_text = (
        "Full_Name,EMAIL\n"
        "Alice,alice@example.com\n"
        "Alice Again,alice@example.com\n"  # dup within file
        ",noname@example.com\n"  # missing name
        "NoEmail,\n"  # missing email
    )
    resp = client.post(
        "/api/v1/prospects/import",
        files={"file": _csv_bytes(csv_text)},
        headers=headers,
    )
    body = resp.json()
    assert body["created"] == 1
    assert body["duplicates_in_file"] == 1
    assert len(body["invalid_rows"]) == 2
    reasons = {r["reason"] for r in body["invalid_rows"]}
    assert "missing full_name" in reasons
    assert "missing or invalid email" in reasons


def test_import_already_prospects(client):
    headers, _ = auth_headers(client)
    client.post(
        "/api/v1/prospects",
        json={"full_name": "Alice", "email": "alice@example.com"},
        headers=headers,
    )
    csv_text = "full_name,email\nAlice,alice@example.com\n"
    resp = client.post(
        "/api/v1/prospects/import",
        files={"file": _csv_bytes(csv_text)},
        headers=headers,
    )
    body = resp.json()
    assert body["created"] == 0
    assert body["already_prospects"] == 1


def test_import_links_to_existing_candidate(client):
    headers, _ = auth_headers(client)
    create_candidate_via_api(client, headers, email="c@example.com")
    csv_text = "full_name,email\nCand Person,c@example.com\n"
    resp = client.post(
        "/api/v1/prospects/import",
        files={"file": _csv_bytes(csv_text)},
        headers=headers,
    )
    body = resp.json()
    assert body["created"] == 1
    assert body["linked_to_existing_candidate"] == 1


def test_import_row_cap_413(client):
    headers, _ = auth_headers(client)
    rows = "\n".join(f"Name{i},user{i}@example.com" for i in range(501))
    csv_text = f"full_name,email\n{rows}\n"
    resp = client.post(
        "/api/v1/prospects/import",
        files={"file": _csv_bytes(csv_text)},
        headers=headers,
    )
    assert resp.status_code == 413


def test_import_missing_required_columns_400(client):
    headers, _ = auth_headers(client)
    csv_text = "name,mail\nAlice,alice@example.com\n"
    resp = client.post(
        "/api/v1/prospects/import",
        files={"file": _csv_bytes(csv_text)},
        headers=headers,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# list + suppression flags + search/filter
# ---------------------------------------------------------------------------


def test_list_surfaces_suppression_flag(client, db):
    headers, email = auth_headers(client)
    # Find the org id via the created user.
    from app.models.user import User

    user = db.query(User).filter(User.email == email).first()
    org_id = user.organization_id

    client.post(
        "/api/v1/prospects",
        json={"full_name": "Sup Person", "email": "sup@example.com"},
        headers=headers,
    )
    client.post(
        "/api/v1/prospects",
        json={"full_name": "Clean Person", "email": "clean@example.com"},
        headers=headers,
    )
    from app.services.email_suppression_service import suppress

    suppress(db, email="sup@example.com", reason="unsubscribed", source="link", organization_id=org_id)

    resp = client.get("/api/v1/prospects", headers=headers)
    assert resp.status_code == 200
    by_email = {p["email"]: p for p in resp.json()["prospects"]}
    assert by_email["sup@example.com"]["suppressed"] == "unsubscribed"
    assert by_email["clean@example.com"]["suppressed"] is None


def test_list_search_and_status_filter(client):
    headers, _ = auth_headers(client)
    client.post(
        "/api/v1/prospects",
        json={"full_name": "Searchable Sam", "email": "sam@example.com", "position": "Cook"},
        headers=headers,
    )
    client.post(
        "/api/v1/prospects",
        json={"full_name": "Other One", "email": "other@example.com"},
        headers=headers,
    )
    resp = client.get("/api/v1/prospects", params={"q": "searchable"}, headers=headers)
    emails = [p["email"] for p in resp.json()["prospects"]]
    assert emails == ["sam@example.com"]

    # Archive one, filter by status.
    pid = resp.json()["prospects"][0]["id"]
    client.delete(f"/api/v1/prospects/{pid}", headers=headers)
    archived = client.get("/api/v1/prospects", params={"status": "archived"}, headers=headers)
    assert [p["email"] for p in archived.json()["prospects"]] == ["sam@example.com"]


# ---------------------------------------------------------------------------
# update + archive
# ---------------------------------------------------------------------------


def test_update_prospect_fields_and_status(client):
    headers, _ = auth_headers(client)
    pid = client.post(
        "/api/v1/prospects",
        json={"full_name": "Editable", "email": "edit@example.com"},
        headers=headers,
    ).json()["id"]
    resp = client.patch(
        f"/api/v1/prospects/{pid}",
        json={"position": "CTO", "status": "contacted"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["position"] == "CTO"
    assert resp.json()["status"] == "contacted"


def test_update_invalid_status_400(client):
    headers, _ = auth_headers(client)
    pid = client.post(
        "/api/v1/prospects",
        json={"full_name": "X", "email": "x@example.com"},
        headers=headers,
    ).json()["id"]
    resp = client.patch(f"/api/v1/prospects/{pid}", json={"status": "bogus"}, headers=headers)
    assert resp.status_code == 400


def test_delete_soft_archives(client):
    headers, _ = auth_headers(client)
    pid = client.post(
        "/api/v1/prospects",
        json={"full_name": "Gone", "email": "gone@example.com"},
        headers=headers,
    ).json()["id"]
    resp = client.delete(f"/api/v1/prospects/{pid}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


# ---------------------------------------------------------------------------
# org isolation
# ---------------------------------------------------------------------------


def test_org_isolation_list_and_mutations(client):
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")

    pid_a = client.post(
        "/api/v1/prospects",
        json={"full_name": "A Person", "email": "a@example.com"},
        headers=headers_a,
    ).json()["id"]

    # Org B can't see org A's prospect in its list.
    list_b = client.get("/api/v1/prospects", headers=headers_b)
    assert all(p["id"] != pid_a for p in list_b.json()["prospects"])

    # Org B can't patch or delete org A's prospect.
    assert client.patch(f"/api/v1/prospects/{pid_a}", json={"position": "X"}, headers=headers_b).status_code == 404
    assert client.delete(f"/api/v1/prospects/{pid_a}", headers=headers_b).status_code == 404

    # Same email allowed across orgs (org-scoped uniqueness).
    assert client.post(
        "/api/v1/prospects",
        json={"full_name": "A In B", "email": "a@example.com"},
        headers=headers_b,
    ).status_code == 200


def test_prospect_routes_require_auth(client):
    assert client.get("/api/v1/prospects").status_code in (401, 403)
    assert client.post("/api/v1/prospects", json={"full_name": "x", "email": "x@x.com"}).status_code in (401, 403)
