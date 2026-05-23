"""Single-threshold banding: the effective-threshold overlay collapses the
reject ceiling and send floor onto one boundary, so every candidate is
decided (no gap) and the recruiter's threshold drives the engine live.
"""

from __future__ import annotations

from app.decision_policy.engine import DecisionInputs, evaluate

from .conftest import bootstrap, make_org, make_role


def _inputs(org, role, *, role_fit, pre_screen=70.0, eff=None, flags=None):
    return DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"role_fit_score": role_fit, "pre_screen_score": pre_screen},
        flags=flags or {"no_pending_assessment": True, "has_pending_assessment": False},
        effective_role_fit_threshold=eff,
    )


def test_above_threshold_sends(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    v = evaluate(_inputs(org, role, role_fit=60.0, eff=50.0), db=db)
    assert v.decision_type == "queue_send_assessment"


def test_below_threshold_rejects(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    v = evaluate(_inputs(org, role, role_fit=40.0, eff=50.0), db=db)
    assert v.decision_type == "queue_reject_decision"


def test_at_threshold_sends_not_rejects(db):
    """Boundary candidate (== threshold) goes to send (send point is
    evaluated before reject)."""
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    v = evaluate(_inputs(org, role, role_fit=50.0, eff=50.0), db=db)
    assert v.decision_type == "queue_send_assessment"


def test_gap_is_closed_no_silent_no_action(db):
    """The score that used to fall in the 30..65 gap (no_action) now lands
    on a side of the single boundary — every candidate gets a verdict."""
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    # 45 with threshold 50 -> reject (not no_action)
    v = evaluate(_inputs(org, role, role_fit=45.0, eff=50.0), db=db)
    assert v.decision_type != "no_action"
    assert v.decision_type == "queue_reject_decision"


def test_decoupling_fixed_effective_threshold_overrides_frozen_policy(db):
    """Regression for the core bug: the stored policy's send floor is 65
    (default), but a role threshold of 30 must drive the agent. A score of
    40 should SEND (overlay won), not fall in the old 30..65 gap."""
    org = make_org(db, default_score_threshold=65)  # frozen policy role_fit_min=65
    role = make_role(db, org=org, score_threshold=30)
    bootstrap(db, org)
    # Without the overlay this is the gap -> no_action; with eff=30 it sends.
    v = evaluate(_inputs(org, role, role_fit=40.0, eff=30.0), db=db)
    assert v.decision_type == "queue_send_assessment"


def test_none_effective_falls_back_to_policy_gap(db):
    """When no threshold is resolvable (eff=None), the engine keeps the
    stored policy thresholds — preserving the 'ask the recruiter' gate."""
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    # role_fit 45 sits in the policy's 30..65 gap -> no queueable verdict.
    v = evaluate(_inputs(org, role, role_fit=45.0, eff=None), db=db)
    assert v.decision_type in {"no_action", "skip"}


def test_must_have_blocked_auto_rejects_regardless_of_score(db):
    """Higher-priority hard rule still wins over the threshold band."""
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    v = evaluate(
        _inputs(org, role, role_fit=90.0, eff=50.0, flags={"must_have_blocked": True, "no_pending_assessment": True}),
        db=db,
    )
    assert v.decision_type == "auto_reject"
