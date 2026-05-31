"""Decision-policy convergence shadow comparator (ADR-0010 cut #2).

Behind a flag, every deterministic policy verdict tali emits is also
re-derived through mainspring's vendored ``PolicyEngine`` (a DomainSpec
translated from the same ``DecisionPolicyRow`` + inputs) and a verdict
agreement/disagreement diff is logged. These lock: no-op when off; the
agree / disagree / gap statuses; and that it never raises (must not affect
the verdict).
"""
from __future__ import annotations

import logging

from app.decision_policy.engine import DecisionInputs, PolicyDecision
from app.decision_policy.schema import PolicyJson
from app.platform.config import settings
from app.services.mainspring_policy_shadow import shadow_compare_verdict
from vendor.mainspring_policy.policy import PolicyEngine, Rule, Verdict
from vendor.mainspring_policy.signals import Signal, SignalBundle


_SHADOW_EVENTS = lambda caplog: [
    r for r in caplog.records if getattr(r, "event", None) == "mainspring_policy_shadow"
]


def _send_policy() -> PolicyJson:
    """A minimal one-rule policy: role_fit_score >= floor -> send_assessment."""
    return PolicyJson.model_validate(
        {
            "schema_version": "v1",
            "decision_points": {
                "send_assessment": {
                    "thresholds": {"role_fit_min": 65.0},
                    "rules": [
                        {
                            "if": "role_fit_score >= role_fit_min",
                            "then": "queue_send_assessment",
                            "priority": 100,
                            "reason_template": "strong role fit",
                        }
                    ],
                }
            },
        }
    )


def _inputs(role_fit: float) -> DecisionInputs:
    return DecisionInputs(
        application_id=1,
        role_id=2,
        organization_id=3,
        scores={"role_fit_score": role_fit},
    )


def test_vendored_engine_emits_a_verdict():
    # The vendored mainspring PolicyEngine must run end-to-end ORM-free.
    engine = PolicyEngine(
        rules=[Rule(name="r", when=lambda ctx: ctx.get("s", 0) >= 50, then="queue_x", priority=1)],
        thresholds={},
    )
    bundle = SignalBundle()
    bundle.add(Signal(name="s", value=80.0, confidence=1.0))
    v = engine.evaluate("e1", bundle, flags={})
    assert isinstance(v, Verdict)
    assert v.decision_type == "queue_x"


def test_shadow_is_noop_when_flag_off(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_POLICY_SHADOW", False, raising=False)
    tali = PolicyDecision(decision_type="queue_send_assessment", decision_point="send_assessment")
    with caplog.at_level(logging.INFO, logger="taali.policy.shadow"):
        shadow_compare_verdict(inputs=_inputs(90.0), policy=_send_policy(), tali_verdict=tali)
    assert _SHADOW_EVENTS(caplog) == []


def test_shadow_logs_agree_when_engines_match(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_POLICY_SHADOW", True, raising=False)
    # role_fit 90 >= floor 65 -> both engines queue_send_assessment.
    tali = PolicyDecision(decision_type="queue_send_assessment", decision_point="send_assessment")
    with caplog.at_level(logging.INFO, logger="taali.policy.shadow"):
        shadow_compare_verdict(inputs=_inputs(90.0), policy=_send_policy(), tali_verdict=tali)
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "agree"
    assert evs[0].tali_decision_type == "queue_send_assessment"
    assert evs[0].mainspring_decision_type == "queue_send_assessment"


def test_shadow_logs_disagree_when_engines_differ(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_POLICY_SHADOW", True, raising=False)
    # Mainspring re-derives from the SAME rule: role_fit 90 fires -> send.
    # We hand it a deliberately-wrong tali verdict to prove a mismatch logs
    # as 'disagree' (the parity signal the shadow exists to surface).
    tali = PolicyDecision(decision_type="no_action", decision_point="send_assessment")
    with caplog.at_level(logging.INFO, logger="taali.policy.shadow"):
        shadow_compare_verdict(inputs=_inputs(90.0), policy=_send_policy(), tali_verdict=tali)
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "disagree"
    assert evs[0].mainspring_decision_type == "queue_send_assessment"
    assert evs[0].tali_decision_type == "no_action"


def test_shadow_logs_gap_for_untranslatable_weighted_fallthrough(caplog, monkeypatch):
    """A weights-only point (no rules) has no mainspring equivalent — tali
    falls through to a weighted-score verdict the substrate engine can't
    reproduce. That logs as 'gap' (a translation TODO), not 'disagree'."""
    monkeypatch.setattr(settings, "MAINSPRING_POLICY_SHADOW", True, raising=False)
    policy = PolicyJson.model_validate(
        {
            "schema_version": "v1",
            "decision_points": {
                "send_assessment": {
                    "thresholds": {"role_fit_min": 65.0},
                    "weights": {"role_fit_score": 1.0},
                    "rules": [],
                }
            },
        }
    )
    tali = PolicyDecision(decision_type="no_action", decision_point="send_assessment")
    with caplog.at_level(logging.INFO, logger="taali.policy.shadow"):
        shadow_compare_verdict(inputs=_inputs(90.0), policy=policy, tali_verdict=tali)
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "gap"
    assert any("weighted_fallthrough" in u for u in evs[0].untranslatable)


def test_shadow_never_raises_on_bad_input(monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_POLICY_SHADOW", True, raising=False)
    # Garbage that would break translation/evaluation must be swallowed.
    shadow_compare_verdict(inputs=None, policy=None, tali_verdict=None)


# --- P2 FIX 1: mainspring's abstention overlay must be DISABLED in the shadow --


def _advance_policy() -> PolicyJson:
    """advance fires on a high taali_score alone (the verdict tali queues)."""
    return PolicyJson.model_validate(
        {
            "schema_version": "v1",
            "decision_points": {
                "advance_to_interview": {
                    "thresholds": {"taali_min": 80.0},
                    "rules": [
                        {
                            "if": "taali_score >= taali_min",
                            "then": "queue_advance_decision",
                            "priority": 100,
                            "reason_template": "strong overall fit",
                        }
                    ],
                }
            },
        }
    )


def _spread_inputs() -> DecisionInputs:
    # High taali_score (90) but low role_fit_score (10): a >35 spread that
    # mainspring's default (enabled) EscalationConfig would rewrite to
    # `escalate`. tali has no abstention overlay and simply queues advance.
    return DecisionInputs(
        application_id=1,
        role_id=2,
        organization_id=3,
        scores={"taali_score": 90.0, "role_fit_score": 10.0},
    )


def test_shadow_does_not_escalate_low_confidence_spread(caplog, monkeypatch):
    """REGRESSION (P2 #1): a candidate tali correctly queues (high taali_score,
    low role_fit_score) must NOT be logged as a false `disagree` because
    mainspring's abstention overlay rewrote the matching rule to `escalate`.
    The shadow builds the vendored engine with EscalationConfig(enabled=False),
    so the spread no longer triggers escalation and the engines AGREE."""
    monkeypatch.setattr(settings, "MAINSPRING_POLICY_SHADOW", True, raising=False)
    tali = PolicyDecision(
        decision_type="queue_advance_decision", decision_point="advance_to_interview"
    )
    with caplog.at_level(logging.INFO, logger="taali.policy.shadow"):
        shadow_compare_verdict(
            inputs=_spread_inputs(), policy=_advance_policy(), tali_verdict=tali
        )
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "agree"
    assert evs[0].mainspring_decision_type == "queue_advance_decision"


def test_abstention_disabled_in_translated_engine():
    """Unit-level guard: the engine the shadow builds carries a DISABLED
    abstention config (so this can't silently regress to the default)."""
    from app.services.mainspring_policy_shadow import _translate_to_engine
    from vendor.mainspring_policy.policy import EscalationConfig, PolicyEngine, Rule

    engine, _ = _translate_to_engine(
        _advance_policy(), _spread_inputs(), PolicyEngine, Rule, EscalationConfig
    )
    assert engine.escalation.enabled is False


# --- P2 FIX 2: a passive higher-priority rule must NOT short-circuit the cascade


def test_shadow_passive_rule_does_not_short_circuit_cascade(caplog, monkeypatch):
    """REGRESSION (P2 #2): tali records a higher-priority point's passive
    skip/no_action and CONTINUES to later points; a later point that queues is
    the final verdict. The vendored PolicyEngine returns immediately on the
    first firing SKIP/NO_ACTION, so flattening that passive rule would log a
    false mismatch (`skip` vs `queue_advance_decision`). The shadow emulates
    tali's fall-through by dropping passive rules, so the engines AGREE on the
    later queueing verdict."""
    monkeypatch.setattr(settings, "MAINSPRING_POLICY_SHADOW", True, raising=False)
    policy = PolicyJson.model_validate(
        {
            "schema_version": "v1",
            "decision_points": {
                # Higher-priority point with a PASSIVE rule that fires.
                "send_assessment": {
                    "rules": [
                        {
                            "if": "assessment_completed == true",
                            "then": "skip",
                            "priority": 100,
                            "reason_template": "already assessed",
                        }
                    ],
                },
                # Lower-priority point that queues — tali's actual final verdict.
                "advance_to_interview": {
                    "thresholds": {"taali_min": 80.0},
                    "rules": [
                        {
                            "if": "taali_score >= taali_min",
                            "then": "queue_advance_decision",
                            "priority": 100,
                        }
                    ],
                },
            },
        }
    )
    inputs = DecisionInputs(
        application_id=1,
        role_id=2,
        organization_id=3,
        scores={"taali_score": 90.0},
        flags={"assessment_completed": True},
    )
    # tali falls through send's `skip` to advance's queue.
    tali = PolicyDecision(
        decision_type="queue_advance_decision", decision_point="advance_to_interview"
    )
    with caplog.at_level(logging.INFO, logger="taali.policy.shadow"):
        shadow_compare_verdict(inputs=inputs, policy=policy, tali_verdict=tali)
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "agree"
    assert evs[0].mainspring_decision_type == "queue_advance_decision"


def test_shadow_real_disagreement_still_surfaces_under_passive_handling(
    caplog, monkeypatch
):
    """The passive-rule emulation must not mask a GENUINE divergence. Same
    cascade shape, but tali (wrongly, for the test) reports `no_action` while
    mainspring queues advance — the shadow must still log `disagree`."""
    monkeypatch.setattr(settings, "MAINSPRING_POLICY_SHADOW", True, raising=False)
    policy = PolicyJson.model_validate(
        {
            "schema_version": "v1",
            "decision_points": {
                "send_assessment": {
                    "rules": [
                        {"if": "assessment_completed == true", "then": "skip", "priority": 100}
                    ],
                },
                "advance_to_interview": {
                    "thresholds": {"taali_min": 80.0},
                    "rules": [
                        {"if": "taali_score >= taali_min", "then": "queue_advance_decision", "priority": 100}
                    ],
                },
            },
        }
    )
    inputs = DecisionInputs(
        application_id=1,
        role_id=2,
        organization_id=3,
        scores={"taali_score": 90.0},
        flags={"assessment_completed": True},
    )
    # `no_action` is NOT a passive-equivalent of a *queueing* verdict — these
    # genuinely differ (no_action vs queue_advance_decision).
    tali = PolicyDecision(decision_type="no_action", decision_point="advance_to_interview")
    with caplog.at_level(logging.INFO, logger="taali.policy.shadow"):
        shadow_compare_verdict(inputs=inputs, policy=policy, tali_verdict=tali)
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "disagree"
    assert evs[0].mainspring_decision_type == "queue_advance_decision"
    assert evs[0].tali_decision_type == "no_action"


def test_shadow_passive_rule_shadows_lower_priority_rule_same_point(caplog, monkeypatch):
    """A higher-priority PASSIVE rule shadows a lower-priority QUEUE rule in the
    SAME point: tali walks rules priority-desc, the passive rule fires first and
    wins the point (skip → fall through), so the lower-priority queue rule is
    never reached. The shadow must not emit that shadowed rule, else mainspring
    would queue inside this point where tali skipped it. With only one point and
    the passive rule firing, tali falls through to NO_ACTION and mainspring
    (no rule emitted for the point) returns NO_ACTION too → agree (both passive).
    """
    monkeypatch.setattr(settings, "MAINSPRING_POLICY_SHADOW", True, raising=False)
    policy = PolicyJson.model_validate(
        {
            "schema_version": "v1",
            "decision_points": {
                "advance_to_interview": {
                    "thresholds": {"taali_min": 80.0},
                    "rules": [
                        # Higher priority: passive skip that fires.
                        {"if": "assessment_completed == true", "then": "skip", "priority": 100},
                        # Lower priority: a queue rule tali never reaches.
                        {"if": "taali_score >= taali_min", "then": "queue_advance_decision", "priority": 50},
                    ],
                }
            },
        }
    )
    inputs = DecisionInputs(
        application_id=1,
        role_id=2,
        organization_id=3,
        scores={"taali_score": 90.0},
        flags={"assessment_completed": True},
    )
    # tali's passive skip wins the only point; no later point → no_action.
    tali = PolicyDecision(decision_type="no_action", decision_point="advance_to_interview")
    with caplog.at_level(logging.INFO, logger="taali.policy.shadow"):
        shadow_compare_verdict(inputs=inputs, policy=policy, tali_verdict=tali)
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "agree"
    # The shadowed queue rule must NOT have fired in mainspring.
    assert evs[0].mainspring_decision_type in ("no_action", "skip")
