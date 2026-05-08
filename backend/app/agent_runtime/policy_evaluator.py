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

from ..decision_policy.engine import (
    DecisionInputs,
    PolicyDecision,
    evaluate,
    load_active_policy,
)
from ..decision_policy.schema import PolicyJson
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..sub_agents.base import SubAgentRequest, SubAgentResult
from ..sub_agents.registry import get_sub_agent
from .manual_action_reader import read_recent_manual_actions


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
    skip_cache: bool = False,
) -> dict[str, SubAgentResult]:
    out: dict[str, SubAgentResult] = {}
    for name in PRE_EVAL_SUB_AGENT_NAMES:
        try:
            sa = get_sub_agent(name)
        except KeyError:
            logger.warning("sub_agent %s not registered; skipping", name)
            continue
        req = SubAgentRequest(
            organization_id=organization_id,
            application_id=application_id,
            role_id=role_id,
            skip_cache=skip_cache,
            metering_context=metering_context,
        )
        try:
            out[name] = sa.run(req, db=db)  # type: ignore[call-arg]
        except TypeError:
            # Sub-agents that don't accept the optional db kwarg.
            out[name] = sa.run(req)
    return out


def _flags_from_application(
    app: CandidateApplication, scores: dict[str, float]
) -> dict[str, bool]:
    """Build the boolean flags the engine's rules reference.

    ``has_pending_assessment`` — true if any non-voided assessment row
    is present without a final score. ``no_pending_assessment`` is the
    inverse, exposed positively because rule conditions read better.
    ``assessment_completed`` mirrors the assessment_scoring sub-agent's
    output for engine convenience.
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
        # ``must_have_blocked`` defaults False; the orchestrator can
        # set it True from intent_parser ``disqualifying_signals`` once
        # the matching pass is implemented (Phase 5+).
        "must_have_blocked": False,
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

    outputs = _gather_sub_agent_outputs(
        db,
        organization_id=int(role.organization_id),
        application_id=int(application_id),
        role_id=int(role.id),
        metering_context=metering_context,
        skip_cache=skip_cache,
    )
    scores = _scores_from_outputs(outputs)
    flags = _flags_from_application(app, scores)

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

    inputs = DecisionInputs(
        application_id=int(application_id),
        role_id=int(role.id),
        organization_id=int(role.organization_id),
        scores=scores,
        graph_priors=graph_priors,
        intent=parsed_intent or {},
        flags=flags,
        manual_actions=actions,
    )

    verdict = evaluate(inputs, db=db)
    return verdict, outputs


__all__ = [
    "PRE_EVAL_SUB_AGENT_NAMES",
    "evaluate_for_application",
]
