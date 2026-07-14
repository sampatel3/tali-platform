"""Bridges sub-agent outputs + manual actions into a PolicyDecision.

Public entry point: ``evaluate_for_application(db, role, application_id)``.

The orchestrator's MCP tool ``evaluate_policy`` calls into here. So
does the integration test harness — the path is sub-agent-shaped end
to end, no LLM in the verdict step.

Honours the policy's own ``manual_action_window.lookback_hours`` so a
retune can change the window without redeploying.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..decision_policy.abstention import (
    DEFAULT_CONFIDENCE_FLOOR,
    DEFAULT_PER_AGENT_UNCERTAINTY_THRESHOLD,
    DEFAULT_SHARP_DISAGREEMENT_DELTA,
    should_escalate,
)
from ..decision_policy.engine import (
    DecisionInputs,
    PolicyDecision,
    evaluate,
    load_active_policy,
)
from ..decision_policy.schema import PolicyJson
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..services.auto_threshold_service import resolve_role_fit_threshold
from ..services.decision_evidence_service import must_have_blocked
from ..sub_agents.base import SubAgentRequest, SubAgentResult
from ..sub_agents.registry import get_sub_agent
from .decision_translation import role_has_assessment_stage
from .manual_action_reader import read_recent_manual_actions
from .role_intent import fetch_active_intent


logger = logging.getLogger("taali.agent_runtime.policy_evaluator")


# Sub-agent names the orchestrator gathers BEFORE evaluating. Order
# doesn't matter for correctness — each sub-agent is independent.
PRE_EVAL_SUB_AGENT_NAMES = (
    "pre_screen",
    "cv_scoring",
    "assessment_scoring",
    "graph_priors",
)


def _result_to_outputs(result: SubAgentResult) -> dict[str, Any]:
    if not result.ok:
        return {}
    return result.output or {}


def _gather_sub_agent_outputs(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    role_id: int,
    metering_context: dict[str, Any] | None,
    role_intent_extra: dict[str, Any] | None = None,
    skip_cache: bool = False,
) -> dict[str, SubAgentResult]:
    """Run all four sub-agents in sequence.

    Per-agent ``SubAgentRequest.extra`` is populated with two keys when
    available:

      ``role_intent``     — Amendment A1 overlay, read at score time.
      ``exemplars_text``  — formatted few-shot block from the agent's
                            exemplar store. Empty when no past teach
                            attribution exists for this agent (the
                            common pre-pilot case). Cost-guarded so a
                            cold store doesn't trigger a similarity
                            walk on every call.

    Sub-agents that opt in to either overlay read from ``req.extra``;
    sub-agents that don't are unaffected.
    """
    from . import exemplar_store

    out: dict[str, SubAgentResult] = {}
    for name in PRE_EVAL_SUB_AGENT_NAMES:
        try:
            sa = get_sub_agent(name)
        except KeyError:
            logger.warning("sub_agent %s not registered; skipping", name)
            continue
        extra: dict[str, Any] = {}
        if role_intent_extra:
            extra["role_intent"] = role_intent_extra
        # Exemplar overlay — cheap pre-check inside the helper avoids
        # cosine work on a cold store. The empty string is the no-op.
        try:
            exemplars_text = exemplar_store.render_exemplars_for_prompt(
                db,
                agent_name=name,
                organization_id=organization_id,
                role_id=role_id,
                # Query features come from the candidate's prior signals
                # if available; we default to an empty dict here because
                # the policy_evaluator runs sub-agents in parallel and
                # doesn't have per-candidate features pre-computed.
                # When sub-agents pick this up, they should re-call
                # retrieve_top_k with their own feature vector for a
                # tighter match. The render_exemplars_for_prompt path
                # is still useful for the "recent broadly-similar
                # teach events" overlay.
                query_features={},
                k=2,
            )
            if exemplars_text:
                extra["exemplars_text"] = exemplars_text
        except Exception:
            pass
        req = SubAgentRequest(
            organization_id=organization_id,
            application_id=application_id,
            role_id=role_id,
            skip_cache=skip_cache,
            metering_context=metering_context,
            extra=extra,
        )
        try:
            out[name] = sa.run(req, db=db)  # type: ignore[call-arg]
        except TypeError:
            # Sub-agents that don't accept the optional db kwarg.
            out[name] = sa.run(req)
    return out


def _flags_from_application(
    app: CandidateApplication, scores: dict[str, float], role: Role
) -> dict[str, bool]:
    """Build the boolean flags the engine's rules reference.

    ``has_pending_assessment`` — true if any non-voided assessment row
    is present without a final score. ``no_pending_assessment`` is the
    inverse, exposed positively because rule conditions read better.
    ``assessment_completed`` mirrors the assessment_scoring sub-agent's
    output for engine convenience. ``has_assessment_task`` — true when
    the role has at least one assessment task linked; when False a
    ``send_assessment`` verdict is translated to ``advance`` (there's
    nothing to send, so a strong candidate goes straight to interview).
    """
    has_pending = False
    try:
        from ..models.assessment import Assessment

        # Avoid a query when the application has no assessments
        # relationship cached; fall back to a count query.
        rows = list(getattr(app, "assessments", []) or [])
        if not rows and getattr(app, "id", None) is not None:
            # Lazy: fall back to None when ORM session not attached.
            pass
        for row in rows:
            score = getattr(row, "assessment_score", None)
            voided = bool(getattr(row, "is_voided", False))
            if not voided and score is None:
                has_pending = True
                break
    except Exception:  # pragma: no cover — defensive
        has_pending = False

    return {
        "has_pending_assessment": has_pending,
        "no_pending_assessment": not has_pending,
        "assessment_completed": (
            scores.get("assessment_score") is not None
        ),
        "must_have_blocked": must_have_blocked(app),
        "has_assessment_task": role_has_assessment_stage(role),
    }


def _scores_from_outputs(
    sub_outputs: dict[str, SubAgentResult]
) -> dict[str, float]:
    scores: dict[str, float] = {}
    pre = _result_to_outputs(sub_outputs.get("pre_screen") or SubAgentResult(sub_agent="pre_screen", ok=False))
    cv = _result_to_outputs(sub_outputs.get("cv_scoring") or SubAgentResult(sub_agent="cv_scoring", ok=False))
    ass = _result_to_outputs(sub_outputs.get("assessment_scoring") or SubAgentResult(sub_agent="assessment_scoring", ok=False))
    if pre.get("score") is not None:
        scores["pre_screen_score"] = float(pre["score"])
    if cv.get("role_fit_score") is not None:
        scores["role_fit_score"] = float(cv["role_fit_score"])
    if cv.get("calibrated_p_advance") is not None:
        scores["calibrated_p_advance"] = float(cv["calibrated_p_advance"])
    if ass.get("taali_score") is not None:
        scores["taali_score"] = float(ass["taali_score"])
    if ass.get("assessment_score") is not None:
        scores["assessment_score"] = float(ass["assessment_score"])
    return scores


def evaluate_for_application(
    db: Session,
    *,
    role: Role,
    application_id: int,
    metering_context: dict[str, Any] | None = None,
    parsed_intent: dict[str, Any] | None = None,
    skip_cache: bool = False,
) -> tuple[PolicyDecision, dict[str, SubAgentResult]]:
    """End-to-end: gather sub-agent outputs, build inputs, evaluate.

    Returns ``(verdict, sub_agent_outputs)``. The orchestrator stamps
    both onto ``AgentDecision.evidence`` for the audit trail.
    """
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == role.organization_id,
        )
        .one_or_none()
    )
    if app is None:
        return (
            PolicyDecision(
                decision_type="no_action",
                reasoning=f"application {application_id} not found",
                rule_path=["application_missing"],
            ),
            {},
        )

    # Amendment A1: authored intent is fetched once per evaluation and
    # passed through SubAgentRequest.extra so sub-agents that opt-in
    # (today: none; planned: cv_scoring, pre_screen) can read it
    # without re-fetching. Returns None when no intent has been
    # authored for the role — pre-A1 behaviour is preserved.
    role_intent_extra: dict[str, Any] | None = None
    try:
        intent_record = fetch_active_intent(db, role_id=int(role.id))
        if intent_record is not None:
            role_intent_extra = {
                "version": int(intent_record.version),
                "structured": intent_record.structured.model_dump(),
                "free_text": intent_record.free_text,
            }
    except Exception:  # pragma: no cover — never break the cycle
        role_intent_extra = None

    outputs = _gather_sub_agent_outputs(
        db,
        organization_id=int(role.organization_id),
        application_id=int(application_id),
        role_id=int(role.id),
        metering_context=metering_context,
        role_intent_extra=role_intent_extra,
        skip_cache=skip_cache,
    )
    scores = _scores_from_outputs(outputs)
    flags = _flags_from_application(app, scores, role)

    # Resolve manual-action lookback window from the active policy
    # itself so a retune that widens / narrows it takes effect without
    # a code change.
    try:
        policy_row = load_active_policy(
            db,
            organization_id=int(role.organization_id),
            role_id=int(role.id),
        )
        lookback = int(
            PolicyJson.model_validate(policy_row.policy_json or {})
            .manual_action_window.lookback_hours
        )
    except Exception as exc:
        logger.warning("policy load failed; defaulting lookback=72h: %s", exc)
        lookback = 72

    actions = read_recent_manual_actions(
        db,
        application_id=int(application_id),
        lookback_hours=lookback,
    )

    graph_output = (
        outputs.get("graph_priors").output
        if outputs.get("graph_priors") and outputs["graph_priors"].ok
        else {}
    )
    graph_priors = {
        "p_advance": graph_output.get("p_advance"),
        "p_hired": graph_output.get("p_hired"),
        "neighbour_count": graph_output.get("neighbour_count", 0),
        "confidence": float(graph_output.get("confidence", 0.0) or 0.0),
    }

    resolved_intent = parsed_intent or (
        role_intent_extra.get("structured", {}) if role_intent_extra else {}
    )
    inputs = DecisionInputs(
        application_id=int(application_id),
        role_id=int(role.id),
        organization_id=int(role.organization_id),
        scores=scores,
        graph_priors=graph_priors,
        intent=resolved_intent,
        flags=flags,
        manual_actions=actions,
        effective_role_fit_threshold=resolve_role_fit_threshold(db, role=role),
    )

    verdict = evaluate(inputs, db=db)

    # Phase 4 abstention overlay (zero LLM cost — pure-Python triggers
    # over the sub-agent outputs the rule engine just consumed). When
    # the verdict is already ``no_action`` / ``skip`` we don't escalate
    # — the engine has already decided not to act.
    verdict = _maybe_escalate(verdict, outputs)
    return verdict, outputs


def _maybe_escalate(
    verdict: PolicyDecision,
    outputs: dict[str, SubAgentResult],
) -> PolicyDecision:
    """Overlay ``escalate_low_confidence`` when sub-agents disagree or
    are individually uncertain. Preserves the original rule_path so the
    audit shows both the original verdict and the abstention reason.

    Skips when:
    - The engine already chose ``skip`` / ``no_action`` / ``auto_reject``.
      Manual-action skip and hard-rule auto-rejects are deliberate and
      not abstention candidates.
    - Fewer than 3 sub-agents produced a usable score (disagreement
      can't be measured meaningfully).
    """
    if verdict.decision_type in ("skip", "no_action", "auto_reject"):
        return verdict
    if verdict.skipped_due_to_manual:
        return verdict
    per_agent_scores: list[float] = []
    per_agent_uncertainties: list[float] = []
    per_agent_names: list[str] = []
    for name, result in outputs.items():
        if not result.ok:
            continue
        per_agent_names.append(name)
        # Disagreement is measured over each agent's *predicted score*, not
        # its confidence metadata. ``SubAgentResult.confidence`` defaults to
        # 0.0 (never None), so reading it first collapsed the spread to zeros;
        # use the agent's output["score"] and only fall back to confidence
        # when no score was emitted. Normalise [0, 100] → [0, 1] so the
        # spread is meaningful regardless of the original signal scale.
        output = result.output or {}
        raw_score = output.get("score")
        if raw_score is None:
            raw_score = result.confidence
        raw_score = float(raw_score or 0.0)
        if raw_score > 1.0:
            raw_score = raw_score / 100.0
        per_agent_scores.append(float(raw_score))
        per_agent_uncertainties.append(float(result.uncertainty or 0.0))
    if len([s for s in per_agent_scores if s is not None]) < 3:
        return verdict
    abstain = should_escalate(
        per_agent_scores=per_agent_scores,
        per_agent_uncertainties=per_agent_uncertainties,
        calibrated_confidence=float(verdict.confidence) if verdict.confidence else None,
        per_agent_uncertainty_threshold=DEFAULT_PER_AGENT_UNCERTAINTY_THRESHOLD,
        sharp_disagreement_delta=DEFAULT_SHARP_DISAGREEMENT_DELTA,
        confidence_floor=DEFAULT_CONFIDENCE_FLOOR,
        per_agent_names=per_agent_names,
    )
    if not abstain.escalate:
        return verdict
    rule_path = list(verdict.rule_path) + [
        f"abstention_overlay:{abstain.triggered_by}:{abstain.reason}"
    ]
    return PolicyDecision(
        decision_type="escalate_low_confidence",
        confidence=verdict.confidence,
        reasoning=(
            f"Escalated for low confidence — {abstain.reason}. "
            f"Original verdict: {verdict.decision_type}. "
            f"Original reasoning: {verdict.reasoning}"
        ),
        rule_path=rule_path,
        policy_revision_id=verdict.policy_revision_id,
        decision_point=verdict.decision_point,
        intent_overrode=verdict.intent_overrode,
        skipped_due_to_manual=False,
    )


__all__ = [
    "PRE_EVAL_SUB_AGENT_NAMES",
    "evaluate_for_application",
]
