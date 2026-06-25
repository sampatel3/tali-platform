"""P1: public careers queries + JobPosting JSON-LD."""
from app.domains.assessments_runtime.careers_service import (
    build_job_posting_jsonld,
    get_published_role,
    list_published_roles,
)
from app.domains.assessments_runtime.role_publishing_service import publish_role
from app.models import Organization, Role


def _org(db, slug="acme"):
    org = Organization(name="Acme Inc", slug=slug)
    db.add(org)
    db.flush()
    return org


def _role(db, org, name, **kw):
    role = Role(organization_id=org.id, name=name, source="manual", **kw)
    db.add(role)
    db.flush()
    return role


def test_list_only_published(db):
    org = _org(db)
    r1 = _role(db, org, "Published Eng")
    publish_role(db, r1)
    _role(db, org, "Draft Eng")  # stays draft -> excluded
    db.flush()
    org_out, roles = list_published_roles(db, "acme")
    assert org_out.id == org.id
    assert [r.slug for r in roles] == [r1.slug]


def test_list_unknown_org(db):
    org_out, roles = list_published_roles(db, "nope")
    assert org_out is None and roles == []


def test_get_published_role(db):
    org = _org(db)
    r = _role(db, org, "Senior Eng")
    publish_role(db, r)
    db.flush()
    _, role = get_published_role(db, "acme", r.slug)
    assert role.id == r.id
    _, missing = get_published_role(db, "acme", "missing")
    assert missing is None


def test_jsonld_remote_with_salary(db):
    org = _org(db)
    r = _role(
        db, org, "Backend Engineer",
        description="Build things",
        employment_type="full_time",
        workplace_type="remote",
        salary_min=100000, salary_max=150000,
        salary_currency="USD", salary_period="year",
    )
    publish_role(db, r)
    db.flush()
    db.refresh(r)
    ld = build_job_posting_jsonld(r, org)
    assert ld["@type"] == "JobPosting"
    assert ld["title"] == "Backend Engineer"
    assert ld["hiringOrganization"]["name"] == "Acme Inc"
    assert ld["employmentType"] == "FULL_TIME"
    assert ld["jobLocationType"] == "TELECOMMUTE"
    assert ld["baseSalary"]["currency"] == "USD"
    assert ld["baseSalary"]["value"]["minValue"] == 100000
    assert ld["baseSalary"]["value"]["unitText"] == "YEAR"
    assert ld["directApply"] is True
    assert "datePosted" in ld


def test_jsonld_onsite_location(db):
    org = _org(db)
    r = _role(
        db, org, "Onsite Role",
        employment_type="contract",
        location_city="Dubai", location_country="AE",
    )
    publish_role(db, r)
    db.flush()
    ld = build_job_posting_jsonld(r, org)
    assert ld["employmentType"] == "CONTRACTOR"
    assert ld["jobLocation"]["address"]["addressLocality"] == "Dubai"
    assert "jobLocationType" not in ld
