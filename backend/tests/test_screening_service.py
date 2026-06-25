"""P1: screening questions + deterministic knockout gate."""
import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime.screening_service import (
    create_role_question,
    evaluate_knockouts,
    list_role_questions,
)
from app.models import Organization, Role


def _org_role(db):
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Eng", source="manual")
    db.add(role)
    db.flush()
    return org, role


def test_create_validates(db):
    org, role = _org_role(db)
    q = create_role_question(db, org.id, role.id, prompt="Years?", kind="number")
    assert q.position == 0 and q.is_active is True
    with pytest.raises(HTTPException) as e1:
        create_role_question(db, org.id, role.id, prompt="x", kind="bogus")
    assert e1.value.status_code == 422
    with pytest.raises(HTTPException) as e2:
        create_role_question(db, org.id, role.id, prompt="   ", kind="text")
    assert e2.value.status_code == 422


def test_list_ordered_and_active_filter(db):
    org, role = _org_role(db)
    create_role_question(db, org.id, role.id, prompt="Q1", kind="text")
    q2 = create_role_question(db, org.id, role.id, prompt="Q2", kind="text")
    assert [q.prompt for q in list_role_questions(db, org.id, role.id)] == ["Q1", "Q2"]
    q2.is_active = False
    db.flush()
    assert [q.prompt for q in list_role_questions(db, org.id, role.id)] == ["Q1"]


def test_evaluate_knockouts(db):
    org, role = _org_role(db)
    work = create_role_question(
        db, org.id, role.id, prompt="Authorized to work?", kind="boolean",
        required=True, knockout=True, knockout_expected=[True],
    )
    yrs = create_role_question(
        db, org.id, role.id, prompt="Years?", kind="number", required=True
    )
    qs = list_role_questions(db, org.id, role.id)
    ok, failed = evaluate_knockouts(qs, {str(work.id): True, str(yrs.id): 5})
    assert ok and failed == []
    ok, failed = evaluate_knockouts(qs, {str(work.id): False, str(yrs.id): 5})
    assert not ok and work.id in failed  # knockout failed
    ok, failed = evaluate_knockouts(qs, {str(work.id): True})
    assert not ok and yrs.id in failed  # required missing


def test_multi_select_knockout(db):
    org, role = _org_role(db)
    q = create_role_question(
        db, org.id, role.id, prompt="Skills?", kind="multi_select",
        knockout=True, knockout_expected=["python", "go"],
    )
    qs = list_role_questions(db, org.id, role.id)
    ok, _ = evaluate_knockouts(qs, {str(q.id): ["java", "python"]})  # has python
    assert ok
    ok, failed = evaluate_knockouts(qs, {str(q.id): ["java", "rust"]})
    assert not ok and q.id in failed
