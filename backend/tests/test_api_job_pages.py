"""Public JOB PAGE: publish flow + the no-auth public resolver.

Publishing a requisition mints a shareable PUBLIC job page (idempotent — one per
brief; re-publish reuses the token and refreshes the JD). The public GET serves
only public-safe fields + the poster's org name and NEVER any consultancy
client / rate / margin. A closed (or unknown) page 404s. The requisition
serializer gains a ``job_page`` block once published, and the brief stays
editable (status unchanged) so it can be re-published.

No Anthropic is needed for any of this (publish + serialize only touch DB state).
"""
from app.models.job_page import JobPage
from app.models.role_brief import RoleBrief
from tests.conftest import auth_headers


def _make_requisition(client, headers, **fields):
    """Create a requisition and PATCH the given public fields onto it."""
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    if fields:
        resp = client.patch(
            f"/api/v1/requisitions/{brief_id}", json=fields, headers=headers
        )
        assert resp.status_code == 200, resp.text
    return brief_id


# --------------------------------------------------------------------------- #
# Publish → JobPage
# --------------------------------------------------------------------------- #
def test_publish_creates_job_page_and_returns_token_and_url(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(
        client,
        headers,
        title="Backend Engineer",
        location_city="Dubai",
        location_country="UAE",
        workplace_type="hybrid",
        employment_type="full_time",
        seniority="senior",
        salary_min=180000,
        salary_max=240000,
        salary_currency="AED",
    )

    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "# Backend Engineer\n\nBuild things."},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["job_page_id"], int)
    assert body["token"]
    assert body["status"] == "open"
    assert body["published_at"]
    # URL embeds the token (FRONTEND_URL default is http://localhost:5173).
    assert body["url"].endswith(f"/job/{body['token']}")


def test_publish_snapshots_public_fields_onto_job_page(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(
        client,
        headers,
        title="Data Engineer",
        location_city="Abu Dhabi",
        location_country="UAE",
        workplace_type="remote",
        employment_type="full_time",
        seniority="mid",
        salary_min=150000,
        salary_max=200000,
        salary_currency="AED",
    )
    token = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "JD body"},
        headers=headers,
    ).json()["token"]

    page = db.query(JobPage).filter(JobPage.token == token).first()
    assert page is not None
    assert page.brief_id == brief_id
    assert page.title == "Data Engineer"
    assert page.location == "Abu Dhabi, UAE"  # city, country joined
    assert page.workplace_type == "remote"
    assert page.employment_type == "full_time"
    assert page.seniority == "mid"
    assert page.salary_min == 150000
    assert page.salary_max == 200000
    assert page.salary_currency == "AED"
    assert page.jd_markdown == "JD body"


def test_republish_reuses_same_job_page_and_refreshes_jd(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")

    first = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "first draft"},
        headers=headers,
    ).json()

    # Re-publish the same brief with a new JD body.
    second = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "second draft"},
        headers=headers,
    ).json()

    # Same brief -> same JobPage (same id + token), JD refreshed.
    assert second["token"] == first["token"]
    assert second["job_page_id"] == first["job_page_id"]
    pages = db.query(JobPage).filter(JobPage.brief_id == brief_id).all()
    assert len(pages) == 1
    assert pages[0].jd_markdown == "second draft"


def test_publish_leaves_brief_editable_status_unchanged(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    before = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert before["status"] == "draft"

    client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "JD"},
        headers=headers,
    )

    after = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    # Brief stays editable (NOT 'applied') so it can be re-published.
    assert after["status"] == "draft"
    # And the patch endpoint still accepts edits.
    edit = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Eng II"},
        headers=headers,
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["title"] == "Eng II"


# --------------------------------------------------------------------------- #
# Requisition serializer: job_page block
# --------------------------------------------------------------------------- #
def test_requisition_serializer_job_page_null_before_publish(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    body = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert body["job_page"] is None


def test_requisition_serializer_includes_job_page_after_publish(client):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    pub = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "JD"},
        headers=headers,
    ).json()

    body = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert body["job_page"] is not None
    assert body["job_page"]["token"] == pub["token"]
    assert body["job_page"]["status"] == "open"
    assert body["job_page"]["url"].endswith(f"/job/{pub['token']}")
    assert body["job_page"]["published_at"]


# --------------------------------------------------------------------------- #
# Public GET (no auth)
# --------------------------------------------------------------------------- #
def test_public_get_returns_public_fields_and_org_name(client):
    headers, _ = auth_headers(client, organization_name="Globex Recruiting")
    brief_id = _make_requisition(
        client,
        headers,
        title="Platform Engineer",
        location_city="Dubai",
        location_country="UAE",
        workplace_type="hybrid",
        employment_type="full_time",
        seniority="senior",
        salary_min=200000,
        salary_max=260000,
        salary_currency="AED",
    )
    token = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "# Platform Engineer"},
        headers=headers,
    ).json()["token"]

    # No Authorization header — the public route must serve anonymously.
    resp = client.get(f"/api/v1/public/job/{token}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Platform Engineer"
    assert body["jd_markdown"] == "# Platform Engineer"
    assert body["location"] == "Dubai, UAE"
    assert body["workplace_type"] == "hybrid"
    assert body["employment_type"] == "full_time"
    assert body["seniority"] == "senior"
    assert body["salary_min"] == 200000
    assert body["salary_max"] == 260000
    assert body["salary_currency"] == "AED"
    assert body["status"] == "open"
    assert body["organization_name"] == "Globex Recruiting"


def test_public_get_never_exposes_client_rate_or_margin(client):
    """The public payload must carry NO consultancy economics, even when the
    requisition is assigned to a client with a rate."""
    headers, _ = auth_headers(client)
    client_id = client.post(
        "/api/v1/clients", json={"name": "Secret Client Co"}, headers=headers
    ).json()["id"]
    brief_id = _make_requisition(
        client,
        headers,
        title="Eng",
        client_id=client_id,
        client_rate=300000,
        salary_min=150000,
        salary_max=200000,
        salary_currency="AED",
    )
    token = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "JD"},
        headers=headers,
    ).json()["token"]

    body = client.get(f"/api/v1/public/job/{token}").json()
    forbidden = {
        "client_id",
        "client_name",
        "client_rate",
        "margin",
        "margin_pct",
    }
    assert forbidden.isdisjoint(body.keys()), f"leaked: {forbidden & set(body.keys())}"
    # And no value in the payload echoes the secret rate or client name.
    serialized = str(body)
    assert "300000" not in serialized
    assert "Secret Client Co" not in serialized


def test_public_get_unknown_token_404(client):
    resp = client.get("/api/v1/public/job/does-not-exist")
    assert resp.status_code == 404


def test_public_get_closed_page_404(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    token = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "JD"},
        headers=headers,
    ).json()["token"]

    # Close the page directly (no close endpoint in scope) — a closed listing
    # must read as gone.
    page = db.query(JobPage).filter(JobPage.token == token).first()
    page.status = "closed"
    db.commit()

    resp = client.get(f"/api/v1/public/job/{token}")
    assert resp.status_code == 404
