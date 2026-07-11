"""Candidate identity resolution for native apply (resolve/create, no
cross-org bleed). merge_candidates was intentionally not ported (zero callers)."""
from datetime import datetime, timezone

from app.models import Candidate, Organization
from app.services.candidate_identity_service import (
    normalize_phone,
    resolve_candidate,
)


def _org(db, slug="acme"):
    org = Organization(name=slug.title(), slug=slug)
    db.add(org)
    db.flush()
    return org


def _cand(db, org, **kw):
    cand = Candidate(organization_id=org.id, **kw)
    db.add(cand)
    db.flush()
    return cand


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
        db, org, email="a@x.test", phone="+971 50 202 2165",
        phone_normalized="502022165",
    )
    assert resolve_candidate(db, org.id, phone="00971502022165").id == c.id


def test_resolve_ignores_deleted(db):
    org = _org(db)
    c = _cand(db, org, email="d@x.test")
    c.deleted_at = datetime.now(timezone.utc)
    db.flush()
    assert resolve_candidate(db, org.id, email="d@x.test") is None


def test_resolve_is_org_scoped(db):
    o1 = _org(db, "a")
    o2 = _org(db, "b")
    _cand(db, o1, email="shared@x.test")
    # Same email in a different org must not resolve.
    assert resolve_candidate(db, o2.id, email="shared@x.test") is None
