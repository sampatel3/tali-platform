"""Clients API + consultancy economics.

Covers the CLIENT entity (create/list/get/patch, org-scoping, open_job_count)
and the requisition-serializer additions (client_name + margin/margin_pct) plus
cross-org client_id rejection on the requisition PATCH.

The requisition serializer reads the org's template; no Anthropic is needed for
any of these (create/patch only flush DB state, no LLM).
"""
from app.services.client_service import compute_margin
from tests.conftest import auth_headers


# --------------------------------------------------------------------------- #
# Margin helper (pure)
# --------------------------------------------------------------------------- #
def test_compute_margin_uses_salary_max_when_present():
    # rate 240000, salary_max 180000 -> margin 60000, pct 25
    margin, pct = compute_margin(240000, 120000, 180000)
    assert margin == 60000
    assert pct == 25


def test_compute_margin_falls_back_to_salary_min():
    # No salary_max -> cost = salary_min (200000); margin 40000, pct = round(16.6) = 17
    margin, pct = compute_margin(240000, 200000, None)
    assert margin == 40000
    assert pct == 17


def test_compute_margin_none_when_missing_rate_or_cost():
    assert compute_margin(None, 100000, 150000) == (None, None)
    assert compute_margin(240000, None, None) == (None, None)
    # client_rate must be > 0
    assert compute_margin(0, 100000, 150000) == (None, None)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def test_create_client_returns_serialized_client(client):
    headers, _ = auth_headers(client)
    resp = client.post(
        "/api/v1/clients",
        json={"name": "Globex", "contact_name": "Hank", "contact_email": "hank@globex.com"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Globex"
    assert body["contact_name"] == "Hank"
    assert body["contact_email"] == "hank@globex.com"
    assert body["status"] == "active"
    assert body["open_job_count"] == 0
    assert "id" in body


def test_create_client_blank_name_is_422(client):
    headers, _ = auth_headers(client)
    resp = client.post("/api/v1/clients", json={"name": "   "}, headers=headers)
    assert resp.status_code == 422


def test_list_clients_ordered_by_name(client):
    headers, _ = auth_headers(client)
    for name in ("Zeta Corp", "Acme", "Mango"):
        client.post("/api/v1/clients", json={"name": name}, headers=headers)
    resp = client.get("/api/v1/clients", headers=headers)
    assert resp.status_code == 200, resp.text
    names = [c["name"] for c in resp.json()]
    assert names == ["Acme", "Mango", "Zeta Corp"]
    assert all(c["open_job_count"] == 0 for c in resp.json())


def test_get_client_includes_requisitions_and_404_for_other_org(client):
    headers, _ = auth_headers(client)
    client_id = client.post("/api/v1/clients", json={"name": "Initech"}, headers=headers).json()["id"]

    # Create a requisition and assign it to the client via PATCH.
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Backend Engineer", "client_id": client_id},
        headers=headers,
    )

    resp = client.get(f"/api/v1/clients/{client_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == client_id
    assert len(body["requisitions"]) == 1
    req = body["requisitions"][0]
    assert req["id"] == brief_id
    assert req["title"] == "Backend Engineer"
    assert req["status"] == "draft"
    assert "completeness" in req
    # Draft (non-applied) requisition counts as open.
    assert body["open_job_count"] == 1

    # A different org cannot see this client.
    other_headers, _ = auth_headers(client, organization_name="OtherOrg")
    assert client.get(f"/api/v1/clients/{client_id}", headers=other_headers).status_code == 404


def test_patch_client_updates_fields(client):
    headers, _ = auth_headers(client)
    client_id = client.post("/api/v1/clients", json={"name": "Hooli"}, headers=headers).json()["id"]
    resp = client.patch(
        f"/api/v1/clients/{client_id}",
        json={"name": "Hooli XYZ", "status": "archived", "contact_name": "Gavin"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Hooli XYZ"
    assert body["status"] == "archived"
    assert body["contact_name"] == "Gavin"


# --------------------------------------------------------------------------- #
# open_job_count semantics
# --------------------------------------------------------------------------- #
def test_open_job_count_reflects_non_applied_assigned_requisitions(client):
    headers, _ = auth_headers(client)
    client_id = client.post("/api/v1/clients", json={"name": "Stark"}, headers=headers).json()["id"]

    # Two requisitions assigned to the client.
    b1 = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    b2 = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    for b in (b1, b2):
        client.patch(f"/api/v1/requisitions/{b}", json={"client_id": client_id}, headers=headers)

    # A third requisition with NO client should not be counted.
    client.post("/api/v1/requisitions", json={}, headers=headers)

    listed = {c["id"]: c for c in client.get("/api/v1/clients", headers=headers).json()}
    assert listed[client_id]["open_job_count"] == 2

    # Publishing b1 now creates a shareable PUBLIC job page (status 'open') and
    # deliberately leaves the brief editable (status unchanged), so the
    # requisition stays "open" and the count is unchanged.
    client.patch(f"/api/v1/requisitions/{b1}", json={"title": "Eng"}, headers=headers)
    pub = client.post(
        f"/api/v1/requisitions/{b1}/publish", json={"jd_markdown": "# Eng"}, headers=headers
    )
    assert pub.status_code == 200, pub.text
    assert pub.json()["status"] == "open"

    listed = {c["id"]: c for c in client.get("/api/v1/clients", headers=headers).json()}
    assert listed[client_id]["open_job_count"] == 2


# --------------------------------------------------------------------------- #
# Requisition serializer additions
# --------------------------------------------------------------------------- #
def test_requisition_serializer_returns_client_name_and_margin(client):
    headers, _ = auth_headers(client)
    client_id = client.post("/api/v1/clients", json={"name": "Wayne Enterprises"}, headers=headers).json()["id"]
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]

    resp = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={
            "title": "Data Engineer",
            "client_id": client_id,
            "client_rate": 240000,
            "salary_max": 180000,
            "salary_min": 120000,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client_id"] == client_id
    assert body["client_name"] == "Wayne Enterprises"
    assert body["client_rate"] == 240000
    assert body["margin"] == 60000
    assert body["margin_pct"] == 25


def test_requisition_serializer_null_client_and_margin_by_default(client):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    body = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert body["client_id"] is None
    assert body["client_name"] is None
    assert body["margin"] is None
    assert body["margin_pct"] is None


def test_cross_org_client_id_is_rejected_on_requisition_patch(client):
    # Client belongs to org A; org B's requisition cannot point at it.
    headers_a, _ = auth_headers(client)
    client_id = client.post("/api/v1/clients", json={"name": "Cyberdyne"}, headers=headers_a).json()["id"]

    headers_b, _ = auth_headers(client, organization_name="OrgB")
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers_b).json()["id"]
    resp = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"client_id": client_id},
        headers=headers_b,
    )
    assert resp.status_code == 404, resp.text

    # And the brief was not mutated to the cross-org client.
    after = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers_b).json()
    assert after["client_id"] is None
