"""Server-owned freshness gate for single and bulk decision approvals."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication


_ENGINE_OUTDATED = "engine_outdated"
logger = logging.getLogger("taali.decision_approval_guard")


def approval_staleness_report(db: Session, decision: AgentDecision) -> Any:
    """Evaluate the correct owner/related-role freshness contract."""
    from . import decision_staleness
    from .decision_role_context import (
        is_cross_role_decision,
        load_related_evaluation,
        related_decision_staleness,
    )

    application = db.get(CandidateApplication, int(decision.application_id))
    if is_cross_role_decision(decision, application):
        evaluation = load_related_evaluation(
            db,
            decision=decision,
            application=application,
        )
        return related_decision_staleness(
            db,
            decision,
            evaluation,
            application=application,
        )
    return decision_staleness.evaluate(
        db,
        decision,
        application=application,
    )


def blocking_staleness_reasons(report: Any) -> list[str]:
    """Input drift is blocking; an old engine alone remains forceable."""
    return [
        str(reason)
        for reason in list(getattr(report, "reasons", None) or [])
        if str(reason) != _ENGINE_OUTDATED
    ]


def enforce_decision_approval_freshness(
    db: Session,
    decision: AgentDecision,
    *,
    allow_engine_outdated: bool,
) -> Any:
    """Reject stale-input approvals for every API and worker entry point.

    ``allow_engine_outdated`` permits the bounded, existing override where the
    candidate inputs are unchanged and only the scoring engine version is old.
    It never permits score-generation, CV, criteria, note, threshold, or
    related-role input drift.
    """
    try:
        report = approval_staleness_report(db, decision)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "decision approval freshness could not be verified decision_id=%s",
            getattr(decision, "id", None),
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "decision_freshness_unknown",
                "message": (
                    "Decision freshness could not be verified. Nothing was "
                    "accepted; refresh the queue and try again."
                ),
            },
        ) from exc

    reasons = [str(reason) for reason in list(getattr(report, "reasons", None) or [])]
    blocking = blocking_staleness_reasons(report)
    engine_outdated_only = bool(reasons) and all(
        reason == _ENGINE_OUTDATED for reason in reasons
    )
    if bool(getattr(report, "is_stale", False)) and not (
        allow_engine_outdated and engine_outdated_only
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "decision_stale",
                "message": (
                    "Inputs cited by this decision changed after it was queued. "
                    "Re-evaluate before approving."
                    if blocking
                    else (
                        (
                            "This decision was scored by an older engine. "
                            "Re-evaluate or explicitly approve the unchanged "
                            "old score."
                        )
                        if engine_outdated_only
                        else (
                            "Decision freshness could not be safely verified. "
                            "Re-evaluate before approving."
                        )
                    )
                ),
                "reasons": reasons,
                "summary": getattr(report, "summary", None),
            },
        )
    return report


def enforce_decision_approval_eligibility(
    db: Session,
    decision: AgentDecision,
    *,
    allow_engine_outdated: bool,
    application: CandidateApplication | None = None,
) -> Any:
    """Require a live application independently of historical staleness.

    Resolved applications deliberately evaluate as non-stale so their audit
    snapshots stay frozen.  That read-side rule is never execution authority:
    approval must fail closed once the candidate is rejected, hired, or handed
    off beyond Tali, even when every cited input is unchanged.
    """
    if application is None:
        application = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(decision.application_id),
                CandidateApplication.organization_id
                == int(decision.organization_id),
            )
            .populate_existing()
            .one_or_none()
        )
    if application is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "decision_application_unavailable",
                "message": "The decision's application is unavailable. Nothing was accepted.",
            },
        )

    # Lazy import avoids pulling the assessments runtime (which imports the
    # pipeline service) into this small guard at module-import time.
    from ..domains.assessments_runtime.role_support import is_resolved

    if is_resolved(application):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "application_resolved",
                "message": (
                    "This candidate has already left Tali's active flow. "
                    "The decision remains available as an audit record but "
                    "cannot be approved."
                ),
            },
        )
    return enforce_decision_approval_freshness(
        db,
        decision,
        allow_engine_outdated=allow_engine_outdated,
    )


__all__ = [
    "approval_staleness_report",
    "blocking_staleness_reasons",
    "enforce_decision_approval_eligibility",
    "enforce_decision_approval_freshness",
]
