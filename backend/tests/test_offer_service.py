"""P2: offer lifecycle + approval quorum."""
import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime.offer_service import (
    add_approval,
    create_offer,
    offer_is_fully_approved,
    record_approval,
    transition_offer,
)
from app.models import (
    Candidate,
    CandidateApplication,
    Organization,
    Role,
)


def _app(db):
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Eng", source="manual")
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand)
    db.flush()
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
    return org, app


def test_create_offer_versions(db):
    org, app = _app(db)
    o1 = create_offer(
        db, organization_id=org.id, application_id=app.id,
        base_salary_amount=120000, currency="USD", pay_frequency="year",
    )
    assert o1.status == "draft" and o1.version == 1 and o1.currency == "USD"
    o2 = create_offer(db, organization_id=org.id, application_id=app.id)
    assert o2.version == 2


def test_happy_path_transitions(db):
    org, app = _app(db)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    transition_offer(db, o, "pending_approval")
    transition_offer(db, o, "approved")  # no approvals -> fully approved
    transition_offer(db, o, "sent")
    assert o.sent_at is not None
    transition_offer(db, o, "accepted")
    assert o.status == "accepted" and o.accepted_at is not None


def test_invalid_transitions(db):
    org, app = _app(db)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    with pytest.raises(HTTPException) as e1:
        transition_offer(db, o, "accepted")  # draft -> accepted not allowed
    assert e1.value.status_code == 409
    transition_offer(db, o, "sent")
    transition_offer(db, o, "accepted")
    with pytest.raises(HTTPException) as e2:
        transition_offer(db, o, "sent")  # accepted is terminal
    assert e2.value.status_code == 409
    with pytest.raises(HTTPException) as e3:
        transition_offer(db, o, "bogus")
    assert e3.value.status_code == 422


def test_approval_quorum_gates_approved(db):
    org, app = _app(db)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    a1 = add_approval(db, o, group_order=0, group_quorum=2)
    a2 = add_approval(db, o, group_order=0, group_quorum=2)
    transition_offer(db, o, "pending_approval")
    assert offer_is_fully_approved(o) is False
    record_approval(db, a1, approved=True)
    with pytest.raises(HTTPException) as e:
        transition_offer(db, o, "approved")  # quorum 2, only 1 approved
    assert e.value.status_code == 409
    record_approval(db, a2, approved=True)
    assert offer_is_fully_approved(o) is True
    transition_offer(db, o, "approved")
    assert o.status == "approved"
