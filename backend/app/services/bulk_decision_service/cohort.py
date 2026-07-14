"""The role-cohort bulk decision pass.

Runs the deterministic engine over every undecided, scored, open candidate for a
role using the scores ALREADY stored on the application — no sub-agents, no
Anthropic calls — applies the role autonomy contract, re-flows existing bulk
decisions against the current threshold, and raises a volume guard when a lot
of positive decisions genuinely remain for recruiter review. Commits its own
work; never raises on a single bad candidate.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import not_
from sqlalchemy.orm import Session

from ...actions import queue_decision
from ...actions.ask_recruiter import open as ask_recruiter_open
from ...actions.types import Actor
from ...agent_runtime.decision_translation import (
    QUEUEABLE_VERDICTS,
    resolve_persisted_decision_type,
    role_has_assessment_stage,
)
from ...decision_policy.engine import evaluate
from ...domains.assessments_runtime.pipeline_service import (
    is_post_handover_workable_stage,
)
from ...models.agent_decision import AgentDecision
from ...models.agent_run import AgentRun
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ..auto_threshold_service import resolve_role_fit_threshold
from ._shared import _inputs_for, _no_assessment_note, _recruiter_reasoning

logger = logging.getLogger("taali.bulk_decision")

# Cap per role per pass so one tick can't run unbounded DB work; a 300+
# cohort still clears in one or two ticks.
DEFAULT_PER_TICK_LIMIT = 250

# When at least this many pending positive decisions await the recruiter,
# raise a threshold question (heavy review load — maybe raise the bar).
VOLUME_GUARD_PENDING_LIMIT = 60

_POSITIVE_TYPES = ("send_assessment", "advance_to_interview")


def _auto_execute_existing_pending_positives(
    db: Session,
    *,
    role: Role,
    eff,
    has_task: bool,
    limit: int,
) -> Counter:
    """Drain deterministic positive cards that pre-date auto-promote.

    This is the activation/recovery half of the one-switch contract. Every row
    is re-evaluated against CURRENT scores/policy before execution; stale,
    post-handover or guarded rows remain pending. LLM-authored rows are excluded
    because they lack the deterministic provenance needed for unattended replay.
    """
    result: Counter = Counter()
    rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
            AgentDecision.model_version == "bulk-deterministic",
            AgentDecision.decision_type.in_(_POSITIVE_TYPES),
        )
        .order_by(AgentDecision.id.asc())
        .limit(int(limit))
        .all()
    )
    if not rows:
        return result

    from ...agent_runtime.tool_registry import maybe_auto_execute_decision

    for decision in rows:
        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == int(decision.application_id))
            .one_or_none()
        )
        if app is None:
            continue
        inputs = _inputs_for(
            app,
            role_id=int(role.id),
            org_id=int(role.organization_id),
            eff=eff,
            has_task=has_task,
        )
        if inputs is None:
            continue
        try:
            verdict = evaluate(inputs, db=db)
            current_type = resolve_persisted_decision_type(
                verdict.decision_type, has_assessment_task=has_task
            )
        except Exception:
            logger.exception(
                "pending-positive re-evaluate failed decision=%s", decision.id
            )
            result["errors"] += 1
            continue
        if current_type != decision.decision_type:
            result["stale_skipped"] += 1
            continue
        outcome = maybe_auto_execute_decision(
            db,
            role=role,
            decision=decision,
            decision_type=str(decision.decision_type),
            on_policy=True,
            force_human_review=is_post_handover_workable_stage(
                getattr(app, "workable_stage", None)
            ),
        )
        if outcome["executed"]:
            result["executed"] += 1
        elif outcome["auto_send_held"] or outcome["action_held"]:
            result["held"] += 1
    return result


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
    has_task = role_has_assessment_stage(role)

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

    # A role may have accumulated deterministic HITL cards before the recruiter
    # turned on auto-promote. Drain the currently on-policy positives now; the
    # main candidate query below intentionally excludes pending rows and cannot
    # own this recovery case itself.
    try:
        existing = _auto_execute_existing_pending_positives(
            db,
            role=role,
            eff=eff,
            has_task=has_task,
            limit=limit,
        )
        summary["existing_auto_executed"] = existing["executed"]
        summary["existing_auto_held"] = existing["held"]
        summary["existing_auto_errors"] = existing["errors"]
        if existing["executed"] or existing["held"]:
            db.commit()
    except Exception:
        logger.exception("pending-positive autonomy drain failed role=%s", role.id)
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
        # A candidate may already sit in a post-handover Workable stage (the
        # recruiter moved them forward there before the application entered
        # Taali). They are decided like everyone else — the verdict is a HITL
        # card, never auto-executed — but the card carries the Workable stage
        # so every approve surface can warn "you're rejecting someone already
        # advanced in Workable" (advice, not a block).
        post_handover = is_post_handover_workable_stage(
            getattr(app, "workable_stage", None)
        )
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
            + _no_assessment_note(role, has_task)
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
        if post_handover:
            evidence["workable_stage"] = app.workable_stage
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
            try:
                from ...agent_runtime.tool_registry import maybe_auto_execute_decision

                autonomy = maybe_auto_execute_decision(
                    db,
                    role=role,
                    decision=decision,
                    decision_type=decision_type,
                    on_policy=True,
                    force_human_review=post_handover,
                )
                if autonomy["executed"]:
                    summary["auto_executed"] += 1
                elif autonomy["auto_send_held"] or autonomy["action_held"]:
                    summary["auto_held"] += 1
            except Exception:
                # Queueing the deterministic recommendation is the safe
                # fallback. One failed auto action must not abort the rest of
                # a large cohort.
                logger.exception(
                    "bulk auto-execute failed app=%s decision=%s",
                    app.id,
                    decision.id,
                )
                summary["auto_execute_errors"] += 1
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
