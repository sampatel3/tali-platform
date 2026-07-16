from types import SimpleNamespace

from app.services.decision_evidence_service import (
    blocked_must_have_requirements,
    must_have_blocked,
)
from app.services.decision_presentation_service import (
    build_decision_explanation,
    candidate_summary_for,
    normalize_candidate_summary,
)


def _app(rows):
    return SimpleNamespace(
        cv_match_details={"requirements_assessment": rows},
        pre_screen_evidence=None,
        screening_answers=None,
    )


def _decision(*, decision_type="reject", evidence=None, reasoning="Candidate narrative"):
    return SimpleNamespace(
        decision_type=decision_type,
        evidence=evidence or {},
        reasoning=reasoning,
        model_version="bulk-deterministic",
    )


def test_holistic_inferred_priority_does_not_activate_hard_reject():
    app = _app(
        [
            {
                "requirement_id": "holistic_3",
                "requirement": "Knowledge graph development",
                "priority": "must_have",
                "status": "missing",
            }
        ]
    )

    assert must_have_blocked(app) is False
    assert blocked_must_have_requirements(app) == []


def test_explicit_holistic_blocker_remains_authoritative():
    app = _app(
        [
            {
                "requirement_id": "holistic_3",
                "requirement": "Knowledge graph development",
                "priority": "must_have",
                "status": "missing",
                "blocker": True,
            }
        ]
    )

    assert must_have_blocked(app) is True


def test_must_have_explanation_names_frozen_factors_and_score_override():
    decision = _decision(
        evidence={
            "decision_source": "policy",
            "decision_trigger": "must_have_blocked",
            "role_fit_score": 72,
            "effective_threshold": 55,
            "decision_factors": [
                {"label": "Knowledge graph development", "status": "missing", "priority": "must_have"},
                {"label": "Ontology and taxonomy design", "status": "missing", "priority": "must_have"},
            ],
        }
    )

    result = build_decision_explanation(decision, _app([]))

    assert result["source"] == "policy"
    assert result["summary"] == "Reject recommended because 2 must-have requirements were marked missing."
    assert [item["label"] for item in result["factors"]] == [
        "Knowledge graph development",
        "Ontology and taxonomy design",
    ]
    assert result["context"] == (
        "The 72 role-fit score cleared the 55 threshold; the hard must-have rule took priority."
    )
    assert result["score_context"]["score_was_decisive"] is False


def test_must_have_factors_cap_at_five_but_factors_total_keeps_the_real_count():
    rows = [
        {"label": f"Requirement {i}", "status": "missing", "priority": "must_have"}
        for i in range(1, 8)
    ]
    decision = _decision(
        evidence={
            "decision_source": "policy",
            "decision_trigger": "must_have_blocked",
            "decision_factors": rows,
        }
    )

    result = build_decision_explanation(decision, _app([]))

    assert len(result["factors"]) == 5
    assert result["factors_total"] == 7
    assert result["summary"] == "Reject recommended because 7 must-have requirements were marked missing."


def test_unknown_hard_requirement_is_described_as_unverified():
    decision = _decision(
        evidence={
            "decision_source": "policy",
            "decision_trigger": "must_have_blocked",
            "decision_factors": [
                {"label": "Security clearance", "status": "unknown", "priority": "must_have"}
            ],
        }
    )

    result = build_decision_explanation(decision, _app([]))
    assert result["summary"] == "Reject recommended because 1 must-have requirement was left unverified."


def test_threshold_explanation_marks_score_as_decisive():
    decision = _decision(
        evidence={
            "decision_source": "policy",
            "rule_path": [
                "point:reject",
                "rule:fired:role_fit_score <= role_fit_max AND no_pending_assessment",
            ],
            "role_fit_score": 42,
            "effective_threshold": 55,
        }
    )

    result = build_decision_explanation(decision, _app([]))
    assert result["summary"] == (
        "Reject recommended because the role-fit score of 42 is at or below the 55 threshold."
    )
    assert result["score_context"]["score_was_decisive"] is True
    assert result["factors_total"] == 0


def test_candidate_summary_normalizes_whitespace_without_truncating():
    summary = (
        "  Strong Lakehouse and dimensional-modelling background with 18 years of experience.\n"
        "The material gap is unproven knowledge-graph delivery.  "
    )
    assert normalize_candidate_summary(summary) == (
        "Strong Lakehouse and dimensional-modelling background with 18 years of experience. "
        "The material gap is unproven knowledge-graph delivery."
    )

    long = "A " + "very " * 100 + "long Claude-authored summary"
    assert normalize_candidate_summary(long) == long


def test_candidate_summary_prefers_frozen_decision_snapshot_over_live_rescore():
    decision = _decision(
        evidence={"candidate_summary": "Frozen decision-time synthesis."},
        reasoning="Legacy decision-time synthesis.",
    )
    app = _app([])
    app.cv_match_details["summary"] = "New live synthesis after a later re-score."

    assert candidate_summary_for(decision, app) == "Frozen decision-time synthesis."


def test_legacy_hard_rule_does_not_invent_missing_factor_details():
    decision = _decision(
        evidence={
            "decision_source": "policy",
            "decision_trigger": "must_have_blocked",
        }
    )

    result = build_decision_explanation(decision, _app([]))
    assert "details were not captured" in result["summary"]
    assert "marked missing" not in result["summary"]


def test_policy_fallback_is_not_relabelled_as_candidate_summary():
    decision = _decision(
        reasoning="Deterministic policy: role-fit 42 vs threshold 55 -> reject",
        evidence={
            "policy_basis": "Deterministic policy: role-fit 42 vs threshold 55 -> reject"
        },
    )

    assert candidate_summary_for(decision, None) is None
