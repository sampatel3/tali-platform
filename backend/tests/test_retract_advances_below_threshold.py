"""Pending advance/send cards for candidates below the enforced Stage-1 gate
must be retracted before full scoring; the gate reconcile then emits the
matching skip_assessment_reject card.

Pins retract_advances_below_threshold in isolation.
"""
from app.models.organization import Organization
from app.models.role import Role
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.agent_decision import AgentDecision
from app.services.pre_screen_decision_emitter import (
    retract_advances_below_threshold,
)

# Uses the shared function-scoped ``db`` fixture from conftest.py (create_all
# + drop_all per test), same as test_pre_screen_decision_emitter.py — a local
# fixture would leak state across files under the in-memory SQLite engine.


def _seed_org_role(
    db,
    *,
    threshold=70,
    auto_reject=False,
    auto_reject_pre_screen=False,
    agentic=True,
):
    org = Organization(name="Acme", workable_config={"auto_reject_enabled": auto_reject})
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Engineer",
        score_threshold=threshold,
        auto_reject=bool(auto_reject),
        auto_reject_pre_screen=bool(auto_reject_pre_screen),
        agentic_mode_enabled=agentic,
    )
    db.add(role)
    db.flush()
    return org, role


def _seed_app(db, *, org, role, score=None, stage=None, outcome="open"):
    cand = Candidate(organization_id=org.id, full_name="Jane Doe", email="jane@example.com")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        pre_screen_score_100=score,
        genuine_pre_screen_score_100=score,
        workable_stage=stage,
        application_outcome=outcome,
    )
    db.add(app)
    db.flush()
    return app


def _seed_decision(db, *, org, role, app, dtype="advance_to_interview", status="pending"):
    d = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type=dtype,
        recommendation=dtype,
        reasoning="seed",
        model_version="test",
        prompt_version="test",
        idempotency_key=f"test:{app.id}:{dtype}",
        status=status,
    )
    db.add(d)
    db.flush()
    return d


def test_retracts_advance_below_stage1_gate(db):
    org, role = _seed_org_role(db, threshold=70)
    app = _seed_app(db, org=org, role=role, score=20)
    d = _seed_decision(db, org=org, role=role, app=app, dtype="advance_to_interview")

    out = retract_advances_below_threshold(
        db, role=role, organization_id=org.id, threshold=70.0
    )
    db.refresh(d)
    assert out["discarded"] == 1
    assert d.status == "discarded"
    assert d.resolution_note and "Stage-1 gate" in d.resolution_note


def test_keeps_advance_above_stage1_gate(db):
    org, role = _seed_org_role(db, threshold=70)
    app = _seed_app(db, org=org, role=role, score=35)
    d = _seed_decision(db, org=org, role=role, app=app, dtype="send_assessment")

    out = retract_advances_below_threshold(
        db, role=role, organization_id=org.id, threshold=70.0
    )
    db.refresh(d)
    assert out["discarded"] == 0
    assert d.status == "pending"


def test_send_assessment_and_resend_are_in_scope(db):
    org, role = _seed_org_role(db, threshold=70)
    for dtype in ("send_assessment", "resend_assessment_invite"):
        app = _seed_app(db, org=org, role=role, score=10)
        _seed_decision(db, org=org, role=role, app=app, dtype=dtype)

    out = retract_advances_below_threshold(
        db, role=role, organization_id=org.id, threshold=70.0
    )
    assert out["discarded"] == 2


def test_skip_assessment_reject_card_is_untouched(db):
    # The reject card is the reject reconcile's job, not ours.
    org, role = _seed_org_role(db, threshold=70)
    app = _seed_app(db, org=org, role=role, score=10)
    d = _seed_decision(db, org=org, role=role, app=app, dtype="skip_assessment_reject")

    out = retract_advances_below_threshold(
        db, role=role, organization_id=org.id, threshold=70.0
    )
    db.refresh(d)
    assert out["discarded"] == 0
    assert d.status == "pending"


def test_post_handover_workable_stage_is_retracted_like_everyone(db):
    # A post-handover Workable stage no longer exempts the candidate: the
    # stale advance is retracted so the reject reconcile can put the
    # deterministic reject card in its place (approve surfaces warn the
    # recruiter that acting on it hits someone already advanced in Workable).
    org, role = _seed_org_role(db, threshold=70)
    app = _seed_app(db, org=org, role=role, score=10, stage="hired")
    d = _seed_decision(db, org=org, role=role, app=app, dtype="advance_to_interview")

    out = retract_advances_below_threshold(
        db, role=role, organization_id=org.id, threshold=70.0
    )
    db.refresh(d)
    assert out["discarded"] == 1
    assert d.status == "discarded"


def test_none_caller_threshold_still_enforces_stage1_gate(db):
    org, role = _seed_org_role(db, threshold=None)
    app = _seed_app(db, org=org, role=role, score=10)
    d = _seed_decision(db, org=org, role=role, app=app)

    out = retract_advances_below_threshold(
        db, role=role, organization_id=org.id, threshold=None
    )
    db.refresh(d)
    assert out["discarded"] == 1
    assert d.status == "discarded"


def test_agent_off_role_is_noop(db):
    org, role = _seed_org_role(db, threshold=70, agentic=False)
    app = _seed_app(db, org=org, role=role, score=10)
    d = _seed_decision(db, org=org, role=role, app=app)

    out = retract_advances_below_threshold(
        db, role=role, organization_id=org.id, threshold=70.0
    )
    db.refresh(d)
    assert out["discarded"] == 0
    assert d.status == "pending"


def test_pre_screen_auto_reject_role_is_noop(db):
    # Pre-screen auto-reject roles disqualify directly, not via the Hub.
    org, role = _seed_org_role(
        db, threshold=70, auto_reject_pre_screen=True
    )
    app = _seed_app(db, org=org, role=role, score=10)
    d = _seed_decision(db, org=org, role=role, app=app)

    out = retract_advances_below_threshold(
        db, role=role, organization_id=org.id, threshold=70.0
    )
    db.refresh(d)
    assert out["discarded"] == 0
    assert d.status == "pending"


def test_scored_auto_reject_does_not_disable_pre_screen_reconciliation(db):
    org, role = _seed_org_role(db, threshold=70, auto_reject=True)
    app = _seed_app(db, org=org, role=role, score=10)
    d = _seed_decision(db, org=org, role=role, app=app)

    out = retract_advances_below_threshold(
        db, role=role, organization_id=org.id, threshold=70.0
    )
    db.refresh(d)
    assert out["discarded"] == 1
    assert d.status == "discarded"


def test_already_resolved_card_is_untouched(db):
    org, role = _seed_org_role(db, threshold=70)
    app = _seed_app(db, org=org, role=role, score=10)
    d = _seed_decision(db, org=org, role=role, app=app, status="approved")

    out = retract_advances_below_threshold(
        db, role=role, organization_id=org.id, threshold=70.0
    )
    db.refresh(d)
    assert out["discarded"] == 0
    assert d.status == "approved"
