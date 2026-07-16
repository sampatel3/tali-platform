"""Recruiter-facing presentation for a persisted hiring decision.

``AgentDecision.reasoning`` predates deterministic policy decisions and is a
mixture of two different concepts: sometimes it is an agent's decision reason,
and sometimes it is the candidate's holistic CV summary.  The review surfaces
need both, clearly labelled.  This module derives a candidate summary and an
auditable decision explanation from the immutable decision evidence plus the
application snapshot, including legacy rows that were created before these
presentation fields existed.
"""

from __future__ import annotations

import re
from typing import Any

from .decision_evidence_service import blocked_must_have_requirements
from .decision_reasoning_text import humanize_reasoning

# The serializer already humanizes the raw ``reasoning`` field with this shared
# cleanup (scorer keys, key=value dumps, parenthesized internal IDs). The
# explanation summary is built from the same stored prose, so it must run the
# SAME humanizer — a second narrower copy here would let the two fields drift.

_SPACE = re.compile(r"\s+")


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def _display_number(value: float | None) -> str | None:
    if value is None:
        return None
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


def normalize_candidate_summary(value: Any) -> str | None:
    """Return Claude's candidate summary without changing its meaning.

    Whitespace is normalised for JSON/HTML presentation, but sentence count and
    content are preserved. Concision belongs to the scorer's generation
    contract; this layer must never silently rewrite or truncate its output.
    """
    text = _clean(value)
    return text or None


def candidate_summary_for(decision: Any, application: Any | None) -> str | None:
    evidence = getattr(decision, "evidence", None)
    evidence = evidence if isinstance(evidence, dict) else {}
    # New decisions freeze the candidate synthesis beside the policy snapshot.
    # Prefer it over the mutable application so a later re-score cannot rewrite
    # the explanation of an already-reviewed decision.
    frozen = normalize_candidate_summary(evidence.get("candidate_summary"))
    if frozen:
        return frozen

    # Legacy bulk decisions stored the candidate synthesis in ``reasoning``.
    # It is still a safer historical snapshot than today's CV-match payload.
    if str(getattr(decision, "model_version", "")) == "bulk-deterministic":
        frozen = normalize_candidate_summary(getattr(decision, "reasoning", None))
        policy_basis = normalize_candidate_summary(evidence.get("policy_basis"))
        is_policy_fallback = bool(
            frozen
            and (
                frozen.lower().startswith("deterministic policy:")
                or (policy_basis and frozen == policy_basis)
            )
        )
        if frozen and not is_policy_fallback:
            return frozen

    if str(getattr(decision, "status", "")) in {
        "approved",
        "overridden",
        "discarded",
        "expired",
    }:
        return None

    details = getattr(application, "cv_match_details", None) if application is not None else None
    if isinstance(details, dict):
        summary = normalize_candidate_summary(details.get("summary"))
        if summary:
            return summary
        bullets = details.get("score_rationale_bullets")
        if isinstance(bullets, list):
            for bullet in bullets:
                summary = normalize_candidate_summary(bullet)
                if summary:
                    return summary
    # Agent reasoning is a decision cause, not a candidate synthesis. Returning
    # it here would relabel policy/agent prose as CANDIDATE SUMMARY.
    return None


def _fired_rule(evidence: dict[str, Any]) -> str | None:
    snapshotted = _clean(evidence.get("decision_trigger"))
    if snapshotted:
        return snapshotted
    path = evidence.get("rule_path")
    if not isinstance(path, list):
        return None
    for step in reversed(path):
        prefix = "rule:fired:"
        if isinstance(step, str) and step.startswith(prefix):
            return step[len(prefix) :].strip() or None
    return None


def _decision_source(decision: Any, evidence: dict[str, Any]) -> str:
    explicit = _clean(evidence.get("decision_source")).lower()
    if explicit in {"policy", "agent"}:
        return explicit
    producer = _clean(evidence.get("source")).lower()
    if (
        str(getattr(decision, "model_version", "")) == "bulk-deterministic"
        or str(getattr(decision, "model_version", "")) in {"pre_screen_v1", "knockout_v1"}
        or producer
        in {
            "bulk_decision",
            "score_time_decision",
            "post_handover_second_opinion",
            "pre_screen_threshold",
            "knockout_screening",
        }
    ):
        return "policy"
    return "agent"


def _score_context(evidence: dict[str, Any]) -> tuple[float | None, float | None]:
    score = _number(
        evidence.get("role_fit_score")
        if evidence.get("role_fit_score") is not None
        else evidence.get("taali_score")
        if evidence.get("taali_score") is not None
        else evidence.get("pre_screen_score_100")
        if evidence.get("pre_screen_score_100") is not None
        else evidence.get("pre_screen_score")
    )
    threshold = _number(
        evidence.get("effective_threshold")
        if evidence.get("effective_threshold") is not None
        else evidence.get("threshold_100")
    )
    return score, threshold


def build_decision_explanation(decision: Any, application: Any | None) -> dict[str, Any]:
    """Build a short causal explanation from the rule that actually fired."""
    evidence = getattr(decision, "evidence", None)
    evidence = evidence if isinstance(evidence, dict) else {}
    decision_type = str(getattr(decision, "decision_type", "") or "")
    source = _decision_source(decision, evidence)
    fired_rule = _fired_rule(evidence)
    score, threshold = _score_context(evidence)
    score_text = _display_number(score)
    threshold_text = _display_number(threshold)
    factors: list[dict[str, Any]] = []
    # True factor count before the 5-row display cap — chips derived from
    # ``factors`` must still count all blockers ("7 must-haves missing").
    factors_total = 0
    context: str | None = None

    if source == "policy" and fired_rule == "must_have_blocked":
        frozen_rows = evidence.get("decision_factors")
        rows = (
            [row for row in frozen_rows if isinstance(row, dict)]
            if isinstance(frozen_rows, list)
            else blocked_must_have_requirements(application)
            if application is not None
            else []
        )
        factors = rows[:5]
        count = len(rows)
        factors_total = count
        statuses = {_clean(row.get("status")).lower() for row in rows}
        if statuses and statuses <= {"missing", "not_met", "not met", "failed", "fail", "no"}:
            state = "marked missing"
        elif statuses and statuses <= {"unknown"}:
            state = "left unverified"
        else:
            state = "marked missing or unverified"
        if count:
            summary = (
                f"Reject recommended because {count} must-have "
                f"requirement{'s were' if count != 1 else ' was'} {state}."
            )
        else:
            captured_reason = normalize_candidate_summary(evidence.get("policy_reasoning"))
            summary = captured_reason or (
                "Reject recommended because the must-have policy rule fired; "
                "the decisive requirement details were not captured on this legacy decision."
            )
        if score is not None and threshold is not None and score >= threshold:
            context = (
                f"The {score_text} role-fit score cleared the {threshold_text} threshold; "
                "the hard must-have rule took priority."
            )
    elif source == "policy" and fired_rule == "pre_screen_auto_reject_eligible":
        if score_text is not None and threshold_text is not None:
            summary = (
                f"Reject recommended at pre-screen because the score of {score_text} "
                f"is below the {threshold_text} threshold."
            )
        else:
            summary = "Reject recommended because the candidate did not clear pre-screen."
    elif source == "policy" and fired_rule == "knockout_screening":
        summary = normalize_candidate_summary(evidence.get("policy_reasoning"))
        if not summary:
            summary = "Reject recommended because a required application answer failed a knockout rule."
    elif source == "policy" and fired_rule and "role_fit_score <= role_fit_max" in fired_rule:
        if score_text is not None and threshold_text is not None:
            summary = (
                f"Reject recommended because the role-fit score of {score_text} "
                f"is at or below the {threshold_text} threshold."
            )
        else:
            summary = "Reject recommended because role fit is below the configured threshold."
    elif source == "policy" and fired_rule and "role_fit_score >= role_fit_min" in fired_rule:
        verb = "Send an assessment" if decision_type == "send_assessment" else "Advance"
        if score_text is not None and threshold_text is not None:
            summary = (
                f"{verb} recommended because the role-fit score of {score_text} "
                f"clears the {threshold_text} threshold."
            )
        else:
            summary = f"{verb} recommended because the candidate cleared the role-fit policy."
        if decision_type == "advance_to_interview" and evidence.get("has_assessment_task") is False:
            context = "This role skips the assessment stage, so a positive send verdict becomes an advance."
    elif source == "policy" and fired_rule and "taali_score >= taali_score_min" in fired_rule:
        summary = "Advance recommended because the completed assessment clears the interview policy."
    elif source == "policy":
        policy_reasoning = normalize_candidate_summary(evidence.get("policy_reasoning"))
        if policy_reasoning:
            summary = policy_reasoning
        elif decision_type in {"reject", "skip_assessment_reject"}:
            summary = "Reject recommended by the configured decision policy."
        elif decision_type == "send_assessment":
            summary = "Send an assessment recommended by the configured decision policy."
        else:
            summary = "Advance recommended by the configured decision policy."
    else:
        summary = normalize_candidate_summary(
            humanize_reasoning(str(getattr(decision, "reasoning", None) or ""))
        )
        if not summary:
            summary = "The agent queued this decision for recruiter review."

    return {
        "source": source,
        "label": "Policy" if source == "policy" else "Agent",
        "summary": summary,
        "context": context,
        "factors": factors,
        "factors_total": factors_total,
        "rule": fired_rule,
        "score_context": {
            "role_fit_score": score,
            "threshold": threshold,
            "threshold_passed": (
                score >= threshold if score is not None and threshold is not None else None
            ),
            "score_was_decisive": bool(
                fired_rule
                and ("role_fit_score <= role_fit_max" in fired_rule or "role_fit_score >= role_fit_min" in fired_rule)
            ),
        },
        "policy_revision_id": evidence.get("policy_revision_id"),
    }


__all__ = [
    "build_decision_explanation",
    "candidate_summary_for",
    "normalize_candidate_summary",
]
