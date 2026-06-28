"""Smarter warm-start: prefill a new requisition's SUBSTANCE from the most
similar prior role/brief (title-matched, deterministic, no LLM)."""
from app.models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from app.models.organization import Organization
from app.models.role import Role
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


def test_richer_brief_wins_tie_over_role(db):
    org = _org(db)
    _role_with_criteria(db, org, "Data Engineer", must=["Python"])
    prior = create_brief(db, organization_id=org.id)
    update_brief_fields(
        db, prior,
        title="Data Engineer",
        must_haves=["Python", "Airflow"],
        success_profile="Owns the data platform",
        salary_min=200000,
        salary_max=260000,
    )
    brief = create_brief(db, organization_id=org.id)
    update_brief_fields(db, brief, title="Data Engineer")

    res = find_similar_prefill(db, organization_id=org.id, brief=brief)
    assert res["source"]["kind"] == "brief"  # richer source wins the tie
    assert res["fields"]["success_profile"] == "Owns the data platform"
    assert res["fields"]["salary_min"] == 200000


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
    # A prior requisition with requirements (publishing it also creates a role).
    a = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/v1/requisitions/{a}",
        json={"title": "Senior Data Engineer", "must_haves": ["Python", "Spark"], "preferred": ["AWS"]},
        headers=headers,
    )
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
