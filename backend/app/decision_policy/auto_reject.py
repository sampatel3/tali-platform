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
    role_score_threshold = config.get("threshold_100")
    from ..services.prescreen_gate_calibration import (
        resolve_enforced_gate_threshold,
    )

    evidence = (
        app.pre_screen_evidence
        if isinstance(getattr(app, "pre_screen_evidence", None), dict)
        else {}
    )
    # A pre-screen card may only enforce the Stage-1 gate.  The role's score
    # threshold belongs to the downstream full-score/send decision; using it
    # here rejected 30–49 candidates whom Stage 1 deliberately passed for full
    # evaluation.  Preserve it separately for audit/UI context.
    config = {
        **config,
        "role_score_threshold_100": role_score_threshold,
        "threshold_100": resolve_enforced_gate_threshold(
            db,
            role=role,
            evidence=evidence,
        ),
    }
    score = snapshot["pre_screen_score"]
    recommendation = snapshot.get("pre_screen_recommendation")
    threshold = config["threshold_100"]
    # A pre-screen reject is DETERMINISTIC policy (score vs the role's cutoff),
    # so the decision is always produced and surfaced as a Decision Hub card —
    # independent of the agent toggle. The agent governs autonomous *execution*,
    # not whether a deterministic reject exists for human review.
    #
    # ``auto_disqualify_eligible`` gates ONLY the irreversible Workable
    # auto-disqualify write-back (still opt-in), via either:
    #   1. the legacy org-level Workable switch (``config['enabled']``), OR
    #   2. role-level ``agentic_mode_enabled`` (agent-managed roles).
    # The caller additionally requires ``role.auto_reject_pre_screen``; when
    # either condition is absent the reject is carded for manual review instead
    # of written back.
    role_paused = bool(
        role is not None and getattr(role, "agent_paused_at", None) is not None
    )
    workspace_paused = False
    if db is not None and role is not None:
        from ..services.workspace_agent_control import workspace_agent_is_paused

        workspace_paused = workspace_agent_is_paused(
            db,
            organization_id=int(role.organization_id),
        )
    agentic_eligible = bool(
        role is not None
        and getattr(role, "agentic_mode_enabled", False)
        and not role_paused
        and not workspace_paused
    )
    # Pause is a role-wide execution stop, including the legacy org-level
    # Workable switch. The deterministic verdict still exists and is surfaced
    # as a HITL card; only the irreversible provider/native write is withheld.
    auto_disqualify_eligible = bool(
        (bool(config["enabled"]) or agentic_eligible)
        and not role_paused
        and not workspace_paused
    )
    # HARD RAIL: irreversible rejection fails closed across every ATS.  A
    # normalized post-handover state means a recruiter already advanced the
    # candidate; an unmapped Bullhorn state is intentionally unknown and must
    # be reviewed before automation acts.  The deterministic reject still
    # surfaces as a HITL card in both cases.
    from ..services.ats_context_service import application_ats_context

    ats_context = application_ats_context(app)
    if ats_context["post_handover"] or ats_context["needs_mapping"]:
        auto_disqualify_eligible = False

    if app.application_outcome != "open":
        return {
            "should_trigger": False,
            "state": "skipped",
            "reason": "Application is already closed locally",
            "config": config,
            "snapshot": snapshot,
        }
    # Defer to full scoring. Pre-screen auto-reject is a cheap gate that runs
    # BEFORE full cv_match scoring to avoid paying for it. Once a candidate
    # has a cv_match score, that score is authoritative and the agent's
    # cv_match flow owns the reject/send decision. Re-firing the pre-screen
    # gate here used to mislabel fully-scored candidates: the snapshot's
    # ``pre_screen_score`` mirrors ``cv_match_score`` once scored, so this
    # gate was effectively rejecting on the full score while typing it as a
    # pre-screen reject — including candidates the full scorer rated strong.
    if getattr(app, "cv_match_score", None) is not None:
        return {
            "should_trigger": False,
            "state": "deferred_to_full_scoring",
            "reason": "Candidate has a full cv_match score; reject/send is the agent's decision",
            "config": config,
            "snapshot": snapshot,
        }
    # Respect the pre-screen verdict. The decision ('yes'/'maybe') is the
    # authoritative gate result; a passed candidate must never be auto-rejected
    # even if the numeric ``pre_screen_score`` was contaminated by a cv_match
    # write that disagrees with it (see app 48632: decision 'yes', llm 75,
    # but the column held a stale 16.7).
    _ps_ev = evidence
    if str(_ps_ev.get("decision") or "").strip().lower() in ("yes", "maybe"):
        return {
            "should_trigger": False,
            "state": "pre_screen_passed",
            "reason": "Pre-screen decision was 'yes' — candidate passed the gate, not a reject",
            "config": config,
            "snapshot": snapshot,
        }
    # Require a GENUINE pre-screen run. The recommendation / score columns can be
    # populated by a cv_match snapshot refresh without any pre-screen running
    # (e.g. a cv_match 'no' whose score was later invalidated), which would
    # otherwise fire a "pre-screen reject" for a never-pre-screened candidate.
    # ``pre_screen_run_at`` is set ONLY by the pre-screen engine, never by the
    # snapshot — so it cleanly separates a real reject from a stale label.
    if getattr(app, "pre_screen_run_at", None) is None:
        return {
            "should_trigger": False,
            "state": "not_pre_screened",
            "reason": "No pre-screen has run for this candidate",
            "config": config,
            "snapshot": snapshot,
        }
    # A recommendation without the durable genuine score is a legacy/display
    # artifact, not safe rejection evidence.  Fail open to full scoring rather
    # than reviving the contaminated shared-column behaviour.
    rec_says_reject = isinstance(recommendation, str) and recommendation.strip().lower() == "below threshold"
    if threshold is None and not rec_says_reject:
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
            "state": "missing_genuine_pre_screen_score",
            "reason": "No durable genuine pre-screen score is available; continue to full scoring",
            "config": config,
            "snapshot": snapshot,
        }
    # Workable linkage is NOT required: the decision is surfaced as a Decision
    # Hub card (``queue_pre_screen_reject``), which needs no Workable round-trip.
    # An unlinked candidate is carded by the caller; only an
    # ``auto_disqualify_eligible`` + ``auto_reject_pre_screen`` role with a linked
    # candidate takes the irreversible Workable write-back path.

    if score is not None and threshold is not None and score >= threshold:
        return {
            "should_trigger": False,
            "state": "not_triggered",
            "reason": f"Pre-screen score {score:.1f} meets threshold {threshold:.1f}",
            "config": config,
            "snapshot": snapshot,
        }

    if score is not None and threshold is not None:
        legacy_reason = (
            f"Pre-screen score {score:.1f} is below configured threshold "
            f"{threshold:.1f}"
        )
    elif rec_says_reject:
        legacy_reason = "Pre-screen recommendation: Below threshold"
    else:
        legacy_reason = "Below threshold"
    if db is None or role is None or org is None:
        return {
            "should_trigger": True,
            "state": "eligible",
            "reason": legacy_reason,
            "config": config,
            "snapshot": snapshot,
            "auto_disqualify_eligible": auto_disqualify_eligible,
        }

    try:
        inputs = DecisionInputs(
            application_id=int(app.id),
            role_id=int(role.id),
            organization_id=int(org.id),
            # Engine reads a numeric score; pass 0.0 when the deterministic
            # path triggered via recommendation only so the policy rules
            # still see the candidate as "below threshold".
            scores={"pre_screen_score": float(score) if score is not None else 0.0},
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
            "auto_disqualify_eligible": auto_disqualify_eligible,
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
        "auto_disqualify_eligible": auto_disqualify_eligible,
    }
