"""Screening questions CRUD + deterministic knockout gate (with malformed-type
hardening)."""
import pytest
from fastapi import HTTPException

from app.domains.job_pages.screening_service import (
    create_role_question,
    delete_role_question,
    evaluate_knockouts,
    get_role_question,
    list_role_questions,
    update_role_question,
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
    assert len(list_role_questions(db, org.id, role.id, include_inactive=True)) == 2


def test_update_and_delete(db):
    org, role = _org_role(db)
    q = create_role_question(db, org.id, role.id, prompt="Old", kind="text")
    update_role_question(
        db, org.id, role.id, q.id,
        fields={"prompt": "New", "required": True, "is_active": False},
    )
    assert q.prompt == "New" and q.required is True and q.is_active is False
    with pytest.raises(HTTPException) as e:
        update_role_question(db, org.id, role.id, q.id, fields={"kind": "bogus"})
    assert e.value.status_code == 422
    delete_role_question(db, org.id, role.id, q.id)
    with pytest.raises(HTTPException) as e2:
        get_role_question(db, org.id, role.id, q.id)
    assert e2.value.status_code == 404


def test_cross_org_isolation(db):
    org, role = _org_role(db)
    other = Organization(name="Other", slug="other")
    db.add(other)
    db.flush()
    q = create_role_question(db, org.id, role.id, prompt="Q", kind="text")
    # Another org cannot fetch/update/delete this question.
    with pytest.raises(HTTPException) as e:
        get_role_question(db, other.id, role.id, q.id)
    assert e.value.status_code == 404


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


def test_evaluate_knockouts_malformed_types_raise_422(db):
    org, role = _org_role(db)
    q = create_role_question(
        db, org.id, role.id, prompt="Skills?", kind="multi_select",
        knockout=True, knockout_expected=["python"],
    )
    qs = list_role_questions(db, org.id, role.id)
    # A dict answer -> 422, never a TypeError 500.
    with pytest.raises(HTTPException) as e1:
        evaluate_knockouts(qs, {str(q.id): {"nested": "obj"}})
    assert e1.value.status_code == 422
    # A list-of-dicts answer -> 422.
    with pytest.raises(HTTPException) as e2:
        evaluate_knockouts(qs, {str(q.id): [{"a": 1}, {"b": 2}]})
    assert e2.value.status_code == 422
    # A non-dict answers payload -> 422.
    with pytest.raises(HTTPException) as e3:
        evaluate_knockouts(qs, ["not", "a", "dict"])
    assert e3.value.status_code == 422
    # None answers payload is treated as empty (no crash), question passes
    # because it's a non-required knockout with no answer supplied → failed.
    ok, failed = evaluate_knockouts(qs, None)
    assert not ok and q.id in failed
