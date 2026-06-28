"""Requisition warm-start helpers: role-agnostic boilerplate standardisation +
requirements GUIDANCE (a reference for the intake agent, not a prefill)."""
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.client import Client
from app.models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_criterion import CRITERION_SOURCE_RECRUITER, RoleCriterion
from app.services.requisition_similar_service import (
    _score,
    _tokens,
    apply_agnostic_fields,
    similar_requirements_guidance,
    standardize_agnostic_fields,
)
from app.services.role_brief_service import create_brief, update_brief_fields


def _org(db, name="Acme"):
    org = Organization(name=name, slug=name.lower().replace(" ", "-"))
    db.add(org)
    db.flush()
    return org


def _role(db, org, name, *, must=(), preferred=(), constraint=(), job_spec_text=None):
    role = Role(organization_id=org.id, name=name, source="workable", job_spec_text=job_spec_text)
    db.add(role)
    db.flush()
    ordering = 0
    for items, bucket in ((must, BUCKET_MUST), (preferred, BUCKET_PREFERRED), (constraint, BUCKET_CONSTRAINT)):
        for text in items:
            db.add(RoleCriterion(
                role_id=role.id, text=text, bucket=bucket, must_have=(bucket == BUCKET_MUST),
                source=CRITERION_SOURCE_RECRUITER, ordering=ordering,
            ))
            ordering += 1
    db.flush()
    return role


def _apps(db, org, role, n):
    for i in range(n):
        c = Candidate(organization_id=org.id, email=f"c{role.id}-{i}@x.test", full_name=f"C{i}")
        db.add(c)
        db.flush()
        db.add(CandidateApplication(
            organization_id=org.id, candidate_id=c.id, role_id=role.id,
            status="applied", pipeline_stage="applied", application_outcome="open", source="manual",
        ))
    db.flush()


def _new_brief(db, org, title, **fields):
    b = create_brief(db, organization_id=org.id)
    update_brief_fields(db, b, title=title, **fields)
    return b


# --- scoring --------------------------------------------------------------- #
def test_score_overlap_coefficient():
    assert _score(_tokens("Senior Data Engineer"), _tokens("Data Engineer")) == 1.0
    assert _score(_tokens("Data Engineer"), _tokens("Marketing Manager")) == 0.0


# --- requirements guidance ------------------------------------------------- #
def test_guidance_from_similar_role(db):
    org = _org(db)
    _role(db, org, "Senior Data Engineer", must=["Python", "Spark"], preferred=["AWS"], constraint=["Onsite"])
    res = similar_requirements_guidance(db, organization_id=org.id, brief=_new_brief(db, org, "Data Engineer"))
    assert res is not None
    assert res["role_name"] == "Senior Data Engineer"
    assert set(res["must_haves"]) == {"Python", "Spark"}
    assert res["preferred"] == ["AWS"]
    assert res["dealbreakers"] == ["Onsite"]


def test_guidance_none_below_threshold(db):
    org = _org(db)
    _role(db, org, "Marketing Manager", must=["SEO"])
    assert similar_requirements_guidance(db, organization_id=org.id, brief=_new_brief(db, org, "Data Engineer")) is None


def test_guidance_none_without_title(db):
    org = _org(db)
    _role(db, org, "Data Engineer", must=["Python"])
    assert similar_requirements_guidance(db, organization_id=org.id, brief=create_brief(db, organization_id=org.id)) is None


def test_guidance_prefers_more_applicants(db):
    org = _org(db)
    strong = _role(db, org, "Senior Data Engineer", must=["Scala", "Spark"])  # older
    weak = _role(db, org, "Data Engineer", must=["Python"])                    # newer
    _apps(db, org, weak, 2)
    _apps(db, org, strong, 30)
    res = similar_requirements_guidance(db, organization_id=org.id, brief=_new_brief(db, org, "Data Engineer"))
    assert res["role_name"] == "Senior Data Engineer"  # more applications wins
    assert res["applicants"] == 30


def test_guidance_scopes_to_same_client_then_falls_back(db):
    org = _org(db)
    client = Client(organization_id=org.id, name="Globex")
    db.add(client)
    db.flush()
    # A same-client role (linked via a brief) + a higher-signal unrelated-client role.
    client_role = _role(db, org, "Data Engineer", must=["ClientStack"])
    cb = create_brief(db, organization_id=org.id)
    update_brief_fields(db, cb, title="Data Engineer", client_id=client.id)
    cb.role_id = client_role.id
    db.flush()
    other_role = _role(db, org, "Data Engineer", must=["OtherStack"])
    _apps(db, org, other_role, 50)  # more applicants, but different client

    # With the client set, guidance must come from the client's own role.
    scoped = similar_requirements_guidance(
        db, organization_id=org.id, brief=_new_brief(db, org, "Data Engineer", client_id=client.id),
    )
    assert scoped["must_haves"] == ["ClientStack"]

    # With no client, it falls back org-wide → the stronger (more-applied) role.
    org_wide = similar_requirements_guidance(
        db, organization_id=org.id, brief=_new_brief(db, org, "Data Engineer"),
    )
    assert org_wide["must_haves"] == ["OtherStack"]


# --- role-agnostic standardisation ----------------------------------------- #
def test_standardize_evp_from_recent_brief(db):
    org = _org(db)
    prior = create_brief(db, organization_id=org.id)
    update_brief_fields(db, prior, title="Eng", evp=["Remote-first", "Strong equity"])
    out = standardize_agnostic_fields(db, org.id)
    assert out["evp"] == ["Remote-first", "Strong equity"]


def test_standardize_benefits_from_role_spec(db):
    org = _org(db)
    _role(db, org, "Eng", job_spec_text="About the role\nBuild things.\n\nBenefits\n- Health\n- 25 days leave")
    out = standardize_agnostic_fields(db, org.id)
    assert out["benefits"] == ["Health", "25 days leave"]


def test_apply_agnostic_fills_empty_only(db):
    org = _org(db)
    brief = create_brief(db, organization_id=org.id)
    update_brief_fields(db, brief, title="Eng", evp=["Existing EVP"])  # already set
    applied = apply_agnostic_fields(db, brief, {"evp": ["New EVP"], "benefits": ["Health"]})
    assert "evp" not in applied            # not overwritten
    assert brief.evp == ["Existing EVP"]
    assert "benefits" in applied
    assert brief.custom_fields["benefits"] == ["Health"]
