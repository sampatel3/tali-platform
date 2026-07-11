"""Score-time single-candidate deterministic decision.

Materialises a SCORED candidate's deterministic verdict as a PENDING HITL
decision the moment the score lands — decoupled from the agent cohort tick
(which only runs on active roles, so paused-role candidates would otherwise
strand as "not yet decided"). Single-candidate twin of ``decide_role_cohort``
that deliberately omits its role-level side effects (no threshold reconcile, no
volume guard).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...actions import queue_decision
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


def ensure_deterministic_decision(
    db: Session, *, app: CandidateApplication, role: Role
) -> str | None:
    """Make sure a SCORED candidate carries its deterministic verdict as a
    PENDING HITL decision — generated the moment the score lands, decoupled from
    the agent cohort tick (which only runs on active roles, so paused-role
    candidates strand as "not yet decided"). The verdict is intrinsic to the
    score; this materialises it. Fresh score → HITL (the recruiter approves;
    NEVER auto-applied).

    Single-candidate twin of ``decide_role_cohort`` that reuses the same verdict
    + queue funnel, but deliberately OMITS its role-level side effects:
    NO ``_reconcile_stale_pending`` (role-wide threshold re-flow) and — critically
    — NO ``_maybe_raise_volume_guard`` (which would spam a threshold card per score
    during a backlog drain). The existing ``auto_correct_stale_verdict`` owns an
    app that ALREADY has a pending row; this owns the no-pending case. Touches
    zero role/agent state, runs no LLM, emits no episode.

    Best-effort: returns the queued ``decision_type`` on a fresh queue, else None.
    Never raises. Does NOT commit — the caller commits.
    """
    try:
        # An existing pending/processing card is auto_correct_stale_verdict's to
        # own — don't double-queue.
        existing = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.application_id == int(app.id),
                AgentDecision.status.in_(("pending", "processing")),
            )
            .first()
        )
        if existing is not None:
            return None
        # Cheap band guards so we never mint a useless AgentRun for a candidate
        # queue_decision.run would refuse (terminal/external freeze).
        if getattr(app, "application_outcome", None) != "open":
            return None
        if getattr(app, "pipeline_stage", None) not in ("applied", "review"):
            return None
        if getattr(app, "workable_disqualified_at", None) is not None:
            return None
        # A post-handover Workable stage (the recruiter moved them forward
        # there, possibly before the application ever entered Taali) does NOT
        # suppress the decision — the candidate is decided like everyone else.
        # The card carries the Workable stage so approve surfaces warn the
        # recruiter; execution stays HITL, never automated.
        post_handover = is_post_handover_workable_stage(
            getattr(app, "workable_stage", None)
        )

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
            return None  # escalate / no_action / skip — left to the LLM/recruiter
        decision_type = resolve_persisted_decision_type(
            verdict.decision_type, has_assessment_task=has_task
        )
        if decision_type is None:
            return None

        role_fit = inputs.scores["role_fit_score"]
        pre_screen = inputs.scores["pre_screen_score"]
        policy_basis = (
            f"role-fit {role_fit:.0f} vs threshold "
            f"{eff if eff is not None else 'default'} (pre-screen {pre_screen:.0f}) "
            f"→ {decision_type}"
            + _no_assessment_note(role, has_task)
        )
        reasoning = _recruiter_reasoning(app) or f"Deterministic policy: {policy_basis}"
        evidence = {
            "role_fit_score": role_fit,
            "pre_screen_score": pre_screen,
            "effective_threshold": eff,
            "has_assessment_task": has_task,
            "rule_path": verdict.rule_path,
            "engine_verdict": verdict.decision_type,
            "policy_basis": policy_basis,
            "source": "score_time_decision",
        }
        if post_handover:
            evidence["workable_stage"] = app.workable_stage
        run = AgentRun(
            organization_id=int(role.organization_id),
            role_id=int(role.id),
            trigger="score_time_decision",
            status="completed",
            model_version="bulk-deterministic",
            prompt_version="single_threshold_v1",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()  # assign run.id
        actor = Actor.agent(int(run.id))
        try:
            decision = queue_decision.run(
                db,
                actor,
                organization_id=int(role.organization_id),
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
        except HTTPException as exc:  # terminal-state race etc. — never fail scoring
            logger.info(
                "score-time queue refused app=%s: %s",
                app.id, getattr(exc, "detail", exc),
            )
            return None
        if getattr(decision, "_just_created", True):
            logger.info(
                "score-time deterministic decision app=%s -> %s", app.id, decision_type
            )
            return decision_type
        return None  # dedup / one-pending guard returned an existing row
    except Exception:  # noqa: BLE001 — never break scoring
        logger.exception(
            "ensure_deterministic_decision failed app=%s", getattr(app, "id", "?")
        )
        return None
