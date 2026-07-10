"""Clients API + consultancy economics.

Covers the CLIENT entity (create/list/get/patch, org-scoping, open_job_count)
and the requisition-serializer additions (client_name + margin/margin_pct) plus
cross-org client_id rejection on the requisition PATCH.

The requisition serializer reads the org's template; no Anthropic is needed for
any of these (create/patch only flush DB state, no LLM).
"""
from app.services.client_service import compute_margin
from tests.conftest import auth_headers


# Publish now enforces the required-fields gate — spread these into a PATCH
# before publishing so the brief passes. Column fields at top level; template-
# only fields (domain / urgency / responsibilities) under custom_fields.
_REQUIRED_COLUMN_FIELDS = {
    "seniority": "senior",
    "summary": "Build and own the payments API.",
    "workplace_type": "remote",
    "employment_type": "full_time",
    "openings": 1,
    "must_haves": ["Python", "Postgres"],
    "success_profile": "Ships reliable services end-to-end.",
}
_REQUIRED_CUSTOM_FIELDS = {
    "domain": "Fintech",
    "urgency": "high",
    "responsibilities": ["Design APIs", "On-call rotation"],
}


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
    # Enriched detail fields present (no rate set → null margin, unpublished → null page).
    assert req["client_rate"] is None
    assert req["margin"] is None
    assert req["margin_pct"] is None
    assert req["job_page"] is None
    # A drafted-but-unpublished requisition has NO job page → open_job_count 0
    # (the requisition still shows in the requisitions list).
    assert body["open_job_count"] == 0

    # A different org cannot see this client.
    other_headers, _ = auth_headers(client, organization_name="OtherOrg")
    assert client.get(f"/api/v1/clients/{client_id}", headers=other_headers).status_code == 404


# --------------------------------------------------------------------------- #
# Client detail page: summary rollup + enriched requisitions
# --------------------------------------------------------------------------- #
def test_get_client_detail_summary_and_enriched_requisitions(client):
    """Two requisitions — one with rate+salary (computable margin), one without
    — yield the right summary rollup, enriched requisition items, and an
    open_job_count from the published page."""
    headers, _ = auth_headers(client)
    client_id = client.post(
        "/api/v1/clients", json={"name": "Soylent"}, headers=headers
    ).json()["id"]

    # Requisition A: rate 240000, salary_max 180000 → margin 60000, pct 25.
    with_margin_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/v1/requisitions/{with_margin_id}",
        json={
            **_REQUIRED_COLUMN_FIELDS,
            "title": "Data Engineer",
            "client_id": client_id,
            "client_rate": 240000,
            "salary_min": 120000,
            "salary_max": 180000,
            "custom_fields": _REQUIRED_CUSTOM_FIELDS,
        },
        headers=headers,
    )
    # Requisition B: assigned but no rate → margin not computable.
    no_margin_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/v1/requisitions/{no_margin_id}",
        json={"title": "Recruiter", "client_id": client_id},
        headers=headers,
    )

    # Publish the margin requisition → one open job page.
    pub = client.post(
        f"/api/v1/requisitions/{with_margin_id}/publish",
        json={"jd_markdown": "# Data Engineer"},
        headers=headers,
    )
    assert pub.status_code == 200, pub.text

    body = client.get(f"/api/v1/clients/{client_id}", headers=headers).json()

    # open_job_count = the single published page.
    assert body["open_job_count"] == 1

    # summary: only the computable margin contributes; avg over one pct = 25.
    assert body["summary"] == {
        "open_jobs": 1,
        "total_requisitions": 2,
        "total_margin": 60000,
        "avg_margin_pct": 25,
    }

    # Requisitions are newest-first (id desc): B (no margin), then A (margin).
    reqs = {r["id"]: r for r in body["requisitions"]}
    assert len(reqs) == 2

    a = reqs[with_margin_id]
    assert a["client_rate"] == 240000
    assert a["margin"] == 60000
    assert a["margin_pct"] == 25
    assert a["job_page"] == "open"  # published

    b = reqs[no_margin_id]
    assert b["client_rate"] is None
    assert b["margin"] is None
    assert b["margin_pct"] is None
    assert b["job_page"] is None  # unpublished


def test_get_client_detail_no_requisitions_zeros_and_nulls(client):
    """A client with no requisitions → empty list, zero counts, null margins."""
    headers, _ = auth_headers(client)
    client_id = client.post(
        "/api/v1/clients", json={"name": "Umbrella"}, headers=headers
    ).json()["id"]

    body = client.get(f"/api/v1/clients/{client_id}", headers=headers).json()
    assert body["requisitions"] == []
    assert body["open_job_count"] == 0
    assert body["summary"] == {
        "open_jobs": 0,
        "total_requisitions": 0,
        "total_margin": None,
        "avg_margin_pct": None,
    }


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
# open_job_count semantics — counts PUBLISHED job pages, not requisitions
# --------------------------------------------------------------------------- #
def test_open_job_count_counts_published_job_pages(client, db):
    headers, _ = auth_headers(client)
    client_id = client.post("/api/v1/clients", json={"name": "Stark"}, headers=headers).json()["id"]

    # Two requisitions assigned to the client; only one is PUBLISHED.
    published_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    draft_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    for b in (published_id, draft_id):
        client.patch(
            f"/api/v1/requisitions/{b}",
            json={
                **_REQUIRED_COLUMN_FIELDS,
                "client_id": client_id,
                "title": "Eng",
                "custom_fields": _REQUIRED_CUSTOM_FIELDS,
            },
            headers=headers,
        )

    # Brand new: no job pages yet → 0 open jobs even though two requisitions exist.
    listed = {c["id"]: c for c in client.get("/api/v1/clients", headers=headers).json()}
    assert listed[client_id]["open_job_count"] == 0

    # Publish ONE requisition → a published (open) job page → count is 1.
    pub = client.post(
        f"/api/v1/requisitions/{published_id}/publish",
        json={"jd_markdown": "# Eng"},
        headers=headers,
    )
    assert pub.status_code == 200, pub.text
    assert pub.json()["status"] == "open"

    listed = {c["id"]: c for c in client.get("/api/v1/clients", headers=headers).json()}
    # 1 published page; the drafted-but-unpublished requisition does NOT count.
    assert listed[client_id]["open_job_count"] == 1
    # Single-client GET is consistent.
    assert client.get(
        f"/api/v1/clients/{client_id}", headers=headers
    ).json()["open_job_count"] == 1

    # Closing the page (no close endpoint in scope) excludes it again → 0.
    page_token = pub.json()["token"]
    from app.models.job_page import JobPage

    page = db.query(JobPage).filter(JobPage.token == page_token).first()
    page.status = "closed"
    db.commit()

    listed = {c["id"]: c for c in client.get("/api/v1/clients", headers=headers).json()}
    assert listed[client_id]["open_job_count"] == 0


def test_open_job_count_excludes_other_clients_pages(client):
    """A published page only counts for the client its requisition is assigned
    to (the grouped query keys by client_id)."""
    headers, _ = auth_headers(client)
    a = client.post("/api/v1/clients", json={"name": "Acme"}, headers=headers).json()["id"]
    b = client.post("/api/v1/clients", json={"name": "Beta"}, headers=headers).json()["id"]

    # One published requisition for client A, none for B.
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={
            **_REQUIRED_COLUMN_FIELDS,
            "client_id": a,
            "title": "Eng",
            "custom_fields": _REQUIRED_CUSTOM_FIELDS,
        },
        headers=headers,
    )
    client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "# Eng"},
        headers=headers,
    )

    listed = {c["id"]: c for c in client.get("/api/v1/clients", headers=headers).json()}
    assert listed[a]["open_job_count"] == 1
    assert listed[b]["open_job_count"] == 0


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
