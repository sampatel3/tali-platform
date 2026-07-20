"""auto_correct_stale_verdict — post-rescore in-place correction of the SAFE
subset of stale agent-decision verdict flips."""

from __future__ import annotations

from app.models.agent_decision import AgentDecision
from app.models.cv_score_job import CvScoreJob
from app.models.role import ROLE_KIND_SISTER, Role
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
        idempotency_key=f"t:{role.id}:{app.id}:{decision_type}",
    )
    db.add(d)
    db.flush()
    return d


def test_send_flips_to_reject_is_corrected(db, monkeypatch):
    org, role, _, app = make_world(db, pre_screen=80.0, cv_match=30.0)
    d = _decision(
        db, org, role, app, "send_assessment",
        evidence={
            "decision_source": "policy",
            "decision_trigger": "role_fit_score >= role_fit_min",
            "rule_path": ["rule:fired:role_fit_score >= role_fit_min"],
            "policy_reasoning": "Send the assessment.",
        },
    )
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
    assert (d.evidence or {}).get("decision_trigger") == "role_fit_score <= role_fit_max"
    assert "Send the assessment" not in str(d.evidence)


def test_auto_correct_refuses_old_pending_card_while_latest_score_is_stale(
    db, monkeypatch
):
    org, role, _, app = make_world(db)
    decision = _decision(db, org, role, app, "send_assessment")
    db.add(
        CvScoreJob(
            application_id=int(app.id), role_id=int(role.id), status="stale"
        )
    )
    db.flush()
    monkeypatch.setattr(
        bds,
        "recompute_persisted_verdict",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale score must not be recomputed")
        ),
    )

    assert bds.auto_correct_stale_verdict(db, app=app, role=role) is None
    assert decision.decision_type == "send_assessment"


def test_reject_flips_to_send_is_corrected(db, monkeypatch):
    org, role, _, app = make_world(db, pre_screen=80.0, cv_match=85.0)
    d = _decision(
        db, org, role, app, "reject",
        evidence={
            "decision_source": "policy",
            "decision_trigger": "role_fit_score <= role_fit_max",
            "rule_path": ["rule:fired:role_fit_score <= role_fit_max"],
            "policy_reasoning": "Reject under the old score.",
        },
    )
    monkeypatch.setattr(bds, "recompute_persisted_verdict", lambda *a, **k: "send_assessment")

    out = bds.auto_correct_stale_verdict(db, app=app, role=role)
    db.flush()
    db.refresh(d)
    assert out == "send_assessment"
    assert d.decision_type == "send_assessment"
    assert (d.evidence or {}).get("decision_trigger") == "role_fit_score >= role_fit_min"
    assert "Reject under the old score" not in str(d.evidence)


def test_auto_correct_scopes_shared_application_to_owner_not_two_related_roles(
    db, monkeypatch
):
    org, owner, _, app = make_world(db, pre_screen=80.0, cv_match=30.0)
    related_roles = [
        Role(
            organization_id=int(org.id),
            name=f"Related {index}",
            source="sister",
            role_kind=ROLE_KIND_SISTER,
            ats_owner_role_id=int(owner.id),
        )
        for index in (1, 2)
    ]
    db.add_all(related_roles)
    db.flush()
    owner_decision = _decision(db, org, owner, app, "send_assessment")
    related_decisions = [
        _decision(db, org, related, app, "reject") for related in related_roles
    ]
    monkeypatch.setattr(
        bds, "recompute_persisted_verdict", lambda *args, **kwargs: "reject"
    )

    assert bds.auto_correct_stale_verdict(db, app=app, role=owner) == "reject"
    db.flush()

    assert owner_decision.decision_type == "reject"
    assert [decision.decision_type for decision in related_decisions] == [
        "reject",
        "reject",
    ]


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


def test_new_policy_factor_snapshot_blocks_correction(db, monkeypatch):
    org, role, _, app = make_world(db)
    d = _decision(
        db,
        org,
        role,
        app,
        "reject",
        evidence={
            "decision_source": "policy",
            "decision_trigger": "must_have_blocked",
            "decision_factors": [
                {"label": "Production security clearance", "status": "missing"}
            ],
        },
    )
    monkeypatch.setattr(
        bds, "recompute_persisted_verdict", lambda *a, **k: "send_assessment"
    )

    assert bds.auto_correct_stale_verdict(db, app=app, role=role) is None
    db.refresh(d)
    assert d.decision_type == "reject"
    assert d.evidence["decision_trigger"] == "must_have_blocked"


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
