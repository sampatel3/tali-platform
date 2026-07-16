"""Public JOB PAGE: publish flow + the no-auth public resolver.

Publishing a requisition mints a shareable PUBLIC job page (idempotent — one per
brief; re-publish reuses the token and refreshes the JD). The public GET serves
only public-safe fields + the poster's org name and NEVER any consultancy
client / rate / margin. A closed (or unknown) page 404s. The requisition
serializer gains a ``job_page`` block once published, and the brief stays
editable (status unchanged) so it can be re-published.

Provider-backed edit coverage uses a monkeypatched structured response; tests
make no external Anthropic call.
"""
import pytest

from app.llm.structured import StructuredResult
from app.models.job_hiring_team import (
    TEAM_ROLE_INTERVIEWER,
    JobHiringTeam,
)
from app.models.job_page import JobPage
from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, Role
from app.models.role_brief import RoleBrief
from app.models.role_change_event import RoleChangeEvent
from app.models.user import User
from app.platform.config import settings
from app.services import requisition_chat_service as requisition_chat
from app.services.requisition_chat_service import ChatCapture
from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def _enable_public_apply(monkeypatch):
    monkeypatch.setattr(settings, "ATS_PUBLIC_APPLY_ENABLED", True)


# Publish now enforces the same required-fields gate the UI does, so fill every
# required template field by default. Column-backed fields go top-level;
# template-only fields (domain / urgency / responsibilities) go in custom_fields.
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


def _make_requisition(client, headers, **fields):
    """Create a requisition and PATCH the given public fields onto it. Required
    fields are pre-filled so publish passes the server-side gate."""
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    resp = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={**_REQUIRED_COLUMN_FIELDS, **fields, "custom_fields": _REQUIRED_CUSTOM_FIELDS},
        headers=headers,
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
        json={
            "jd_markdown": "second draft",
            "expected_version": first["version"],
        },
        headers=headers,
    ).json()

    # Same brief -> same JobPage (same id + token), JD refreshed.
    assert second["token"] == first["token"]
    assert second["job_page_id"] == first["job_page_id"]
    pages = db.query(JobPage).filter(JobPage.brief_id == brief_id).all()
    assert len(pages) == 1
    assert pages[0].jd_markdown == "second draft"


def test_publish_leaves_brief_editable_versioned_and_republishable(client, db):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Eng")
    before = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert before["status"] == "draft"

    published = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "JD"},
        headers=headers,
    ).json()

    after = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    # Brief stays editable (NOT 'applied') so it can be re-published.
    assert after["status"] == "draft"
    # A linked write must identify the Role revision the recruiter reviewed.
    missing_version = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Eng II"},
        headers=headers,
    )
    assert missing_version.status_code == 422, missing_version.text

    # The versioned patch remains editable and advances Role.version once.
    edit = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Eng II", "expected_version": published["version"]},
        headers=headers,
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["title"] == "Eng II"
    assert edit.json()["job"]["version"] == published["version"] + 1

    event = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == published["role_id"])
        .order_by(RoleChangeEvent.id.desc())
        .first()
    )
    assert event is not None
    assert event.action == "requisition_brief_updated"
    assert event.from_version == published["version"]
    assert event.to_version == edit.json()["job"]["version"]

    # An identical retry is a true no-op: no revision or audit noise.
    event_count = db.query(RoleChangeEvent).filter(
        RoleChangeEvent.role_id == published["role_id"]
    ).count()
    no_op = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={
            "title": "Eng II",
            "expected_version": edit.json()["job"]["version"],
        },
        headers=headers,
    )
    assert no_op.status_code == 200, no_op.text
    assert no_op.json()["job"]["version"] == edit.json()["job"]["version"]
    assert db.query(RoleChangeEvent).filter(
        RoleChangeEvent.role_id == published["role_id"]
    ).count() == event_count

    # A stale tab cannot overwrite that edit.
    stale = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Stale title", "expected_version": published["version"]},
        headers=headers,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "ROLE_VERSION_CONFLICT"
    assert client.get(
        f"/api/v1/requisitions/{brief_id}", headers=headers
    ).json()["title"] == "Eng II"

    # The edited brief is still the source for a subsequent re-publish.
    republished = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={
            "jd_markdown": "# Eng II\n\nUpdated role",
            "expected_version": edit.json()["job"]["version"],
        },
        headers=headers,
    )
    assert republished.status_code == 200, republished.text
    assert republished.json()["role_id"] == published["role_id"]
    db.expire_all()
    assert db.get(Role, published["role_id"]).name == "Eng II"


def test_linked_requisition_edit_denies_non_editing_team_member(client, db):
    headers, email = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Controlled Brief")
    published = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "# Controlled Brief\n\nShared role"},
        headers=headers,
    ).json()

    user = db.query(User).filter(User.email == email).one()
    membership = (
        db.query(JobHiringTeam)
        .filter(
            JobHiringTeam.role_id == published["role_id"],
            JobHiringTeam.user_id == user.id,
        )
        .one()
    )
    user.role = "member"
    membership.team_role = TEAM_ROLE_INTERVIEWER
    db.commit()

    denied = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={
            "title": "Unauthorized edit",
            "expected_version": published["version"],
        },
        headers=headers,
    )

    assert denied.status_code == 403, denied.text
    db.expire_all()
    assert db.get(RoleBrief, brief_id).title == "Controlled Brief"
    assert db.get(Role, published["role_id"]).version == published["version"]


def test_linked_requisition_chat_advances_one_shared_revision(
    client, db, monkeypatch
):
    headers, _ = auth_headers(client)
    brief_id = _make_requisition(client, headers, title="Chat Editable Brief")
    published = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "# Chat Editable Brief\n\nShared role"},
        headers=headers,
    ).json()
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    def fake_generate_structured(_client, **_kwargs):
        return StructuredResult(
            value=ChatCapture(
                assistant_reply="Updated the success profile.",
                success_profile="Owns reliable delivery across the platform.",
            ),
            ok=True,
        )

    monkeypatch.setattr(
        requisition_chat,
        "generate_structured",
        fake_generate_structured,
    )

    response = client.post(
        f"/api/v1/requisitions/{brief_id}/chat",
        data={
            "message": "Great means owning reliable delivery.",
            "expected_version": str(published["version"]),
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()["brief"]
    assert body["success_profile"] == "Owns reliable delivery across the platform."
    assert body["job"]["version"] == published["version"] + 1
    db.expire_all()
    events = db.query(RoleChangeEvent).filter(
        RoleChangeEvent.role_id == published["role_id"],
        RoleChangeEvent.action == "requisition_brief_updated",
    ).all()
    assert len(events) == 1
    assert events[0].from_version == published["version"]
    assert events[0].to_version == body["job"]["version"]


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


# --------------------------------------------------------------------------- #
# Public careers board: GET /api/v1/public/careers/{slug} (no auth)
# --------------------------------------------------------------------------- #
def _publish(client, headers, db, **fields):
    """Create/publish and mark the already-readied agent live for board tests."""
    jd = fields.pop("jd_markdown", "JD")
    brief_id = _make_requisition(client, headers, **fields)
    published = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": jd},
        headers=headers,
    ).json()
    role = db.query(Role).filter(Role.id == published["role_id"]).one()
    role.agentic_mode_enabled = True
    role.job_status = JOB_STATUS_OPEN
    db.commit()
    return published["token"]


def _org_slug(db, organization_name):
    org = db.query(Organization).filter(Organization.name == organization_name).first()
    assert org is not None
    return org.slug


def test_careers_board_lists_published_pages_with_public_fields(client, db):
    headers, _ = auth_headers(client, organization_name="Initech Talent")
    _publish(
        client,
        headers,
        db,
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
    slug = _org_slug(db, "Initech Talent")

    # No Authorization header — the careers board must serve anonymously.
    resp = client.get(f"/api/v1/public/careers/{slug}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["organization_name"] == "Initech Talent"
    assert body["slug"] == slug
    assert len(body["jobs"]) == 1
    job = body["jobs"][0]
    assert job["title"] == "Platform Engineer"
    assert job["location"] == "Dubai, UAE"
    assert job["workplace_type"] == "hybrid"
    assert job["employment_type"] == "full_time"
    assert job["seniority"] == "senior"
    assert job["salary"] == "AED 200,000–260,000 / year"
    assert job["published_at"]
    # URL embeds the token, mirroring the single-page route.
    assert job["url"].endswith(f"/job/{job['token']}")


def test_careers_board_newest_first(client, db):
    headers, _ = auth_headers(client, organization_name="Hooli Search")
    # Publish three; reorder published_at directly so the test is deterministic
    # regardless of clock resolution.
    t1 = _publish(client, headers, db, title="First")
    t2 = _publish(client, headers, db, title="Second")
    t3 = _publish(client, headers, db, title="Third")
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for tok, offset in ((t1, 0), (t2, 1), (t3, 2)):
        page = db.query(JobPage).filter(JobPage.token == tok).first()
        page.published_at = base + timedelta(days=offset)
    db.commit()

    slug = _org_slug(db, "Hooli Search")
    body = client.get(f"/api/v1/public/careers/{slug}").json()
    titles = [j["title"] for j in body["jobs"]]
    assert titles == ["Third", "Second", "First"]  # published_at desc


def test_careers_board_excludes_closed_and_drafts(client, db):
    headers, _ = auth_headers(client, organization_name="Pied Piper Hiring")
    open_token = _publish(client, headers, db, title="Open Role")
    closed_token = _publish(client, headers, db, title="Closed Role")
    # A draft requisition that was never published mints NO page → never listed.
    _make_requisition(client, headers, title="Draft Role")

    page = db.query(JobPage).filter(JobPage.token == closed_token).first()
    page.status = "closed"
    db.commit()

    slug = _org_slug(db, "Pied Piper Hiring")
    body = client.get(f"/api/v1/public/careers/{slug}").json()
    titles = [j["title"] for j in body["jobs"]]
    assert titles == ["Open Role"]
    assert "Closed Role" not in titles
    assert "Draft Role" not in titles
    tokens = {j["token"] for j in body["jobs"]}
    assert open_token in tokens
    assert closed_token not in tokens


def test_careers_board_does_not_advertise_published_requisition_preview(client, db):
    headers, _ = auth_headers(client, organization_name="Preview Only Co")
    brief_id = _make_requisition(client, headers, title="Not Live Yet")
    published = client.post(
        f"/api/v1/requisitions/{brief_id}/publish",
        json={"jd_markdown": "# Not Live Yet\n\nPreview."},
        headers=headers,
    )
    assert published.status_code == 200, published.text

    slug = _org_slug(db, "Preview Only Co")
    body = client.get(f"/api/v1/public/careers/{slug}").json()

    assert body["jobs"] == []


def test_careers_board_excludes_other_orgs_pages(client, db):
    # Org A publishes a page.
    headers_a, _ = auth_headers(client, organization_name="Org Alpha")
    _publish(client, headers_a, db, title="Alpha Role")
    # Org B publishes a page.
    headers_b, _ = auth_headers(client, organization_name="Org Beta")
    _publish(client, headers_b, db, title="Beta Role")

    slug_a = _org_slug(db, "Org Alpha")
    body = client.get(f"/api/v1/public/careers/{slug_a}").json()
    titles = [j["title"] for j in body["jobs"]]
    assert titles == ["Alpha Role"]
    assert "Beta Role" not in titles


def test_careers_board_empty_list_when_no_published_pages(client, db):
    headers, _ = auth_headers(client, organization_name="Empty Co")
    slug = _org_slug(db, "Empty Co")

    resp = client.get(f"/api/v1/public/careers/{slug}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["organization_name"] == "Empty Co"
    assert body["slug"] == slug
    assert body["jobs"] == []


def test_careers_board_never_exposes_client_rate_or_margin(client, db):
    """The board payload must carry NO consultancy economics."""
    headers, _ = auth_headers(client, organization_name="Discreet Recruiting")
    client_id = client.post(
        "/api/v1/clients", json={"name": "Hidden Client Ltd"}, headers=headers
    ).json()["id"]
    _publish(
        client,
        headers,
        db,
        title="Eng",
        client_id=client_id,
        client_rate=400000,
        salary_min=150000,
        salary_max=200000,
        salary_currency="AED",
    )
    slug = _org_slug(db, "Discreet Recruiting")

    body = client.get(f"/api/v1/public/careers/{slug}").json()
    job = body["jobs"][0]
    forbidden = {"client_id", "client_name", "client_rate", "margin", "margin_pct"}
    assert forbidden.isdisjoint(job.keys()), f"leaked: {forbidden & set(job.keys())}"
    serialized = str(body)
    assert "400000" not in serialized
    assert "Hidden Client Ltd" not in serialized


def test_careers_board_salary_formatting_variants(client, db):
    headers, _ = auth_headers(client, organization_name="Comp Variants Co")
    # Full band (currency omitted → defaults to AED).
    _publish(client, headers, db, title="Both", salary_min=20000, salary_max=28000)
    # Floor only.
    _publish(client, headers, db, title="MinOnly", salary_min=20000, salary_currency="AED")
    # Ceiling only.
    _publish(client, headers, db, title="MaxOnly", salary_max=28000, salary_currency="AED")
    # No band at all → "".
    _publish(client, headers, db, title="NoBand")

    slug = _org_slug(db, "Comp Variants Co")
    body = client.get(f"/api/v1/public/careers/{slug}").json()
    by_title = {j["title"]: j["salary"] for j in body["jobs"]}
    assert by_title["Both"] == "AED 20,000–28,000 / year"
    assert by_title["MinOnly"] == "AED 20,000+ / year"
    assert by_title["MaxOnly"] == "up to AED 28,000 / year"
    assert by_title["NoBand"] == ""


def test_careers_board_unknown_slug_404(client):
    resp = client.get("/api/v1/public/careers/no-such-org")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Requisition serializer: careers_url
# --------------------------------------------------------------------------- #
def test_requisition_serializer_includes_careers_url_when_org_has_slug(client, db):
    headers, _ = auth_headers(client, organization_name="Slugged Org")
    brief_id = _make_requisition(client, headers, title="Eng")
    slug = _org_slug(db, "Slugged Org")

    body = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert body["careers_url"] is not None
    # FRONTEND_URL default is http://localhost:5173.
    assert body["careers_url"].endswith(f"/careers/{slug}")


def test_requisition_serializer_careers_url_null_when_org_has_no_slug(client, db):
    headers, _ = auth_headers(client, organization_name="Slugless Org")
    brief_id = _make_requisition(client, headers, title="Eng")

    # Clear the org's slug directly (self-signup always sets one).
    org = db.query(Organization).filter(Organization.name == "Slugless Org").first()
    org.slug = None
    db.commit()

    body = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert body["careers_url"] is None
