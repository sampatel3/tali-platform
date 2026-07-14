"""Route tests for the distribution surfaces.

- ``GET /api/v1/roles/{id}/distribution`` (authed): artefacts for a published
  role; ``published: false`` before publish; foreign role 404.
- ``GET /api/v1/public/careers/{slug}/feed.xml`` (no auth): a valid, parseable
  JobPosting feed for the org's open pages; an unknown/empty org → empty feed
  (200), never a 500.
"""
from xml.etree import ElementTree as ET

from app.models.organization import Organization
from tests.conftest import auth_headers

_REQUIRED_COLUMN_FIELDS = {
    "title": "Backend Engineer",
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


def _publish_role(client, headers, **fields):
    """Create + publish a requisition; return the publish response body
    (carries role_id + token)."""
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    resp = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={**_REQUIRED_COLUMN_FIELDS, **fields, "custom_fields": _REQUIRED_CUSTOM_FIELDS},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    pub = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "# Backend Engineer\n\nBuild the payments API."},
        headers=headers,
    )
    assert pub.status_code == 200, pub.text
    return pub.json()


def _set_org_slug(db, slug="acme"):
    org = db.query(Organization).first()
    org.slug = slug
    db.commit()
    return slug


# ---- authed artefacts endpoint --------------------------------------------


def test_distribution_returns_artefacts_for_published_role(client, db):
    headers, _ = auth_headers(client)
    slug = _set_org_slug(db)
    pub = _publish_role(client, headers, title="Backend Engineer")

    resp = client.get(f"/api/v1/roles/{pub['role_id']}/distribution", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["published"] is True
    # Apply URL is the EXISTING public job page for this token.
    assert body["apply_url"].endswith(f"/job/{pub['token']}")
    assert body["apply_url"] == pub["url"]
    assert pub["token"] in body["linkedin_post"]
    assert body["share_urls"]["apply_url"] == body["apply_url"]
    assert body["share_urls"]["linkedin"].startswith(
        "https://www.linkedin.com/sharing/share-offsite/?url="
    )
    assert body["share_urls"]["email"].startswith("mailto:?")
    # Feed URL points at this org's careers feed.
    assert body["feed_url"].endswith(f"/api/v1/public/careers/{slug}/feed.xml")
    # published_at is surfaced so the panel can show "Live since …".
    assert body["published_at"] is not None


# ---- Jobs-list "Live" published flag --------------------------------------


def test_roles_list_is_published_flag(client, db):
    """A published role carries is_published=True in the /roles list; an
    unpublished role is False — a single batched flag, no per-card fetch."""
    headers, _ = auth_headers(client)
    _set_org_slug(db)
    pub = _publish_role(client, headers, title="Backend Engineer")
    draft = client.post("/api/v1/roles", json={"name": "Draft Role"}, headers=headers).json()

    rows = client.get("/api/v1/roles", headers=headers).json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[pub["role_id"]]["is_published"] is True
    assert by_id[draft["id"]]["is_published"] is False


def test_distribution_unpublished_role_returns_published_false(client):
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "Draft Role"}, headers=headers).json()

    resp = client.get(f"/api/v1/roles/{role['id']}/distribution", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"published": False}


def test_distribution_foreign_role_404(client):
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")
    pub = _publish_role(client, headers_a, title="Backend Engineer")

    resp = client.get(f"/api/v1/roles/{pub['role_id']}/distribution", headers=headers_b)
    assert resp.status_code == 404, resp.text


def test_distribution_requires_auth(client):
    resp = client.get("/api/v1/roles/1/distribution")
    assert resp.status_code == 401, resp.text


# ---- public feed -----------------------------------------------------------


def test_feed_xml_lists_open_pages(client, db):
    headers, _ = auth_headers(client)
    slug = _set_org_slug(db)
    _publish_role(client, headers, title="Backend Engineer")
    _publish_role(client, headers, title="Data Analyst")

    resp = client.get(f"/api/v1/public/careers/{slug}/feed.xml")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/xml")

    root = ET.fromstring(resp.text)  # well-formed
    titles = {j.find("title").text for j in root.findall("job")}
    assert {"Backend Engineer", "Data Analyst"} <= titles
    # Each job links to its public /job/{token} page.
    for job in root.findall("job"):
        assert "/job/" in job.find("link").text


def test_feed_xml_unknown_org_is_empty_not_500(client):
    resp = client.get("/api/v1/public/careers/no-such-org/feed.xml")
    assert resp.status_code == 200, resp.text
    root = ET.fromstring(resp.text)
    assert root.findall("job") == []


def test_feed_xml_org_with_no_open_pages_is_empty(client, db):
    auth_headers(client)  # creates the org, but nothing published
    slug = _set_org_slug(db)

    resp = client.get(f"/api/v1/public/careers/{slug}/feed.xml")
    assert resp.status_code == 200, resp.text
    root = ET.fromstring(resp.text)
    assert root.findall("job") == []
