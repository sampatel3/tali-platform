"""Shared deterministic-verdict core for the bulk decision pass.

Pure-rule helpers — no sub-agents, no Anthropic calls — that turn an
application's ALREADY-stored scores into ``DecisionInputs`` and (re)compute the
persisted verdict against the role's current threshold. Every entry point in
this package (cohort pass, score-time, post-handover, auto-correct) funnels
through ``_inputs_for`` so a candidate is evaluated identically regardless of
which path reaches it.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ...agent_runtime.decision_translation import (
    QUEUEABLE_VERDICTS,
    resolve_persisted_decision_type,
    role_has_assessment_stage,
)
from ...decision_policy.engine import DecisionInputs, evaluate
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ..auto_threshold_service import resolve_role_fit_threshold
from ..decision_evidence_service import blocked_must_have_requirements, must_have_blocked
from ..decision_presentation_service import normalize_candidate_summary

logger = logging.getLogger("taali.bulk_decision")


def _role_fit_score(app: CandidateApplication) -> float | None:
    val = getattr(app, "role_fit_score_cache_100", None)
    if val is None:
        val = getattr(app, "cv_match_score", None)
    return float(val) if val is not None else None


def _recruiter_reasoning(app: CandidateApplication) -> str | None:
    """Recruiter-facing decision narrative, sourced from the CV-match
    ``summary``. Single source of truth shared with the LLM-agent path (via
    ``queue_decision``) so a card reads the same regardless of producer."""
    from ..decision_reasoning import recruiter_decision_reasoning
    return recruiter_decision_reasoning(app)


def _no_assessment_note(role, has_task: bool) -> str:
    """Policy-basis suffix explaining why send→advance fired: the role either
    has no assessment task, or the recruiter toggled auto_skip_assessment."""
    if has_task:
        return ""
    if bool(getattr(role, "auto_skip_assessment", False)):
        return "; assessments skipped for this role (auto-skip), advancing directly"
    return "; role has no assessment task, advancing directly"


def _assessment_inputs(app: CandidateApplication) -> tuple[dict[str, float], dict[str, bool]]:
    """Return the persisted assessment result + lifecycle flags for ``app``.

    The score-time and cohort paths deliberately avoid sub-agent/LLM work, but
    that does not mean they can ignore an assessment that already completed.
    Completion refreshes the application score cache before waking the role
    agent; use those canonical cached values first and fall back to the active
    assessment row for legacy records whose cache was never refreshed.

    A terminal timeout is still a completed attempt.  Even when scoring failed
    and no numeric result exists, ``assessment_completed`` must suppress a
    second invite; the later decision points (or the agent's HITL fallback) own
    what happens next.
    """
    assessments = [
        row
        for row in (getattr(app, "assessments", None) or [])
        if not bool(getattr(row, "is_voided", False))
    ]

    def _status(row) -> str:
        raw = getattr(getattr(row, "status", None), "value", getattr(row, "status", None))
        return str(raw or "").strip().lower()

    terminal_statuses = {"completed", "completed_due_to_timeout"}
    pending_statuses = {"pending", "in_progress"}
    completed_rows = [
        row
        for row in assessments
        if _status(row) in terminal_statuses
        or bool(getattr(row, "completed_due_to_timeout", False))
    ]
    # The DB invariant permits only one non-voided assessment per candidate and
    # role.  Sorting by id is a defensive legacy fallback without mixing naive
    # and timezone-aware datetimes from old rows.
    completed_rows.sort(
        key=lambda row: int(getattr(row, "id", 0) or 0), reverse=True
    )
    completed = bool(
        completed_rows
        or getattr(app, "assessment_score_cache_100", None) is not None
    )
    pending = any(_status(row) in pending_statuses for row in assessments)

    def _numeric(value) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    result_scores: dict[str, float] = {}
    assessment_score = _numeric(
        getattr(app, "assessment_score_cache_100", None)
    )
    taali_score = _numeric(getattr(app, "taali_score_cache_100", None))
    latest = completed_rows[0] if completed_rows else None
    grading_incomplete = bool(
        latest
        and (
            getattr(latest, "scoring_partial", False)
            or getattr(latest, "scoring_failed", False)
        )
    )
    if latest is not None:
        if assessment_score is None:
            assessment_score = _numeric(getattr(latest, "assessment_score", None))
        if assessment_score is None:
            assessment_score = _numeric(getattr(latest, "final_score", None))
        if assessment_score is None:
            legacy_score = _numeric(getattr(latest, "score", None))
            assessment_score = legacy_score * 10.0 if legacy_score is not None else None
        if taali_score is None:
            taali_score = _numeric(getattr(latest, "taali_score", None))
        # A legacy completed row may only carry the assessment result.  That is
        # still the best available post-assessment headline and mirrors the
        # role-support cache fallback when no separate role-fit blend exists.
        if taali_score is None:
            taali_score = assessment_score

    if grading_incomplete:
        # Never let a stale cache or heuristic fallback become a verdict while
        # the authoritative rubric is incomplete.
        assessment_score = None
        taali_score = None
    if assessment_score is not None:
        result_scores["assessment_score"] = max(0.0, min(100.0, assessment_score))
    if taali_score is not None:
        result_scores["taali_score"] = max(0.0, min(100.0, taali_score))

    return result_scores, {
        "has_pending_assessment": pending,
        "no_pending_assessment": not pending,
        "assessment_completed": completed,
        "assessment_grading_incomplete": grading_incomplete,
    }


def _fired_rule(verdict) -> str | None:
    for step in reversed(list(getattr(verdict, "rule_path", None) or [])):
        if isinstance(step, str) and step.startswith("rule:fired:"):
            return step[len("rule:fired:") :]
    return None


def _policy_basis(*, verdict, decision_type: str, role_fit: float, pre_screen: float, eff, role, has_task: bool) -> str:
    """Audit text led by the rule that caused the verdict, not just its score.

    A hard must-have rule can reject an above-threshold candidate.  The old
    unconditional ``score -> decision`` string made that look as if the score
    caused the reject (for example, ``72 vs 55 -> reject``).  Preserve score
    context while making the fired rule the causal statement.
    """
    threshold = eff if eff is not None else "default"
    fired = _fired_rule(verdict)
    score_context = f"role-fit {role_fit:.0f} vs threshold {threshold} (pre-screen {pre_screen:.0f})"
    if fired in {"must_have_blocked", "pre_screen_auto_reject_eligible"}:
        reason = str(getattr(verdict, "reasoning", "") or "Policy hard rule fired.").strip()
        return f"{reason} Score context: {score_context}; hard rule took priority."
    return (
        f"{score_context} -> {decision_type}"
        + _no_assessment_note(role, has_task)
    )


def _policy_evidence(
    app,
    *,
    verdict,
    decision_type: str,
    role_fit: float,
    pre_screen: float,
    eff,
    role,
    has_task: bool,
    assessment_completed: bool,
    source: str,
) -> dict:
    """Freeze the causal policy snapshot on a new deterministic decision."""
    fired = _fired_rule(verdict)
    details = getattr(app, "cv_match_details", None)
    candidate_summary = normalize_candidate_summary(
        details.get("summary") if isinstance(details, dict) else None
    )
    evidence = {
        "role_fit_score": role_fit,
        "pre_screen_score": pre_screen,
        "effective_threshold": eff,
        "has_assessment_task": has_task,
        "rule_path": list(getattr(verdict, "rule_path", None) or []),
        "engine_verdict": getattr(verdict, "decision_type", None),
        "policy_reasoning": getattr(verdict, "reasoning", None),
        "policy_revision_id": getattr(verdict, "policy_revision_id", None),
        "decision_point": getattr(verdict, "decision_point", None),
        "policy_basis": _policy_basis(
            verdict=verdict,
            decision_type=decision_type,
            role_fit=role_fit,
            pre_screen=pre_screen,
            eff=eff,
            role=role,
            has_task=has_task,
        ),
        "decision_trigger": fired,
        "decision_source": "policy",
        "decision_stage": (
            "assessment" if assessment_completed else "full_scoring"
        ),
        "source": source,
    }
    if candidate_summary:
        evidence["candidate_summary"] = candidate_summary
    if fired == "must_have_blocked":
        evidence["decision_factors"] = blocked_must_have_requirements(app)
    return evidence


def _inputs_for(app, *, role_id, org_id, eff, has_task):
    """Build the deterministic DecisionInputs from an application's stored
    scores — no sub-agents, no LLM. Shared by the decide loop and the
    threshold-shift reconcile so both evaluate identically."""
    role_fit = _role_fit_score(app)
    if role_fit is None:
        return None
    # Send-gate input. The bulk pass only runs on scored candidates, where
    # pre_screen_score_100 == the full cv_match score — which is the RIGHT
    # input here: the full score governs a fully-scored candidate, so we must
    # NOT re-impose the cheap pre-screen gate on them. (Prod data shows the
    # genuine cheap pre-screen — kept in genuine_pre_screen_score_100 for
    # audit/labels — runs higher AND can straddle 50 vs the full score; using
    # it here would wrongly block advances of strongly-scored candidates.)
    # A low value never causes a false send: the send rule gates on
    # pre_screen_min (50), which apply_effective_threshold leaves untouched.
    pre_screen = (
        float(app.pre_screen_score_100)
        if app.pre_screen_score_100 is not None
        else role_fit
    )
    assessment_scores, assessment_flags = _assessment_inputs(app)
    if assessment_flags.get("assessment_grading_incomplete"):
        return None
    return DecisionInputs(
        application_id=int(app.id),
        role_id=int(role_id),
        organization_id=int(org_id),
        scores={
            "role_fit_score": role_fit,
            "pre_screen_score": pre_screen,
            **assessment_scores,
        },
        flags={
            **assessment_flags,
            "must_have_blocked": must_have_blocked(app),
            "has_assessment_task": has_task,
        },
        effective_role_fit_threshold=eff,
    )


def recompute_persisted_verdict(
    db: Session, *, role: Role, app: CandidateApplication
) -> str | None:
    """The deterministic persisted decision_type for ``app`` against the role's
    CURRENT scores + threshold — the same pure-rule path ``decide_role_cohort``
    and the threshold reconcile use, no LLM. Returns ``None`` when the rule
    yields a non-queueable verdict (escalate / skip / no_action), the candidate
    isn't scorable, or on any error — so callers treat "can't recompute" as
    "don't claim the verdict still holds" (fail safe, keep the banner)."""
    try:
        eff = resolve_role_fit_threshold(db, role=role)
        has_task = role_has_assessment_stage(role)
        inputs = _inputs_for(
            app,
            role_id=int(role.id),
            org_id=int(role.organization_id),
            eff=eff,
            has_task=has_task,
        )
        if inputs is None:
            return None
        verdict = evaluate(inputs, db=db)
        if verdict.decision_type not in QUEUEABLE_VERDICTS:
            return None
        return resolve_persisted_decision_type(
            verdict.decision_type, has_assessment_task=has_task
        )
    except Exception:  # noqa: BLE001 — recompute is best-effort
        logger.exception(
            "recompute_persisted_verdict failed app=%s", getattr(app, "id", "?")
        )
        return None
