"""Pre-screen-stage auto-reject decider.

Lives in the same package as ``decision_policy.engine`` so the
pre-screen-stage reject and the agent's full-pipeline reject share a
home. The function computes the per-role eligibility (threshold +
outcome + workable link + score availability), surfaces it as the
``pre_screen_auto_reject_eligible`` flag the engine's bootstrap rule
reads, and consults the engine for the verdict — falling back to the
legacy threshold check when the active policy doesn't have the
engine-side rule wired up yet, so behaviour is preserved for orgs
whose policies were bootstrapped before the rule landed.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .engine import DecisionInputs, evaluate as evaluate_policy

logger = logging.getLogger("taali.decision_policy.auto_reject")


def evaluate_auto_reject_decision(
    app: CandidateApplication,
    *,
    org: Organization | None,
    role: Role | None,
    db: Session | None = None,
) -> dict[str, Any]:
    """Decide whether the pre-screen auto-reject path should fire.

    Returns the legacy dict shape ``run_auto_reject_if_needed`` expects:
    ``{should_trigger, state, reason, config, snapshot, ...}``. When
    ``db`` is supplied and the active policy includes the engine-side
    rule, the verdict carries ``policy_revision_id`` so the audit row
    points back at the policy that produced it.
    """
    # Local imports avoid a circular dependency with pre_screening_service
    # (which itself imports decision_policy via the engine indirectly).
    from ..services.pre_screening_service import (
        pre_screen_snapshot,
        resolved_auto_reject_config,
    )

    snapshot = pre_screen_snapshot(app)
    config = resolved_auto_reject_config(org, role, db=db)
    score = snapshot["pre_screen_score"]
    threshold = config["threshold_100"]

    if app.application_outcome != "open":
        return {
            "should_trigger": False,
            "state": "skipped",
            "reason": "Application is already closed locally",
            "config": config,
            "snapshot": snapshot,
        }
    if not config["enabled"]:
        return {
            "should_trigger": False,
            "state": "disabled",
            "reason": "Auto reject is disabled",
            "config": config,
            "snapshot": snapshot,
        }
    if threshold is None:
        return {
            "should_trigger": False,
            "state": "disabled",
            "reason": "Auto reject threshold is not configured",
            "config": config,
            "snapshot": snapshot,
        }
    if score is None:
        return {
            "should_trigger": False,
            "state": "pending_score",
            "reason": "Pre-screen score is not available yet",
            "config": config,
            "snapshot": snapshot,
        }
    if not getattr(app, "workable_candidate_id", None):
        return {
            "should_trigger": False,
            "state": "skipped",
            "reason": "Candidate is not linked to Workable",
            "config": config,
            "snapshot": snapshot,
        }

    if score >= threshold:
        return {
            "should_trigger": False,
            "state": "not_triggered",
            "reason": f"Pre-screen score {score:.1f} meets threshold {threshold:.1f}",
            "config": config,
            "snapshot": snapshot,
        }

    legacy_reason = (
        f"Pre-screen score {score:.1f} is below configured threshold "
        f"{threshold:.1f}"
    )
    if db is None or role is None or org is None:
        return {
            "should_trigger": True,
            "state": "eligible",
            "reason": legacy_reason,
            "config": config,
            "snapshot": snapshot,
        }

    try:
        inputs = DecisionInputs(
            application_id=int(app.id),
            role_id=int(role.id),
            organization_id=int(org.id),
            scores={"pre_screen_score": float(score)},
            flags={
                "pre_screen_auto_reject_eligible": True,
                "no_pending_assessment": True,
            },
        )
        verdict = evaluate_policy(inputs, db=db)
    except Exception:  # pragma: no cover — defensive
        logger.exception("decision engine call failed in evaluate_auto_reject_decision")
        verdict = None

    if verdict is not None and verdict.decision_type == "auto_reject":
        return {
            "should_trigger": True,
            "state": "eligible",
            "reason": verdict.reasoning or legacy_reason,
            "config": config,
            "snapshot": snapshot,
            "policy_revision_id": verdict.policy_revision_id,
        }
    # Engine returned no_action/skip — likely the active policy hasn't
    # been migrated to include the new rule. Fall through to the legacy
    # verdict so behaviour is unchanged on older orgs.
    return {
        "should_trigger": True,
        "state": "eligible",
        "reason": legacy_reason,
        "config": config,
        "snapshot": snapshot,
    }
