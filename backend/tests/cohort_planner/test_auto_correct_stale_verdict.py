"""auto_correct_stale_verdict — post-rescore in-place correction of the SAFE
subset of stale agent-decision verdict flips."""

from __future__ import annotations

from app.models.agent_decision import AgentDecision
from app.services import bulk_decision_service as bds

from .conftest import make_world


def _decision(db, org, role, app, decision_type, *, reasoning="r", evidence=None):
    d = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type=decision_type,
        recommendation=decision_type,
        status="pending",
        reasoning=reasoning,
        confidence=0.7,
        model_version="claude-sonnet-4-5",
        prompt_version="agent.v10",
        evidence=evidence or {},
        idempotency_key=f"t:{app.id}:{decision_type}",
    )
    db.add(d)
    db.flush()
    return d


def test_send_flips_to_reject_is_corrected(db, monkeypatch):
    org, role, _, app = make_world(db)
    d = _decision(db, org, role, app, "send_assessment")
    monkeypatch.setattr(bds, "recompute_persisted_verdict", lambda *a, **k: "reject")

    out = bds.auto_correct_stale_verdict(db, app=app, role=role)
    db.flush()

    assert out == "reject"
    db.refresh(d)
    assert d.decision_type == "reject"
    assert d.recommendation == "reject"
    assert d.status == "pending"  # corrected, not resolved
    assert d.model_version == "bulk-deterministic"
    assert (d.evidence or {}).get("auto_corrected_from") == "send_assessment"


def test_reject_flips_to_send_is_corrected(db, monkeypatch):
    org, role, _, app = make_world(db)
    d = _decision(db, org, role, app, "reject")
    monkeypatch.setattr(bds, "recompute_persisted_verdict", lambda *a, **k: "send_assessment")

    out = bds.auto_correct_stale_verdict(db, app=app, role=role)
    db.flush()
    db.refresh(d)
    assert out == "send_assessment"
    assert d.decision_type == "send_assessment"


def test_no_flip_is_left_untouched(db, monkeypatch):
    org, role, _, app = make_world(db)
    d = _decision(db, org, role, app, "reject")
    monkeypatch.setattr(bds, "recompute_persisted_verdict", lambda *a, **k: "reject")
    assert bds.auto_correct_stale_verdict(db, app=app, role=role) is None
    db.refresh(d)
    assert d.decision_type == "reject"
    assert d.model_version == "claude-sonnet-4-5"  # unchanged


def test_location_gate_blocks_correction(db, monkeypatch):
    org, role, _, app = make_world(db)
    d = _decision(
        db, org, role, app, "reject",
        reasoning="Role-fit below bar AND candidate is India-based with no relocation.",
    )
    monkeypatch.setattr(bds, "recompute_persisted_verdict", lambda *a, **k: "send_assessment")
    assert bds.auto_correct_stale_verdict(db, app=app, role=role) is None
    db.refresh(d)
    assert d.decision_type == "reject"  # left flagged for the recruiter


def test_structured_must_have_gap_blocks_correction(db, monkeypatch):
    org, role, _, app = make_world(db)
    d = _decision(
        db, org, role, app, "reject",
        evidence={"must_have_gaps": ["AWS Glue production not evidenced"]},
    )
    monkeypatch.setattr(bds, "recompute_persisted_verdict", lambda *a, **k: "send_assessment")
    assert bds.auto_correct_stale_verdict(db, app=app, role=role) is None
    db.refresh(d)
    assert d.decision_type == "reject"


def test_advance_to_interview_is_never_touched(db, monkeypatch):
    org, role, _, app = make_world(db)
    d = _decision(db, org, role, app, "advance_to_interview")
    monkeypatch.setattr(bds, "recompute_persisted_verdict", lambda *a, **k: "reject")
    assert bds.auto_correct_stale_verdict(db, app=app, role=role) is None
    db.refresh(d)
    assert d.decision_type == "advance_to_interview"


def test_flip_to_advance_is_not_auto_corrected(db, monkeypatch):
    org, role, _, app = make_world(db)
    d = _decision(db, org, role, app, "send_assessment")
    monkeypatch.setattr(bds, "recompute_persisted_verdict", lambda *a, **k: "advance_to_interview")
    assert bds.auto_correct_stale_verdict(db, app=app, role=role) is None
    db.refresh(d)
    assert d.decision_type == "send_assessment"
