"""Smarter warm-start: prefill a new requisition's SUBSTANCE from the most
similar prior role/brief (title-matched, deterministic, no LLM)."""
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_brief import RoleBrief
from app.models.role_criterion import CRITERION_SOURCE_RECRUITER, RoleCriterion
from app.services.requisition_similar_service import (
    _score,
    _tokens,
    apply_prefill_to_empty_fields,
    find_similar_prefill,
)
from app.services.role_brief_service import create_brief, update_brief_fields
from tests.conftest import auth_headers


def _org(db, name="Acme"):
    org = Organization(name=name, slug=name.lower().replace(" ", "-"))
    db.add(org)
    db.flush()
    return org


def _role_with_criteria(db, org, name, *, must=(), preferred=(), constraint=()):
    role = Role(organization_id=org.id, name=name, source="workable")
    db.add(role)
    db.flush()
    ordering = 0
    for items, bucket in (
        (must, BUCKET_MUST),
        (preferred, BUCKET_PREFERRED),
        (constraint, BUCKET_CONSTRAINT),
    ):
        for text in items:
            db.add(
                RoleCriterion(
                    role_id=role.id,
                    text=text,
                    bucket=bucket,
                    must_have=(bucket == BUCKET_MUST),
                    source=CRITERION_SOURCE_RECRUITER,
                    ordering=ordering,
                )
            )
            ordering += 1
    db.flush()
    return role


# --- scoring --------------------------------------------------------------- #
def test_score_overlap_coefficient():
    assert _score(_tokens("Senior Data Engineer"), _tokens("Data Engineer")) == 1.0
    assert _score(_tokens("Data Engineer"), _tokens("Software Engineer")) == 0.5
    assert _score(_tokens("Data Engineer"), _tokens("Marketing Manager")) == 0.0


# --- matching -------------------------------------------------------------- #
def test_finds_similar_role_and_extracts_criteria(db):
    org = _org(db)
    _role_with_criteria(
        db, org, "Senior Data Engineer",
        must=["Python", "Spark"], preferred=["AWS"], constraint=["Onsite Dubai"],
    )
    brief = create_brief(db, organization_id=org.id)
    update_brief_fields(db, brief, title="Data Engineer")

    res = find_similar_prefill(db, organization_id=org.id, brief=brief)
    assert res is not None
    assert res["source"]["kind"] == "role"
    assert res["source"]["name"] == "Senior Data Engineer"
    assert set(res["fields"]["must_haves"]) == {"Python", "Spark"}
    assert res["fields"]["preferred"] == ["AWS"]
    assert res["fields"]["dealbreakers"] == ["Onsite Dubai"]
    assert res["fields"]["seniority"] == "Senior"  # lifted from the matched title


def test_no_match_below_threshold(db):
    org = _org(db)
    _role_with_criteria(db, org, "Marketing Manager", must=["SEO"])
    brief = create_brief(db, organization_id=org.id)
    update_brief_fields(db, brief, title="Data Engineer")
    assert find_similar_prefill(db, organization_id=org.id, brief=brief) is None


def test_no_title_returns_none(db):
    org = _org(db)
    brief = create_brief(db, organization_id=org.id)
    assert find_similar_prefill(db, organization_id=org.id, brief=brief) is None


def _apps(db, org, role, n):
    """Attach ``n`` applications to a role (the strong-spec signal)."""
    for i in range(n):
        c = Candidate(organization_id=org.id, email=f"c{role.id}-{i}@x.test", full_name=f"C{i}")
        db.add(c)
        db.flush()
        db.add(CandidateApplication(
            organization_id=org.id, candidate_id=c.id, role_id=role.id,
            status="applied", pipeline_stage="applied", application_outcome="open", source="manual",
        ))
    db.flush()


def test_enriches_from_the_roles_originating_requisition(db):
    """The matched role's linked brief contributes the richer fields a bare role
    lacks (responsibilities / success profile / salary)."""
    org = _org(db)
    role = _role_with_criteria(db, org, "Data Engineer", must=["Python"])
    linked = create_brief(db, organization_id=org.id)
    update_brief_fields(
        db, linked,
        title="Data Engineer",
        success_profile="Owns the data platform",
        salary_min=200000, salary_max=260000,
    )
    linked.role_id = role.id  # the requisition this role came from
    linked.custom_fields = {"responsibilities": ["Build pipelines"]}
    db.flush()

    brief = create_brief(db, organization_id=org.id)
    update_brief_fields(db, brief, title="Data Engineer")

    res = find_similar_prefill(db, organization_id=org.id, brief=brief)
    assert res["source"]["kind"] == "role"
    assert res["fields"]["must_haves"] == ["Python"]          # from role criteria
    assert res["fields"]["success_profile"] == "Owns the data platform"  # from brief
    assert res["fields"]["salary_min"] == 200000
    assert res["fields"]["responsibilities"] == ["Build pipelines"]


def test_prefers_the_similar_role_with_more_applicants(db):
    """Among comparably-similar roles, the one with the stronger spec (more
    applications) wins."""
    org = _org(db)
    # ``strong`` is created FIRST (older) so the win is on applicants, not recency.
    strong = _role_with_criteria(db, org, "Senior Data Engineer", must=["Scala", "Spark"])
    weak = _role_with_criteria(db, org, "Data Engineer", must=["Python"])
    _apps(db, org, weak, 2)
    _apps(db, org, strong, 30)

    brief = create_brief(db, organization_id=org.id)
    update_brief_fields(db, brief, title="Data Engineer")

    res = find_similar_prefill(db, organization_id=org.id, brief=brief)
    assert res["source"]["name"] == "Senior Data Engineer"
    assert res["source"]["applicants"] == 30
    assert res["source"]["strong_spec"] is True
    assert set(res["fields"]["must_haves"]) == {"Scala", "Spark"}


def test_scoped_to_org(db):
    org_a = _org(db, "Org A")
    org_b = _org(db, "Org B")
    _role_with_criteria(db, org_a, "Data Engineer", must=["Python"])
    brief = create_brief(db, organization_id=org_b.id)
    update_brief_fields(db, brief, title="Data Engineer")
    assert find_similar_prefill(db, organization_id=org_b.id, brief=brief) is None


# --- applying -------------------------------------------------------------- #
def test_apply_fills_empties_only(db):
    org = _org(db)
    brief = create_brief(db, organization_id=org.id)
    update_brief_fields(db, brief, title="Data Engineer", must_haves=["Existing"])

    applied = apply_prefill_to_empty_fields(
        db, brief,
        {
            "must_haves": ["New"],            # already set -> skipped
            "preferred": ["AWS"],             # empty -> filled
            "responsibilities": ["Build pipelines"],  # -> custom_fields
            "seniority": "Senior",            # empty -> filled
        },
    )
    assert "must_haves" not in applied
    assert brief.must_haves == ["Existing"]   # never overwritten
    assert "preferred" in applied and brief.preferred == ["AWS"]
    assert "responsibilities" in applied
    assert brief.custom_fields["responsibilities"] == ["Build pipelines"]
    assert "seniority" in applied and brief.seniority == "Senior"


# --- endpoint -------------------------------------------------------------- #
def test_prefill_from_similar_endpoint_applies_and_is_idempotent(client):
    headers, _ = auth_headers(client)
    # A prior requisition with requirements, PUBLISHED so it materializes a role
    # (the matcher is role-primary; the role carries the criteria + applicants).
    a = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/v1/requisitions/{a}",
        json={"title": "Senior Data Engineer", "must_haves": ["Python", "Spark"], "preferred": ["AWS"]},
        headers=headers,
    )
    client.post(f"/api/v1/requisitions/{a}/publish", json={"jd_markdown": "JD"}, headers=headers)
    # A new requisition with only a title.
    b = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(f"/api/v1/requisitions/{b}", json={"title": "Data Engineer"}, headers=headers)

    res = client.post(f"/api/v1/requisitions/{b}/prefill-from-similar", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["prefilled_from"]["name"] == "Senior Data Engineer"
    assert "must_haves" in body["prefilled_fields"]
    assert set(body["must_haves"]) >= {"Python", "Spark"}
    assert body["seniority"] == "Senior"

    # Idempotent — everything's now filled, so a second call applies nothing.
    again = client.post(f"/api/v1/requisitions/{b}/prefill-from-similar", headers=headers).json()
    assert again["prefilled_from"] is None
    assert again["prefilled_fields"] == []


def test_prefill_from_similar_no_match_is_noop(client):
    headers, _ = auth_headers(client)
    b = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(f"/api/v1/requisitions/{b}", json={"title": "Underwater Basket Weaver"}, headers=headers)
    body = client.post(f"/api/v1/requisitions/{b}/prefill-from-similar", headers=headers).json()
    assert body["prefilled_from"] is None
    assert body["prefilled_fields"] == []
