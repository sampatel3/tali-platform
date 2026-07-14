"""Route tests for the distribution surfaces.

- ``GET /api/v1/roles/{id}/distribution`` (authed): artefacts for a published
  role; ``published: false`` before publish; foreign role 404.
- ``GET /api/v1/public/careers/{slug}/feed.xml`` (no auth): a valid, parseable
  JobPosting feed for the org's open pages; an unknown/empty org → empty feed
  (200), never a 500.
"""
from xml.etree import ElementTree as ET
from datetime import datetime, timezone

import pytest

from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, Role
from app.platform.config import settings
from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def _enable_public_apply(monkeypatch):
    monkeypatch.setattr(settings, "ATS_PUBLIC_APPLY_ENABLED", True)

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


def _publish_role(client, headers, db, *, activate=True, **fields):
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
    body = pub.json()
    if activate:
        role = db.query(Role).filter(Role.id == body["role_id"]).one()
        role.agentic_mode_enabled = True
        role.job_status = JOB_STATUS_OPEN
        db.commit()
    return body


def _set_org_slug(db, slug="acme"):
    org = db.query(Organization).first()
    org.slug = slug
    db.commit()
    return slug


# ---- authed artefacts endpoint --------------------------------------------


def test_distribution_returns_artefacts_for_published_role(client, db):
    headers, _ = auth_headers(client)
    slug = _set_org_slug(db)
    pub = _publish_role(client, headers, db, title="Backend Engineer")

    resp = client.get(f"/api/v1/roles/{pub['role_id']}/distribution", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["published"] is True
    assert body["distribution_ready"] is True
    assert body["reason"] is None
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


def test_distribution_unpublished_role_returns_published_false(client):
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "Draft Role"}, headers=headers).json()

    resp = client.get(f"/api/v1/roles/{role['id']}/distribution", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "published": False,
        "distribution_ready": False,
        "reason": "not_published",
    }


def test_distribution_hides_artefacts_until_published_role_is_activated(client, db):
    headers, _ = auth_headers(client)
    pub = _publish_role(client, headers, db, activate=False, title="Preview")

    body = client.get(
        f"/api/v1/roles/{pub['role_id']}/distribution", headers=headers
    ).json()

    assert body == {
        "published": True,
        "distribution_ready": False,
        "reason": "job_not_open",
    }
    assert "apply_url" not in body


def test_distribution_and_feed_stop_while_agent_is_paused(client, db):
    headers, _ = auth_headers(client)
    slug = _set_org_slug(db, "paused-distribution")
    pub = _publish_role(client, headers, db, title="Paused Role")
    role = db.query(Role).filter(Role.id == pub["role_id"]).one()
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "usage cap reached"
    db.commit()

    body = client.get(
        f"/api/v1/roles/{pub['role_id']}/distribution", headers=headers
    ).json()
    root = ET.fromstring(client.get(f"/api/v1/public/careers/{slug}/feed.xml").text)

    assert body == {
        "published": True,
        "distribution_ready": False,
        "reason": "agent_paused",
    }
    assert root.findall("job") == []


def test_distribution_stops_for_closed_workable_mirror(client, db):
    headers, _ = auth_headers(client)
    pub = _publish_role(client, headers, db, title="ATS Mirror")
    role = db.query(Role).filter(Role.id == pub["role_id"]).one()
    # The optional adoption flow legitimately changes source while retaining
    # the requisition's existing public page and managed job_status.
    role.source = "workable"
    role.workable_job_id = "ATS-99"
    role.workable_job_data = {"state": "closed"}
    db.commit()

    body = client.get(
        f"/api/v1/roles/{pub['role_id']}/distribution", headers=headers
    ).json()

    assert body == {
        "published": True,
        "distribution_ready": False,
        "reason": "ats_job_not_live",
    }
    assert "apply_url" not in body


def test_distribution_foreign_role_404(client, db):
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")
    pub = _publish_role(client, headers_a, db, title="Backend Engineer")

    resp = client.get(f"/api/v1/roles/{pub['role_id']}/distribution", headers=headers_b)
    assert resp.status_code == 404, resp.text


def test_distribution_requires_auth(client):
    resp = client.get("/api/v1/roles/1/distribution")
    assert resp.status_code == 401, resp.text


# ---- public feed -----------------------------------------------------------


def test_feed_xml_lists_open_pages(client, db):
    headers, _ = auth_headers(client)
    slug = _set_org_slug(db)
    _publish_role(client, headers, db, title="Backend Engineer")
    _publish_role(client, headers, db, title="Data Analyst")

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


def test_feed_excludes_a_published_but_not_activated_requisition(client, db):
    headers, _ = auth_headers(client)
    slug = _set_org_slug(db, "preview-feed")
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    updated = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={
            **_REQUIRED_COLUMN_FIELDS,
            "title": "Preview Only",
            "custom_fields": _REQUIRED_CUSTOM_FIELDS,
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    published = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "# Preview Only\n\nNot live."},
        headers=headers,
    )
    assert published.status_code == 200, published.text

    root = ET.fromstring(
        client.get(f"/api/v1/public/careers/{slug}/feed.xml").text
    )
    assert root.findall("job") == []
