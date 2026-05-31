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
