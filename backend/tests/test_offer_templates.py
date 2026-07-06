"""P2: offer templates — CRUD service, create-from-template, and API."""
import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime.offer_service import create_offer
from app.domains.assessments_runtime.offer_template_service import (
    create_template,
    delete_template,
    get_template,
    list_templates,
    update_template,
)
from app.models import Candidate, CandidateApplication, Organization, Role
from app.models.user import ROLE_VIEWER, User
from tests.conftest import auth_headers


def _org_app(db):
    org = Organization(name="Acme", slug="acme-ot")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Eng", source="manual")
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email="c@ot.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="applied",
        application_outcome="open", source="manual",
    )
    db.add(app)
    db.flush()
    return org, app


def test_template_crud_service(db):
    org, _ = _org_app(db)
    t = create_template(
        db, org.id, name="Senior Eng",
        base_salary_amount=180000, currency="USD", pay_frequency="year",
        signing_bonus=20000,
    )
    assert t.id and t.is_active and t.base_salary_amount == 180000
    assert [x.id for x in list_templates(db, org.id)] == [t.id]

    update_template(db, org.id, t.id, {"base_salary_amount": 190000, "is_active": False})
    assert get_template(db, org.id, t.id).base_salary_amount == 190000
    assert list_templates(db, org.id) == []  # inactive excluded by default
    assert len(list_templates(db, org.id, include_inactive=True)) == 1

    delete_template(db, org.id, t.id)
    with pytest.raises(HTTPException):
        get_template(db, org.id, t.id)


def test_create_offer_from_template_prefills_and_respects_overrides(db):
    org, app = _org_app(db)
    t = create_template(
        db, org.id, name="Band A",
        base_salary_amount=150000, currency="AED", pay_frequency="year",
        signing_bonus=10000, custom_fields={"relocation": True},
    )
    # No comp args → everything inherited from the template.
    o = create_offer(db, organization_id=org.id, application_id=app.id, template_id=t.id)
    assert o.base_salary_amount == 150000
    assert o.currency == "AED" and o.signing_bonus == 10000
    assert o.custom_fields == {"relocation": True}

    # Explicit args win; unspecified ones still come from the template.
    o2 = create_offer(
        db, organization_id=org.id, application_id=app.id,
        template_id=t.id, base_salary_amount=200000,
    )
    assert o2.base_salary_amount == 200000 and o2.currency == "AED"


def test_create_offer_with_missing_template_is_404(db):
    org, app = _org_app(db)
    with pytest.raises(HTTPException) as exc:
        create_offer(db, organization_id=org.id, application_id=app.id, template_id=999999)
    assert exc.value.status_code == 404


def test_offer_template_api_crud_and_role_gate(client, db):
    headers, email = auth_headers(client)

    r = client.post(
        "/api/v1/offer-templates",
        json={"name": "Senior", "base_salary_amount": 180000, "currency": "USD"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["base_salary_amount"] == 180000

    r = client.get("/api/v1/offer-templates", headers=headers)
    assert any(x["id"] == tid for x in r.json())

    # Deactivate → hidden from the default list.
    r = client.patch(f"/api/v1/offer-templates/{tid}", json={"is_active": False}, headers=headers)
    assert r.status_code == 200 and r.json()["is_active"] is False
    r = client.get("/api/v1/offer-templates", headers=headers)
    assert not any(x["id"] == tid for x in r.json())

    r = client.delete(f"/api/v1/offer-templates/{tid}", headers=headers)
    assert r.status_code == 204

    # Viewer role can't create.
    caller = db.query(User).filter(User.email == email).first()
    caller.role = ROLE_VIEWER
    db.commit()
    r = client.post("/api/v1/offer-templates", json={"name": "X"}, headers=headers)
    assert r.status_code == 403
