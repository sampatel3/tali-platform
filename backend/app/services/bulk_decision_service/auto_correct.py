"""Post-(re)score in-place correction of a stale PENDING agent decision.

When a re-score flips the deterministic verdict, the app's single pending card
can be left showing the wrong recommendation (e.g. a send the re-score dropped
below bar). ``auto_correct_stale_verdict`` corrects the SAFE subset in place —
reject<->send_assessment only, never advance / skip_assessment_reject, never a
decision resting on an independent gate (location/salary/visa/fraud or a
structured must-have gap). Everything excluded keeps its staleness banner for
the recruiter. The row stays PENDING — this corrects the recommendation, it
does not resolve/execute it.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from ...agent_runtime.decision_translation import (
    resolve_persisted_decision_type,
    role_has_assessment_stage,
)
from ...decision_policy.engine import evaluate
from ...models.agent_decision import AgentDecision
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ..auto_threshold_service import resolve_role_fit_threshold
from ..decision_presentation_service import normalize_candidate_summary
from ._shared import _inputs_for, _policy_evidence, _recruiter_reasoning, _role_fit_score

logger = logging.getLogger("taali.bulk_decision")


# Hard external constraints the deterministic score band can't see — a verdict
# resting on one must NOT be auto-flipped by a re-score (the constraint survives
# the score change). A structured must-have GAP counts too: a reject citing a
# specific unmet must-have is the recruiter's call, not the band's.
_VERDICT_GATE_WORDS = re.compile(
    r"location|relocat|visa|salary|compensation|onsite|on-site|notice period|"
    r"work permit|sponsor|fraud|integrity|plagiar",
    re.I,
)
# Only these two verdicts auto-correct, and only between each other. advance /
# skip_assessment_reject / anything else is always left for the recruiter.
_AUTO_CORRECTABLE = {"reject", "send_assessment"}
_STALE_CAUSAL_KEYS = {
    "candidate_summary",
    "decision_factors",
    "decision_point",
    "decision_source",
    "decision_trigger",
    "engine_verdict",
    "has_assessment_task",
    "policy_basis",
    "policy_confidence",
    "policy_reasoning",
    "policy_revision_id",
    "rule_path",
}


def _verdict_has_independent_gate(decision: AgentDecision) -> bool:
    """True when the decision rests on a reason the pure-rule score band can't
    see (location/salary/visa/fraud, or a structured must-have gap) — leave it
    for the recruiter rather than auto-flipping on a score change."""
    ev = decision.evidence if isinstance(decision.evidence, dict) else {}
    if (
        ev.get("must_have_gaps")
        or ev.get("must_have_blocked")
        or ev.get("decision_trigger") == "must_have_blocked"
        or ev.get("decision_factors")
    ):
        return True
    blob = f"{decision.reasoning or ''} {json.dumps(ev, default=str)}"
    return bool(_VERDICT_GATE_WORDS.search(blob))


def auto_correct_stale_verdict(
    db: Session, *, app: CandidateApplication, role: Role
) -> str | None:
    """After a (re)score, correct the app's single PENDING agent decision in
    place when the deterministic verdict has FLIPPED and it's safe to — so a
    stale send/reject card (e.g. a send the re-score dropped below bar, or a
    reject it lifted above bar) doesn't strand in the queue showing the wrong
    recommendation.

    SAFE SUBSET ONLY (Sam's steer): both directions reject<->send_assessment,
    but NEVER advance_to_interview / skip_assessment_reject, and NEVER a decision
    resting on an independent gate (location/salary/visa/fraud or a structured
    must-have gap). Everything excluded keeps its staleness banner so the
    recruiter still sees it (the same judgement calls I leave by hand). The row
    stays PENDING — this corrects the recommendation, it does not resolve/execute
    it. Best-effort: returns the new decision_type on a correction, else None.
    Does NOT commit — the caller does.
    """
    try:
        # Resolve recompute through the package namespace so a test that
        # monkeypatches ``bulk_decision_service.recompute_persisted_verdict``
        # (the same knob the decision-staleness suite patches) is honoured here.
        from . import recompute_persisted_verdict

        decision = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.role_id == int(role.id),
                AgentDecision.application_id == int(app.id),
                AgentDecision.status == "pending",
            )
            .order_by(AgentDecision.id.desc())
            .first()
        )
        if decision is None or decision.decision_type not in _AUTO_CORRECTABLE:
            return None
        new_type = recompute_persisted_verdict(db, role=role, app=app)
        if (
            new_type is None
            or new_type == decision.decision_type
            or new_type not in _AUTO_CORRECTABLE
        ):
            return None  # no flip, non-queueable, or flips to advance — leave it
        if _verdict_has_independent_gate(decision):
            return None  # owned by the recruiter, not the score band

        from ...actions.queue_decision import (
            _capture_input_fingerprint,
            _compute_dedup_key,
        )

        eff = resolve_role_fit_threshold(db, role=role)
        prior_type = decision.decision_type
        decision.decision_type = new_type
        decision.recommendation = new_type
        decision.reasoning = _recruiter_reasoning(app) or decision.reasoning
        # Becomes a deterministic row so future threshold reconciles manage it;
        # provenance of the manual->deterministic correction stays in evidence.
        decision.model_version = "bulk-deterministic"
        decision.prompt_version = "single_threshold_v1"
        decision.confidence = 1.0
        ev = dict(decision.evidence) if isinstance(decision.evidence, dict) else {}
        # A changed verdict cannot retain the prior rule path/factors. Doing so
        # makes the presenter literally explain the opposite action. Re-run the
        # deterministic engine for a complete fresh snapshot; the compact
        # fallback is only for test/mocked or best-effort recompute failures.
        for key in _STALE_CAUSAL_KEYS:
            ev.pop(key, None)
        has_task = role_has_assessment_stage(role)
        fresh_evidence = None
        try:
            inputs = _inputs_for(
                app,
                role_id=int(role.id),
                org_id=int(role.organization_id),
                eff=eff,
                has_task=has_task,
            )
            if inputs is not None:
                fresh_verdict = evaluate(inputs, db=db)
                fresh_type = resolve_persisted_decision_type(
                    fresh_verdict.decision_type,
                    has_assessment_task=has_task,
                )
                if fresh_type == new_type:
                    fresh_evidence = _policy_evidence(
                        app,
                        verdict=fresh_verdict,
                        decision_type=new_type,
                        role_fit=float(inputs.scores["role_fit_score"]),
                        pre_screen=float(inputs.scores["pre_screen_score"]),
                        eff=eff,
                        role=role,
                        has_task=has_task,
                        assessment_completed=bool(
                            inputs.flags.get("assessment_completed", False)
                        ),
                        source="rescore_auto_correction",
                    )
        except Exception:  # pragma: no cover - compact fallback below is safe
            fresh_evidence = None
        if fresh_evidence is None:
            score = _role_fit_score(app)
            trigger = (
                "role_fit_score <= role_fit_max"
                if new_type == "reject"
                else "role_fit_score >= role_fit_min"
            )
            action = "Reject" if new_type == "reject" else "Send an assessment"
            relation = "at or below" if new_type == "reject" else "at or above"
            policy_reasoning = (
                f"{action} because role fit {score:.0f} is {relation} the "
                f"configured threshold {eff:.0f}."
                if score is not None and eff is not None
                else f"{action} under the current role-fit policy."
            )
            details = getattr(app, "cv_match_details", None)
            candidate_summary = normalize_candidate_summary(
                details.get("summary") if isinstance(details, dict) else None
            )
            fresh_evidence = {
                "role_fit_score": score,
                "effective_threshold": eff,
                "has_assessment_task": has_task,
                "rule_path": [
                    f"point:{'reject' if new_type == 'reject' else 'send_assessment'}",
                    f"rule:fired:{trigger}",
                ],
                "engine_verdict": (
                    "queue_reject_decision"
                    if new_type == "reject"
                    else "queue_send_assessment"
                ),
                "policy_reasoning": policy_reasoning,
                "policy_basis": policy_reasoning,
                "decision_trigger": trigger,
                "decision_source": "policy",
                "source": "rescore_auto_correction",
            }
            if candidate_summary:
                fresh_evidence["candidate_summary"] = candidate_summary
        ev.update(fresh_evidence)
        ev.update(
            {
                "auto_corrected_from": prior_type,
                "auto_corrected_reason": "re-score flipped the deterministic verdict",
                "role_fit_score": _role_fit_score(app),
                "effective_threshold": eff,
            }
        )
        decision.evidence = ev
        try:
            fp, cfp, cvfp = _capture_input_fingerprint(
                db, application_id=int(app.id), role_id=int(role.id)
            )
            decision.input_fingerprint = fp
            decision.criteria_fingerprint = cfp
            decision.cv_fingerprint = cvfp
            decision.decision_dedup_key = _compute_dedup_key(
                db, application_id=int(app.id), decision_type=new_type
            )
        except Exception:  # pragma: no cover — fingerprint refresh is best-effort
            pass
        logger.info(
            "auto-corrected stale verdict app=%s %s -> %s",
            getattr(app, "id", "?"), prior_type, new_type,
        )
        # A corrected positive verdict belongs to the same autonomy contract as
        # a freshly-created one. Without this, a role switched to auto-promote
        # could retain an old HITL card forever because the cohort query quite
        # correctly excludes applications that already have a pending row.
        from ...agent_runtime.tool_registry import maybe_auto_execute_decision
        from ...domains.assessments_runtime.pipeline_service import (
            is_post_handover_workable_stage,
        )

        maybe_auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type=new_type,
            on_policy=True,
            force_human_review=is_post_handover_workable_stage(
                getattr(app, "workable_stage", None)
            ),
        )
        return new_type
    except Exception:  # noqa: BLE001 — never break scoring
        logger.exception(
            "auto_correct_stale_verdict failed app=%s", getattr(app, "id", "?")
        )
        return None
