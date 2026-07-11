"""P0: per-org disqualification-reason resolution + management."""
import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime.disqualification_reasons_service import (
    create_org_reason,
    ensure_org_reasons_seeded,
    list_org_reasons,
    reorder_org_reasons,
    resolve_org_reasons,
    update_org_reason,
)
from app.models import DisqualificationReason, Organization


def _make_org(db, slug="acme"):
    org = Organization(name=slug.title(), slug=slug)
    db.add(org)
    db.flush()
    return org


def test_resolve_falls_back_to_canonical_when_unseeded(db):
    org = _make_org(db)
    reasons = resolve_org_reasons(db, org.id)
    assert len(reasons) == 11
    assert reasons[0].label == "Underqualified"
    assert {r.category for r in reasons} == {
        "we_rejected",
        "they_withdrew",
        "other",
    }


def test_seed_idempotent_and_table_read(db):
    org = _make_org(db)
    assert ensure_org_reasons_seeded(db, org.id) == 11
    assert ensure_org_reasons_seeded(db, org.id) == 0
    assert (
        db.query(DisqualificationReason)
        .filter_by(organization_id=org.id)
        .count()
        == 11
    )


def test_create_validates_and_dedups(db):
    org = _make_org(db)
    ensure_org_reasons_seeded(db, org.id)
    row = create_org_reason(db, org.id, label="Visa/relocation", category="other")
    assert row.is_default is False and row.is_active is True
    with pytest.raises(HTTPException) as e1:
        create_org_reason(db, org.id, label="Bad", category="nope")
    assert e1.value.status_code == 422
    with pytest.raises(HTTPException) as e2:
        create_org_reason(db, org.id, label="Underqualified", category="we_rejected")
    assert e2.value.status_code == 409


def test_update_and_deactivate(db):
    org = _make_org(db)
    ensure_org_reasons_seeded(db, org.id)
    other = (
        db.query(DisqualificationReason)
        .filter_by(organization_id=org.id, label="Other")
        .one()
    )
    update_org_reason(db, org.id, other.id, is_active=False)
    assert "Other" not in [r.label for r in list_org_reasons(db, org.id)]
    with pytest.raises(HTTPException) as e:
        update_org_reason(db, org.id, 999999, label="X")
    assert e.value.status_code == 404


def test_reorder(db):
    org = _make_org(db)
    ensure_org_reasons_seeded(db, org.id)
    rows = list_org_reasons(db, org.id)
    reversed_ids = [r.id for r in reversed(rows)]
    result = reorder_org_reasons(db, org.id, reversed_ids)
    assert [r.id for r in result] == reversed_ids
    with pytest.raises(HTTPException) as e:
        reorder_org_reasons(db, org.id, [rows[0].id, 999999])
    assert e.value.status_code == 422


def test_org_isolation(db):
    a = _make_org(db, "a")
    b = _make_org(db, "b")
    ensure_org_reasons_seeded(db, a.id)
    create_org_reason(db, a.id, label="A-only", category="other")
    assert "A-only" in {r.label for r in resolve_org_reasons(db, a.id)}
    assert "A-only" not in {r.label for r in resolve_org_reasons(db, b.id)}
