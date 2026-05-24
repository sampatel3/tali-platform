"""Deterministic bulk decisioning — give EVERY scored candidate a verdict.

The decision-policy engine verdict is fully deterministic, so we don't
need the LLM agent (capped at ~1 send + ~5 rejects per 30-min cycle) to
work through a large cohort one candidate at a time. This pass runs the
engine over every undecided, scored, open candidate using the scores
ALREADY stored on the application — no sub-agents, no Anthropic calls —
and queues the verdict through the normal ``queue_decision`` guard stack
(one-pending-per-app, cross-cycle dedup, terminal-state refusal).

Coverage is EVERY scored candidate, not just pre-screen-passers. A scored
candidate below the pre-screen line is owned by nobody else: the
pre-screen reject emitter defers once a candidate is cv_match-scored
("agent owns the cv_match decision"), so without this pass it can only be
decided by the LLM — and strands when the LLM is unreachable. Banding the
engine on role-fit covers it deterministically.

Banding (after the effective-threshold overlay collapses the boundary):
  - role_fit < threshold              -> reject
  - role_fit >= threshold, has task   -> send_assessment   (needs pre_screen >= 50)
  - role_fit >= threshold, no task    -> advance_to_interview (needs pre_screen >= 50)
  - role_fit >= threshold, pre_screen < 50 -> no_action (left to LLM/recruiter)

The send_assessment rule independently gates on ``pre_screen_min`` (50),
which ``apply_effective_threshold`` leaves untouched — so a low-pre-screen
candidate can never be auto-sent/advanced; it either rejects on role-fit
or falls through to ``no_action``. The LLM agent still runs afterward for
those judgment/abstention/recruiter cases; ``find_apps_in_state`` excludes
apps that now have a pending decision, so there's no double-queue.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import and_, not_
from sqlalchemy.orm import Session

from ..actions import queue_decision
from ..actions.ask_recruiter import open as ask_recruiter_open
from ..actions.types import Actor
from .auto_threshold_service import resolve_role_fit_threshold
from ..agent_runtime.decision_translation import (
    QUEUEABLE_VERDICTS,
    resolve_persisted_decision_type,
)
from ..domains.assessments_runtime.pipeline_service import (
    is_post_handover_workable_stage,
)
from ..decision_policy.engine import DecisionInputs, evaluate
from ..models.agent_decision import AgentDecision
from ..models.agent_run import AgentRun
from ..models.candidate_application import CandidateApplication
from ..models.role import Role

logger = logging.getLogger("taali.bulk_decision")

# Cap per role per pass so one tick can't run unbounded DB work; a 300+
# cohort still clears in one or two ticks.
DEFAULT_PER_TICK_LIMIT = 250

# When at least this many pending positive decisions await the recruiter,
# raise a threshold question (heavy review load — maybe raise the bar).
VOLUME_GUARD_PENDING_LIMIT = 60

_POSITIVE_TYPES = ("send_assessment", "advance_to_interview")


def _role_fit_score(app: CandidateApplication) -> float | None:
    val = getattr(app, "role_fit_score_cache_100", None)
    if val is None:
        val = getattr(app, "cv_match_score", None)
    return float(val) if val is not None else None


def _recruiter_reasoning(app: CandidateApplication) -> str | None:
    """Recruiter-facing decision narrative, sourced from the CV-match
    ``summary`` — the same field that drives the candidate report's
    recommendation hero text and is quoted into the Workable note. Falls
    back to the first score-rationale bullet. Returns None when no
    qualitative narrative exists; the caller then substitutes the
    audit-oriented policy basis so the reasoning is never blank."""
    details = getattr(app, "cv_match_details", None)
    if not isinstance(details, dict):
        return None
    summary = str(details.get("summary") or "").strip()
    if summary:
        return summary
    bullets = details.get("score_rationale_bullets")
    if isinstance(bullets, list):
        for bullet in bullets:
            text = str(bullet or "").strip()
            if text:
                return text
    return None


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
    return DecisionInputs(
        application_id=int(app.id),
        role_id=int(role_id),
        organization_id=int(org_id),
        scores={"role_fit_score": role_fit, "pre_screen_score": pre_screen},
        flags={
            # applied/review + open => no assessment in flight, so the
            # assessment-gate rules (priority 90/85) don't fire and we reach
            # the threshold band.
            "no_pending_assessment": True,
            "has_pending_assessment": False,
            "assessment_completed": False,
            "must_have_blocked": False,
            "has_assessment_task": has_task,
        },
        effective_role_fit_threshold=eff,
    )


def _reconcile_stale_pending(db: Session, *, role: Role, eff, has_task: bool) -> int:
    """Re-evaluate this role's bulk-created PENDING decisions against the
    current (recalibrated) threshold; discard any whose band has flipped so
    the main pass re-decides them with the new bar. This is what makes a
    threshold change actually move existing decisions.

    Only touches ``model_version='bulk-deterministic'`` pending rows — LLM
    decisions are the agent's to manage, and pre-screen rejects are
    reconciled separately. Resolved/advanced candidates are never pending,
    so they stay frozen. Discarding only on a genuine flip (not equal)
    plus the queue's recently-discarded guard bounds churn."""
    pendings = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
            AgentDecision.model_version == "bulk-deterministic",
            AgentDecision.decision_type.in_(
                ["reject", "send_assessment", "advance_to_interview"]
            ),
        )
        .all()
    )
    if not pendings:
        return 0
    discarded = 0
    now = datetime.now(timezone.utc)
    for d in pendings:
        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == d.application_id)
            .one_or_none()
        )
        if app is None:
            continue
        inputs = _inputs_for(app, role_id=role.id, org_id=role.organization_id, eff=eff, has_task=has_task)
        if inputs is None:
            continue
        try:
            verdict = evaluate(inputs, db=db)
        except Exception:
            continue
        new_type = resolve_persisted_decision_type(
            verdict.decision_type, has_assessment_task=has_task
        )
        if new_type is not None and new_type != d.decision_type:
            d.status = "discarded"
            d.resolved_at = now
            d.resolution_note = (
                f"threshold recalibrated to {round(eff) if eff is not None else 'n/a'}; "
                f"re-deciding ({d.decision_type} → {new_type})"
            )[:500]
            discarded += 1
    if discarded:
        db.commit()
    return discarded


def decide_role_cohort(
    db: Session, *, role: Role, limit: int = DEFAULT_PER_TICK_LIMIT
) -> dict:
    """Decide every undecided, pre-screen-pass, scored, open candidate.

    Returns a summary dict. Never raises — a bad candidate is counted as
    an error and the pass continues. Commits its own work.
    """
    org_id = int(role.organization_id)
    eff = resolve_role_fit_threshold(db, role=role)
    has_task = bool(getattr(role, "tasks", None))

    summary: Counter = Counter()
    # First, re-flow existing bulk decisions against the (possibly
    # recalibrated) threshold — discard ones whose band flipped so they're
    # re-decided below with the current bar.
    try:
        summary["reconciled_discarded"] = _reconcile_stale_pending(
            db, role=role, eff=eff, has_task=has_task
        )
    except Exception:
        logger.exception("threshold reconcile failed role=%s", role.id)
        db.rollback()

    candidates = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.pipeline_stage.in_(["applied", "review"]),
            CandidateApplication.cv_match_score.isnot(None),
            # Freeze candidates disqualified in Workable even if Tali's outcome
            # hasn't synced yet — otherwise we queue (and then have to discard)
            # advance/reject decisions for someone the recruiter already
            # dismissed externally.
            CandidateApplication.workable_disqualified_at.is_(None),
            # A 'processing' decision (approved, writeback in flight or stuck)
            # blocks a new one too — counting only 'pending' let stranded
            # 'processing' rows spawn duplicates.
            not_(
                db.query(AgentDecision.id)
                .filter(
                    AgentDecision.application_id == CandidateApplication.id,
                    AgentDecision.status.in_(("pending", "processing")),
                )
                .exists()
            ),
        )
        .order_by(CandidateApplication.cv_match_score.desc())
        .limit(int(limit))
        .all()
    )

    summary["candidates"] = len(candidates)
    if not candidates:
        _maybe_raise_volume_guard(db, role=role, org_id=org_id)
        return dict(summary)

    run = AgentRun(
        organization_id=org_id,
        role_id=int(role.id),
        trigger="bulk_decision",
        status="running",
        model_version="bulk-deterministic",
        prompt_version="single_threshold_v1",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()  # assign run.id
    actor = Actor.agent(int(run.id))

    for app in candidates:
        # A human recruiter has already advanced this candidate past Tali's
        # handover point in Workable (phone screen / interview / offer). That's
        # a strong positive signal — Tali must NOT reject them on role-fit
        # alone (the agent prompt's EXTERNAL PIPELINE STAGE rule). Leave them
        # to the recruiter / agent, don't deterministically decide.
        if is_post_handover_workable_stage(getattr(app, "workable_stage", None)):
            summary["skipped_post_handover"] += 1
            continue
        inputs = _inputs_for(app, role_id=role.id, org_id=org_id, eff=eff, has_task=has_task)
        if inputs is None:
            summary["skipped_missing_score"] += 1
            continue
        role_fit = inputs.scores["role_fit_score"]
        pre_screen = inputs.scores["pre_screen_score"]
        try:
            verdict = evaluate(inputs, db=db)
        except Exception:
            logger.exception("bulk evaluate failed app=%s", app.id)
            summary["errors"] += 1
            continue

        if verdict.decision_type not in QUEUEABLE_VERDICTS:
            summary[f"verdict_{verdict.decision_type}"] += 1
            continue
        decision_type = resolve_persisted_decision_type(
            verdict.decision_type, has_assessment_task=has_task
        )
        if decision_type is None:
            summary["skipped"] += 1
            continue

        # Audit basis: the threshold comparison that drove the verdict.
        # Kept in evidence (not the recruiter-facing reasoning) so the
        # headline reads like the candidate report, not policy mechanics.
        policy_basis = (
            f"role-fit {role_fit:.0f} vs threshold "
            f"{eff if eff is not None else 'default'} (pre-screen {pre_screen:.0f}) "
            f"→ {decision_type}"
            + ("" if has_task else "; role has no assessment task, advancing directly")
        )
        # Recruiter headline = the CV-match narrative (same source as the
        # report hero); fall back to the audit basis when none exists so
        # queue_decision's non-blank guard always passes.
        reasoning = _recruiter_reasoning(app) or f"Deterministic policy: {policy_basis}"
        evidence = {
            "role_fit_score": role_fit,
            "pre_screen_score": pre_screen,
            "effective_threshold": eff,
            "has_assessment_task": has_task,
            "rule_path": verdict.rule_path,
            "engine_verdict": verdict.decision_type,
            "policy_basis": policy_basis,
            "source": "bulk_decision",
        }
        try:
            decision = queue_decision.run(
                db,
                actor,
                organization_id=org_id,
                role_id=int(role.id),
                application_id=int(app.id),
                decision_type=decision_type,
                reasoning=reasoning,
                evidence=evidence,
                confidence=float(verdict.confidence or 0.0),
                model_version="bulk-deterministic",
                prompt_version=str(verdict.policy_revision_id or "single_threshold_v1"),
                recommendation=decision_type,
                skip_episode=True,
            )
        except HTTPException as exc:
            # Pre-filtered to open/applied so terminal-state refusals are
            # rare; count and continue.
            logger.info("bulk queue refused app=%s: %s", app.id, getattr(exc, "detail", exc))
            summary["errors"] += 1
            continue

        if getattr(decision, "_just_created", True):
            summary["created"] += 1
            summary[decision_type] += 1
        else:
            summary["dedup"] += 1

    run.status = "completed"
    run.decisions_emitted = int(summary["created"])
    run.finished_at = datetime.now(timezone.utc)
    db.add(run)
    db.commit()

    _maybe_raise_volume_guard(db, role=role, org_id=org_id, actor=actor)
    logger.info("bulk_decision role=%s summary=%s", role.id, dict(summary))
    return dict(summary)


def _maybe_raise_volume_guard(
    db: Session, *, role: Role, org_id: int, actor: Actor | None = None
) -> None:
    """If a lot of positive decisions are piling up for recruiter review,
    open an idempotent threshold question proposing a higher bar. In auto
    mode the threshold already self-calibrates, but surfacing the load
    lets the recruiter intervene. Best-effort — never raises."""
    try:
        pending_positive = (
            db.query(AgentDecision.id)
            .filter(
                AgentDecision.role_id == int(role.id),
                AgentDecision.status == "pending",
                AgentDecision.decision_type.in_(_POSITIVE_TYPES),
            )
            .count()
        )
        if pending_positive < VOLUME_GUARD_PENDING_LIMIT:
            return
        ask_recruiter_open(
            db,
            actor or Actor.system(),
            organization_id=org_id,
            role_id=int(role.id),
            kind="threshold_ambiguous",
            prompt=(
                f"{pending_positive} candidates are above the current bar and "
                "waiting for your review. Want to raise the threshold so the "
                "agent only surfaces stronger matches?"
            ),
            rationale=(
                "High review load: a large share of scored candidates clear the "
                "current role-fit threshold. Raising it focuses review on the "
                "strongest candidates."
            ),
        )
        db.commit()
    except Exception:  # pragma: no cover — guard must never break the pass
        db.rollback()
        logger.warning("volume guard failed for role %s", getattr(role, "id", "?"))


__all__ = ["decide_role_cohort", "DEFAULT_PER_TICK_LIMIT", "VOLUME_GUARD_PENDING_LIMIT"]
