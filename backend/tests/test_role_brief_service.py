"""Requisition: hiring-brief service."""
import re

import pytest
from fastapi import HTTPException

from app.models import (
    BUCKET_CONSTRAINT,
    BUCKET_MUST,
    BUCKET_PREFERRED,
    Organization,
    CRITERION_SOURCE_REQUISITION,
    RoleCriterion,
)
from app.models.role import (
    JOB_STATUS_DRAFT,
    JOB_STATUS_FILLED,
    JOB_STATUS_OPEN,
)
from app.services.role_brief_service import (
    REF_CODE_PREFIX,
    create_brief,
    ensure_ref_code,
    find_ref_code,
    generate_ref_code,
    materialize_brief_to_role,
    submit_brief,
    update_brief_fields,
)


def _org(db):
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.flush()
    return org


def test_create_brief(db):
    b = create_brief(db, organization_id=_org(db).id, source_kind="conversational")
    assert b.status == "draft" and b.role_id is None and b.source_kind == "conversational"


def test_create_rejects_bad_source(db):
    with pytest.raises(HTTPException) as e:
        create_brief(db, organization_id=_org(db).id, source_kind="bogus")
    assert e.value.status_code == 422


def test_update_fields_whitelist(db):
    b = create_brief(db, organization_id=_org(db).id)
    update_brief_fields(
        db, b,
        title="Senior Engineer",
        must_haves=[{"text": "Python"}],
        priorities=[{"factor": "domain", "weight": "high"}],
        not_a_column="ignored",
    )
    assert b.title == "Senior Engineer"
    assert b.must_haves == [{"text": "Python"}]
    assert b.priorities[0]["factor"] == "domain"


def test_submit(db):
    b = create_brief(db, organization_id=_org(db).id)
    submit_brief(db, b)
    assert b.status == "submitted"


def test_materialize_creates_role(db):
    b = create_brief(db, organization_id=_org(db).id)
    update_brief_fields(db, b, title="Backend Engineer", summary="Build APIs")
    role = materialize_brief_to_role(db, b)
    assert role.id is not None
    assert role.name == "Backend Engineer"
    assert role.description == "Build APIs"
    assert role.source == "requisition"
    assert b.role_id == role.id and b.status == "applied"


def test_materialized_requisition_inherits_workspace_agent_defaults(db):
    org = _org(db)
    org.default_role_budget_cents = 12_500
    org.default_score_threshold = 73
    b = create_brief(db, organization_id=org.id)
    update_brief_fields(db, b, title="Backend Engineer")

    role = materialize_brief_to_role(db, b, mark_applied=False)

    assert role.monthly_usd_budget_cents == 12_500
    assert role.score_threshold == 73


def test_materialize_copies_full_jd_and_structured_role_fields(db):
    b = create_brief(db, organization_id=_org(db).id)
    update_brief_fields(
        db,
        b,
        title="Platform Engineer",
        summary="Short summary",
        department="Engineering",
        location_city="Dubai",
        location_country="UAE",
        workplace_type="hybrid",
        employment_type="full_time",
        salary_min=240000,
        salary_max=300000,
        salary_currency="AED",
        salary_period="year",
    )
    jd = "# Platform Engineer\n\nFull responsibilities and requirements."
    role = materialize_brief_to_role(db, b, job_spec_text=jd)

    assert role.job_spec_text == jd
    assert role.job_spec_uploaded_at is not None
    assert role.description == "Short summary"
    assert role.department == "Engineering"
    assert role.location_city == "Dubai"
    assert role.location_country == "UAE"
    assert role.workplace_type == "hybrid"
    assert role.employment_type == "full_time"
    assert role.salary_min == 240000
    assert role.salary_max == 300000
    assert role.salary_currency == "AED"
    assert role.salary_period == "year"


def test_applied_brief_is_locked_then_rematerializes_same_role(db):
    b = create_brief(db, organization_id=_org(db).id)
    update_brief_fields(db, b, title="Eng")
    r1 = materialize_brief_to_role(db, b)
    with pytest.raises(HTTPException) as e:
        update_brief_fields(db, b, title="Eng v2")  # locked after applied
    assert e.value.status_code == 409
    r2 = materialize_brief_to_role(db, b)
    assert r1.id == r2.id


def test_materialize_creates_criteria_by_bucket(db):
    b = create_brief(db, organization_id=_org(db).id)
    update_brief_fields(
        db, b, title="Eng",
        must_haves=["Python", "Postgres"],
        preferred=["AWS"],
        dealbreakers=["Must be onsite"],
    )
    role = materialize_brief_to_role(db, b)
    crits = {
        (c.text, c.bucket, c.must_have)
        for c in db.query(RoleCriterion).filter(RoleCriterion.role_id == role.id)
    }
    assert ("Python", BUCKET_MUST, True) in crits
    assert ("Postgres", BUCKET_MUST, True) in crits
    assert ("AWS", BUCKET_PREFERRED, False) in crits
    assert ("Must be onsite", BUCKET_CONSTRAINT, False) in crits
    # idempotent: re-publishing does not duplicate criteria
    materialize_brief_to_role(db, b)
    assert (
        db.query(RoleCriterion).filter(RoleCriterion.role_id == role.id).count() == 4
    )


def test_republish_reconciles_only_requisition_owned_criteria(db):
    b = create_brief(db, organization_id=_org(db).id)
    update_brief_fields(
        db,
        b,
        title="Eng",
        must_haves=["Python", "Postgres"],
        preferred=["AWS"],
    )
    role = materialize_brief_to_role(db, b, mark_applied=False)
    recruiter_row = RoleCriterion(
        role_id=role.id,
        text="Recruiter-specific signal",
        bucket=BUCKET_PREFERRED,
        must_have=False,
        source="recruiter",
        ordering=99,
    )
    db.add(recruiter_row)
    db.flush()

    update_brief_fields(
        db,
        b,
        must_haves=["Go"],
        preferred=[],
        dealbreakers=["No production ownership"],
    )
    materialize_brief_to_role(db, b, mark_applied=False)

    active = (
        db.query(RoleCriterion)
        .filter(RoleCriterion.role_id == role.id, RoleCriterion.deleted_at.is_(None))
        .all()
    )
    requisition = {
        (row.text, row.bucket)
        for row in active
        if row.source == CRITERION_SOURCE_REQUISITION
    }
    assert requisition == {
        ("Go", BUCKET_MUST),
        ("No production ownership", BUCKET_CONSTRAINT),
    }
    assert recruiter_row in active
    assert not {"Python", "Postgres", "AWS"} & {row.text for row in active}


def test_republish_adopts_exact_legacy_brief_rows_before_reconciling(db):
    """Pre-provenance requisitions stored their brief rows as recruiter rows.
    An exact uncustomized match can be adopted safely, avoiding duplicates on
    the first publish after upgrade."""
    b = create_brief(db, organization_id=_org(db).id)
    update_brief_fields(db, b, title="Eng", must_haves=["Python"])
    role = materialize_brief_to_role(db, b, mark_applied=False)
    original = (
        db.query(RoleCriterion)
        .filter(RoleCriterion.role_id == role.id)
        .one()
    )
    original.source = "recruiter"
    db.flush()

    materialize_brief_to_role(db, b, mark_applied=False)
    rows = db.query(RoleCriterion).filter(RoleCriterion.role_id == role.id).all()

    assert len(rows) == 1
    assert rows[0].id == original.id
    assert rows[0].source == CRITERION_SOURCE_REQUISITION


# --------------------------------------------------------------------------- #
# Ref code + the Workable-bridge match key
# --------------------------------------------------------------------------- #
_REF_RE = re.compile(r"^TAL-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{5}$")


def test_generate_ref_code_format(db):
    code = generate_ref_code(db)
    assert code.startswith(REF_CODE_PREFIX)
    assert _REF_RE.match(code), code
    # no ambiguous glyphs
    assert not set("01OILU") & set(code[len(REF_CODE_PREFIX):])


def test_ensure_ref_code_is_mint_once(db):
    b = create_brief(db, organization_id=_org(db).id)
    first = ensure_ref_code(db, b)
    assert b.ref_code == first
    assert ensure_ref_code(db, b) == first  # idempotent, reused


def test_ref_codes_are_unique_across_briefs(db):
    org = _org(db)
    codes = {ensure_ref_code(db, create_brief(db, organization_id=org.id)) for _ in range(25)}
    assert len(codes) == 25


def test_find_ref_code_scans_free_text(db):
    code = generate_ref_code(db)
    jd = f"Senior Engineer\n\nGreat role.\n\n---\n_Taali ref: {code} — keep this._\n"
    assert find_ref_code(jd) == code
    assert find_ref_code("no code in here") is None
    assert find_ref_code(None) is None


def test_publish_variant_keeps_brief_editable_and_sets_draft(db):
    b = create_brief(db, organization_id=_org(db).id)
    update_brief_fields(db, b, title="Eng", summary="Build")
    role = materialize_brief_to_role(db, b, mark_applied=False, job_status=JOB_STATUS_DRAFT)
    assert role.job_status == JOB_STATUS_DRAFT
    assert b.status != "applied"  # NOT locked
    # still editable after a publish
    update_brief_fields(db, b, title="Eng v2")
    assert b.title == "Eng v2"


def test_republish_does_not_demote_a_linked_or_filled_job(db):
    b = create_brief(db, organization_id=_org(db).id)
    update_brief_fields(db, b, title="Eng")
    role = materialize_brief_to_role(db, b, mark_applied=False, job_status=JOB_STATUS_DRAFT)
    # bridge linked it + recruiter marked filled
    role.workable_job_id = "ENGIN001"
    role.job_status = JOB_STATUS_FILLED
    db.flush()
    # a re-publish must not knock it back to draft
    materialize_brief_to_role(db, b, mark_applied=False, job_status=JOB_STATUS_DRAFT)
    assert role.job_status == JOB_STATUS_FILLED


def test_republish_promotes_open_only_from_draft(db):
    b = create_brief(db, organization_id=_org(db).id)
    update_brief_fields(db, b, title="Eng")
    role = materialize_brief_to_role(db, b, mark_applied=False, job_status=JOB_STATUS_DRAFT)
    role.job_status = JOB_STATUS_OPEN  # already live (no workable id yet)
    db.flush()
    materialize_brief_to_role(db, b, mark_applied=False, job_status=JOB_STATUS_DRAFT)
    assert role.job_status == JOB_STATUS_OPEN  # not demoted
