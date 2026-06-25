"""P1: candidate identity resolution + cross-source merge."""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.models import Candidate, CandidateApplication, Organization, Role
from app.services.candidate_identity_service import (
    merge_candidates,
    normalize_phone,
    resolve_candidate,
)


def _org(db, slug="acme"):
    org = Organization(name=slug.title(), slug=slug)
    db.add(org)
    db.flush()
    return org


def _role(db, org, name):
    role = Role(organization_id=org.id, name=name, source="manual")
    db.add(role)
    db.flush()
    return role


def _cand(db, org, **kw):
    cand = Candidate(organization_id=org.id, **kw)
    db.add(cand)
    db.flush()
    return cand


def _app(db, org, cand, role):
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    return app


def test_normalize_phone():
    assert normalize_phone("+971 50 202 2165") == "502022165"
    assert normalize_phone("0502022165") == "502022165"
    assert normalize_phone("123") is None
    assert normalize_phone(None) is None


def test_resolve_by_email_case_insensitive(db):
    org = _org(db)
    c = _cand(db, org, email="Jane@X.test", full_name="Jane")
    assert resolve_candidate(db, org.id, email="jane@x.test").id == c.id
    assert resolve_candidate(db, org.id, email="nobody@x.test") is None


def test_resolve_by_phone_fallback(db):
    org = _org(db)
    c = _cand(
        db, org, email="a@x.test", phone="+971 50 202 2165", phone_normalized="502022165"
    )
    assert resolve_candidate(db, org.id, phone="00971502022165").id == c.id


def test_resolve_ignores_deleted(db):
    org = _org(db)
    c = _cand(db, org, email="d@x.test")
    c.deleted_at = datetime.now(timezone.utc)
    db.flush()
    assert resolve_candidate(db, org.id, email="d@x.test") is None


def test_merge_reassigns_apps_and_backfills(db):
    org = _org(db)
    role_a = _role(db, org, "A")
    role_b = _role(db, org, "B")
    primary = _cand(db, org, email="p@x.test", full_name="P")
    dup = _cand(
        db,
        org,
        email="d@x.test",
        full_name="P2",
        phone="0502022165",
        phone_normalized="502022165",
        workable_candidate_id="wk123",
    )
    _app(db, org, primary, role_a)
    dup_app_b = _app(db, org, dup, role_b)
    merge_candidates(db, primary=primary, duplicate=dup)
    db.flush()
    assert dup_app_b.candidate_id == primary.id  # reassigned (no collision)
    assert primary.phone_normalized == "502022165"  # backfilled
    assert primary.workable_candidate_id == "wk123"  # inherits Workable link
    assert primary.full_name == "P"  # not overwritten (was set)
    assert dup.deleted_at is not None


def test_merge_collision_keeps_primary_app(db):
    org = _org(db)
    role = _role(db, org, "Shared")
    primary = _cand(db, org, email="p@x.test")
    dup = _cand(db, org, email="d@x.test")
    p_app = _app(db, org, primary, role)
    d_app = _app(db, org, dup, role)
    merge_candidates(db, primary=primary, duplicate=dup)
    db.flush()
    assert p_app.candidate_id == primary.id and p_app.deleted_at is None
    # colliding dup app stays on the duplicate, soft-deleted (constraint-safe)
    assert d_app.candidate_id == dup.id and d_app.deleted_at is not None


def test_merge_cross_org_rejected(db):
    o1 = _org(db, "a")
    o2 = _org(db, "b")
    c1 = _cand(db, o1, email="a@x.test")
    c2 = _cand(db, o2, email="b@x.test")
    with pytest.raises(HTTPException) as exc:
        merge_candidates(db, primary=c1, duplicate=c2)
    assert exc.value.status_code == 422


def test_merge_same_candidate_noop(db):
    org = _org(db)
    c = _cand(db, org, email="a@x.test")
    assert merge_candidates(db, primary=c, duplicate=c).id == c.id
