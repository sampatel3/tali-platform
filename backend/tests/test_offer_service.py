"""ATS slice B: offer lifecycle + approval chain (incl. the three reviewed fixes)."""
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
from app.models.user import User


def _user(db, org_id, email="approver@x.test") -> User:
    u = User(
        email=email, hashed_password="x", is_active=True,
        is_superuser=False, is_verified=False, organization_id=org_id, role="member",
    )
    db.add(u)
    db.flush()
    return u


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


def test_draft_to_sent_shortcut_when_no_approvals(db):
    # An offer with no approval chain may go straight from draft to sent.
    org, app = _app(db)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    transition_offer(db, o, "sent")
    assert o.status == "sent" and o.sent_at is not None


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
    record_approval(db, o, a1, acting_user_id=1, approved=True)
    with pytest.raises(HTTPException) as e:
        transition_offer(db, o, "approved")  # only 1 of 2 rows approved
    assert e.value.status_code == 409
    record_approval(db, o, a2, acting_user_id=1, approved=True)
    assert offer_is_fully_approved(o) is True
    transition_offer(db, o, "approved")
    assert o.status == "approved"


# --- Fix (a): approval-bypass on draft -> sent ---------------------------------

def test_fix_a_draft_to_sent_blocked_when_approvals_pending(db):
    org, app = _app(db)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    add_approval(db, o, group_order=0)  # an approval is required
    with pytest.raises(HTTPException) as e:
        transition_offer(db, o, "sent")  # cannot skip the pending approval
    assert e.value.status_code == 409
    assert o.status == "draft"


def test_fix_a_draft_to_sent_allowed_once_fully_approved(db):
    # Once the approval chain is satisfied the offer follows the normal path.
    org, app = _app(db)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    a = add_approval(db, o, group_order=0)
    transition_offer(db, o, "pending_approval")
    record_approval(db, o, a, acting_user_id=1, approved=True)
    transition_offer(db, o, "approved")
    transition_offer(db, o, "sent")
    assert o.status == "sent"


# --- Fix (b): only the assigned approver may record, only while pending --------

def test_fix_b_only_assigned_approver_may_record(db):
    org, app = _app(db)
    approver = _user(db, org.id)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    a = add_approval(db, o, group_order=0, approver_user_id=approver.id)
    transition_offer(db, o, "pending_approval")
    with pytest.raises(HTTPException) as e:
        record_approval(db, o, a, acting_user_id=approver.id + 999, approved=True)  # not the approver
    assert e.value.status_code == 403
    record_approval(db, o, a, acting_user_id=approver.id, approved=True)  # the assigned approver
    assert a.status == "approved"


def test_fix_b_record_blocked_outside_pending_approval(db):
    org, app = _app(db)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    a = add_approval(db, o, group_order=0)
    # Still in draft — approvals cannot be recorded yet.
    with pytest.raises(HTTPException) as e:
        record_approval(db, o, a, acting_user_id=1, approved=True)
    assert e.value.status_code == 409


# --- Fix (c): sequential groups ------------------------------------------------

def test_fix_c_group_ordering_enforced(db):
    org, app = _app(db)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    g0 = add_approval(db, o, group_order=0)
    g1 = add_approval(db, o, group_order=1)
    transition_offer(db, o, "pending_approval")
    # Recording group 1 before group 0 completes is rejected.
    with pytest.raises(HTTPException) as e:
        record_approval(db, o, g1, acting_user_id=1, approved=True)
    assert e.value.status_code == 409
    # Complete group 0 first, then group 1.
    record_approval(db, o, g0, acting_user_id=1, approved=True)
    record_approval(db, o, g1, acting_user_id=1, approved=True)
    assert offer_is_fully_approved(o) is True


def test_fix_c_partial_group_is_not_fully_approved(db):
    org, app = _app(db)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    a1 = add_approval(db, o, group_order=0)
    add_approval(db, o, group_order=0)  # second row in the same group stays pending
    transition_offer(db, o, "pending_approval")
    record_approval(db, o, a1, acting_user_id=1, approved=True)
    assert offer_is_fully_approved(o) is False  # one row still pending


# --- Codex P2 fixes: approver validation + version-race retry ------------------

def test_add_approval_rejects_unknown_approver(db):
    org, app = _app(db)
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    with pytest.raises(HTTPException) as e:
        add_approval(db, o, group_order=0, approver_user_id=999999)
    assert e.value.status_code == 404


def test_add_approval_rejects_cross_org_approver(db):
    org, app = _app(db)
    other = Organization(name="Other", slug="other-appr")
    db.add(other)
    db.flush()
    outsider = _user(db, other.id, email="outsider@appr.test")
    o = create_offer(db, organization_id=org.id, application_id=app.id)
    with pytest.raises(HTTPException) as e:
        add_approval(db, o, group_order=0, approver_user_id=outsider.id)
    assert e.value.status_code == 404


def test_create_offer_version_race_retries_once(db, monkeypatch):
    """Two overlapping creates read the same max(version); the loser's insert
    hits uq_offers_application_version and retries with a fresh read."""
    import app.domains.assessments_runtime.offer_service as offer_service_module

    org, app = _app(db)
    o1 = create_offer(db, organization_id=org.id, application_id=app.id)
    assert o1.version == 1

    real = offer_service_module._next_offer_version
    calls = {"n": 0}

    def stale_then_real(session, application_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return o1.version  # stale read — this version is already taken
        return real(session, application_id)

    monkeypatch.setattr(offer_service_module, "_next_offer_version", stale_then_real)
    o2 = create_offer(db, organization_id=org.id, application_id=app.id)
    assert calls["n"] == 2  # first insert collided, retry re-read the max
    assert o2.version == 2 and o1.version == 1
    versions = sorted(
        v for (v,) in db.query(offer_service_module.Offer.version)
        .filter(offer_service_module.Offer.application_id == app.id)
        .all()
    )
    assert versions == [1, 2]
