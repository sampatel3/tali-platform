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

from ...models.agent_decision import AgentDecision
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ..auto_threshold_service import resolve_role_fit_threshold
from ._shared import _recruiter_reasoning, _role_fit_score

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


def _verdict_has_independent_gate(decision: AgentDecision) -> bool:
    """True when the decision rests on a reason the pure-rule score band can't
    see (location/salary/visa/fraud, or a structured must-have gap) — leave it
    for the recruiter rather than auto-flipping on a score change."""
    ev = decision.evidence if isinstance(decision.evidence, dict) else {}
    if ev.get("must_have_gaps") or ev.get("must_have_blocked"):
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
        ev.update(
            {
                "auto_corrected_from": prior_type,
                "auto_corrected_reason": "re-score flipped the deterministic verdict",
                "role_fit_score": _role_fit_score(app),
                "effective_threshold": eff,
                "source": "rescore_auto_correction",
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
        return new_type
    except Exception:  # noqa: BLE001 — never break scoring
        logger.exception(
            "auto_correct_stale_verdict failed app=%s", getattr(app, "id", "?")
        )
        return None
