"""ADR-0010 decision-policy cutover — PARITY PROOF.

tali's verdict cascade is now PRODUCED by mainspring's vendored
``PolicyEngine.evaluate_decision_points`` (via ``decision_policy.mainspring_engine``)
instead of the hand-rolled loop that used to live inside ``engine.evaluate``.
This test proves the cutover is net-ZERO behaviour change on the verdict
``decision_type`` — the field that decides whether a live candidate is sent an
assessment, advanced, or rejected.

Oracle: tali's ORIGINAL cascade. The pure per-point evaluator
``engine._evaluate_decision_point`` and every helper it calls
(``_weighted_score``, ``_confidence_from_inputs``, ``_build_rule_context``,
``_decision_points_to_skip``, ``_eval_condition`` …) were KEPT byte-for-byte by
the cutover (verified against ``origin/main`` at commit time). The only thing
the cutover replaced was the *loop* that walked those per-point verdicts. So
``_original_cascade`` below reconstructs that exact loop (the lines that used to
sit at engine.py ~713-756 on origin/main) and is a faithful stand-in for the
pre-cutover ``evaluate`` verdict — no DB required, both sides run on the same
overlay-applied ``PolicyJson`` + ``DecisionInputs``.

The corpus deliberately spans the cases the old log-only shadow could NOT
translate and flagged as ``gap`` — chiefly the weighted-fallthrough points and
multi-rule passive/shadowed cascades — plus graph-prior confidence and the
abstention-disabled spread case. Every one must match.
"""

from __future__ import annotations

import pytest

from app.decision_policy import engine as eng
from app.decision_policy.engine import (
    DECISION_POINT_ORDER,
    DecisionInputs,
    ManualAction,
    PolicyDecision,
    _decision_points_to_skip,
    _evaluate_decision_point,
)
from app.decision_policy.mainspring_engine import derive_verdict
from app.decision_policy.schema import PolicyJson


# ---------------------------------------------------------------------------
# Oracle: tali's ORIGINAL hand-rolled cascade (kept helper + the replaced loop)
# ---------------------------------------------------------------------------


def _original_cascade(inputs: DecisionInputs, policy: PolicyJson) -> PolicyDecision:
    """Reproduce origin/main ``evaluate``'s post-load cascade verbatim.

    This is the exact loop the cutover removed; it drives the KEPT pure
    per-point evaluator, so it is the pre-cutover verdict by construction.
    """
    skipped = _decision_points_to_skip(inputs.manual_actions)

    last_no_action: PolicyDecision | None = None
    any_manual_skip = False
    final: PolicyDecision | None = None
    for point_name in DECISION_POINT_ORDER:
        point = policy.decision_points.get(point_name)
        if point is None:
            continue
        verdict = _evaluate_decision_point(
            inputs, point_name=point_name, point=point, skipped=skipped
        )
        if verdict.skipped_due_to_manual:
            any_manual_skip = True
        if verdict.decision_type == "skip":
            last_no_action = verdict
            continue
        if verdict.decision_type == "no_action":
            last_no_action = verdict
            continue
        final = verdict
        break

    if final is None:
        if last_no_action is not None:
            if any_manual_skip:
                last_no_action.skipped_due_to_manual = True
            final = last_no_action
        else:
            final = PolicyDecision(
                decision_type="no_action",
                reasoning="No decision points configured for this policy.",
                rule_path=["empty_policy"],
            )
    return final


def _mainspring_verdict(inputs: DecisionInputs, policy: PolicyJson) -> PolicyDecision:
    """Drive the cutover path: mainspring's vendored cascade, reconstructed to
    tali's PolicyDecision shape (mirrors engine._verdict_via_mainspring)."""
    skipped = _decision_points_to_skip(inputs.manual_actions)
    present = [p for p in DECISION_POINT_ORDER if policy.decision_points.get(p)]
    if not present:
        return PolicyDecision(
            decision_type="no_action", rule_path=["empty_policy"]
        )
    ms = derive_verdict(inputs, policy, skip_points=skipped)
    rule_path = list(ms.rule_path or [])
    dp = None
    if rule_path and isinstance(rule_path[0], str) and rule_path[0].startswith("point:"):
        dp = rule_path[0].split(":", 1)[1]
    is_queueing = ms.decision_type not in ("skip", "no_action")
    skip_manual = (not is_queueing) and any(p in skipped for p in present)
    return PolicyDecision(
        decision_type=ms.decision_type,
        confidence=float(ms.confidence),
        reasoning=ms.reasoning,
        rule_path=rule_path,
        decision_point=dp,
        skipped_due_to_manual=skip_manual,
    )


# ---------------------------------------------------------------------------
# Policy corpus
# ---------------------------------------------------------------------------


def _P(decision_points: dict) -> PolicyJson:
    return PolicyJson.model_validate(
        {"schema_version": "v1", "decision_points": decision_points}
    )


def _canonical() -> PolicyJson:
    """The production cold-start shape (bootstrap._default_policy_json),
    role_fit_min=65: auto_reject + passive skips + multi-rule cascade +
    weighted points + per-point confidence floors."""
    return _P(
        {
            "send_assessment": {
                "thresholds": {"role_fit_min": 65.0, "pre_screen_min": 50.0},
                "weights": {"role_fit_score": 0.7, "pre_screen_score": 0.3},
                "rules": [
                    {"if": "must_have_blocked", "then": "auto_reject", "priority": 100},
                    {"if": "has_pending_assessment", "then": "skip", "priority": 90},
                    {"if": "assessment_completed", "then": "skip", "priority": 85},
                    {
                        "if": "role_fit_score >= role_fit_min AND pre_screen_score >= pre_screen_min",
                        "then": "queue_send_assessment",
                        "priority": 50,
                    },
                ],
                "confidence_floor": 0.5,
            },
            "advance_to_interview": {
                "thresholds": {"taali_score_min": 60.0, "assessment_score_min": 50.0},
                "weights": {"taali_score": 0.7, "assessment_score": 0.3},
                "rules": [
                    {
                        "if": "taali_score >= taali_score_min AND assessment_completed",
                        "then": "queue_advance_decision",
                        "priority": 50,
                    }
                ],
                "confidence_floor": 0.6,
            },
            "reject": {
                "thresholds": {"role_fit_max": 30.0},
                "weights": {"role_fit_score": 1.0},
                "rules": [
                    {"if": "pre_screen_auto_reject_eligible", "then": "auto_reject", "priority": 70},
                    {
                        "if": "role_fit_score <= role_fit_max AND no_pending_assessment",
                        "then": "queue_reject_decision",
                        "priority": 50,
                    },
                ],
                "confidence_floor": 0.6,
            },
        }
    )


def _weighted_fallthrough() -> PolicyJson:
    """A weights-only point with NO rules — the case the old shadow logged as a
    'gap' (untranslatable). tali falls through to no_action; mainspring must too."""
    return _P(
        {
            "send_assessment": {
                "thresholds": {"role_fit_min": 65.0},
                "weights": {"role_fit_score": 1.0},
                "rules": [],
            }
        }
    )


def _graph_prior_point() -> PolicyJson:
    """A point whose rule and confidence depend on a graph prior (namespaced)."""
    return _P(
        {
            "advance_to_interview": {
                "thresholds": {"taali_score_min": 60.0},
                "weights": {"taali_score": 0.5, "graph_prior_p_advance": 0.5},
                "rules": [
                    {
                        "if": "taali_score >= taali_score_min AND graph_prior_p_advance >= 50",
                        "then": "queue_advance_decision",
                        "priority": 50,
                    }
                ],
                "confidence_floor": 0.5,
            }
        }
    )


def _passive_then_queue() -> PolicyJson:
    """Higher-priority point with a firing passive skip; a later point queues —
    the cascade fall-through case the old shadow handled by dropping passives."""
    return _P(
        {
            "send_assessment": {
                "rules": [
                    {"if": "assessment_completed == true", "then": "skip", "priority": 100}
                ],
            },
            "advance_to_interview": {
                "thresholds": {"taali_score_min": 80.0},
                "weights": {"taali_score": 1.0},
                "rules": [
                    {"if": "taali_score >= taali_score_min", "then": "queue_advance_decision", "priority": 100}
                ],
                "confidence_floor": 0.0,
            },
        }
    )


def _shadowed_rule_same_point() -> PolicyJson:
    """A higher-priority passive rule shadows a lower-priority queue rule in the
    SAME point — the queue rule must never be reached."""
    return _P(
        {
            "advance_to_interview": {
                "thresholds": {"taali_score_min": 80.0},
                "weights": {"taali_score": 1.0},
                "rules": [
                    {"if": "assessment_completed == true", "then": "skip", "priority": 100},
                    {"if": "taali_score >= taali_score_min", "then": "queue_advance_decision", "priority": 50},
                ],
            }
        }
    )


def _floor_blocked() -> PolicyJson:
    """A queue rule fires but the confidence floor is high and only one of two
    weighted signals is present — tali demotes to no_action; mainspring must too."""
    return _P(
        {
            "send_assessment": {
                "thresholds": {"role_fit_min": 65.0},
                "weights": {"role_fit_score": 0.5, "pre_screen_score": 0.5},
                "rules": [
                    {"if": "role_fit_score >= role_fit_min", "then": "queue_send_assessment", "priority": 50}
                ],
                "confidence_floor": 0.9,
            }
        }
    )


_POLICIES = {
    "canonical": _canonical(),
    "weighted_fallthrough": _weighted_fallthrough(),
    "graph_prior": _graph_prior_point(),
    "passive_then_queue": _passive_then_queue(),
    "shadowed_rule_same_point": _shadowed_rule_same_point(),
    "floor_blocked": _floor_blocked(),
}


# ---------------------------------------------------------------------------
# Input corpus
# ---------------------------------------------------------------------------


def _mk(**kw) -> DecisionInputs:
    base = dict(application_id=1, role_id=2, organization_id=3)
    base.update(kw)
    return DecisionInputs(**base)


_INPUTS = {
    "strong": _mk(
        scores={"role_fit_score": 85.0, "pre_screen_score": 75.0},
        flags={"has_pending_assessment": False},
    ),
    "weak": _mk(
        scores={"role_fit_score": 8.0, "pre_screen_score": 5.0},
        flags={"no_pending_assessment": True},
    ),
    "borderline": _mk(scores={"role_fit_score": 65.0, "pre_screen_score": 50.0}),
    "sparse_one_signal": _mk(scores={"role_fit_score": 90.0}),
    "must_have_blocked": _mk(
        scores={"role_fit_score": 90.0, "pre_screen_score": 80.0},
        flags={"must_have_blocked": True},
    ),
    "pending_assessment": _mk(
        scores={"role_fit_score": 90.0, "pre_screen_score": 80.0},
        flags={"has_pending_assessment": True},
    ),
    "assessment_completed_strong": _mk(
        scores={
            "role_fit_score": 70.0,
            "pre_screen_score": 60.0,
            "taali_score": 88.0,
            "assessment_score": 75.0,
        },
        flags={"assessment_completed": True, "no_pending_assessment": True},
    ),
    "pre_screen_auto_reject": _mk(
        scores={"role_fit_score": 20.0},
        flags={"pre_screen_auto_reject_eligible": True, "no_pending_assessment": True},
    ),
    "spread_high_taali_low_fit": _mk(
        scores={"taali_score": 95.0, "role_fit_score": 5.0},
        flags={"assessment_completed": True},
    ),
    "graph_prior_present_high": _mk(
        scores={"taali_score": 80.0},
        graph_priors={"p_advance": 0.9, "confidence": 0.8},
    ),
    "graph_prior_coldstart_zero_conf": _mk(
        scores={"taali_score": 80.0},
        graph_priors={"p_advance": 0.9, "confidence": 0.0},
    ),
    "graph_prior_absent": _mk(scores={"taali_score": 80.0}),
    "manual_rejected": _mk(
        scores={"role_fit_score": 8.0, "pre_screen_score": 5.0},
        flags={"no_pending_assessment": True},
        manual_actions=[ManualAction(kind="rejected", timestamp_iso="2026-01-01T00:00:00Z")],
    ),
    "manual_advanced": _mk(
        scores={"role_fit_score": 8.0},
        flags={"no_pending_assessment": True},
        manual_actions=[ManualAction(kind="advanced", timestamp_iso="2026-01-01T00:00:00Z")],
    ),
    "manual_sent_assessment": _mk(
        scores={"role_fit_score": 85.0, "pre_screen_score": 75.0},
        manual_actions=[ManualAction(kind="sent_assessment", timestamp_iso="2026-01-01T00:00:00Z")],
    ),
    "empty_scores": _mk(scores={}),
}


@pytest.mark.parametrize("policy_name", list(_POLICIES))
@pytest.mark.parametrize("input_name", list(_INPUTS))
def test_mainspring_matches_original_cascade(policy_name, input_name):
    policy = _POLICIES[policy_name]
    inputs = _INPUTS[input_name]

    original = _original_cascade(inputs, policy)
    mainspring = _mainspring_verdict(inputs, policy)

    assert mainspring.decision_type == original.decision_type, (
        f"VERDICT DIVERGENCE policy={policy_name} input={input_name}: "
        f"mainspring={mainspring.decision_type!r} != original={original.decision_type!r}"
    )
    # decision_point + manual-skip flag must also round-trip (audit-trail shape).
    assert mainspring.decision_point == original.decision_point, (
        f"decision_point divergence policy={policy_name} input={input_name}: "
        f"{mainspring.decision_point!r} != {original.decision_point!r}"
    )
    assert mainspring.skipped_due_to_manual == original.skipped_due_to_manual


def test_corpus_covers_every_decision_type():
    """Sanity: the corpus actually exercises queue/auto_reject/skip/no_action,
    not just one trivial verdict — so the parity assertion has teeth."""
    seen = set()
    for policy in _POLICIES.values():
        for inputs in _INPUTS.values():
            seen.add(_original_cascade(inputs, policy).decision_type)
    # At minimum the live-meaningful verdicts must appear.
    assert {"queue_send_assessment", "queue_advance_decision", "queue_reject_decision",
            "auto_reject", "no_action"}.issubset(seen), seen
