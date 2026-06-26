"""Requisition: hiring-brief service."""
import pytest
from fastapi import HTTPException

from app.models import (
    BUCKET_CONSTRAINT,
    BUCKET_MUST,
    BUCKET_PREFERRED,
    Organization,
    RoleCriterion,
)
from app.services.role_brief_service import (
    create_brief,
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

