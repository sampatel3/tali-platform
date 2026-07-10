"""Deterministic SECOND OPINION on a candidate the recruiter advanced in Workable.

The recruiter moved them into a post-handover Workable stage (Phone Screen /
Technical / Final Interview / Offer); Taali still scores them. A reject verdict
is surfaced in the reject queue (pulling them back from 'advanced' to review); a
positive verdict reflects the hand-off. LOCAL only — never writes to Workable.
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
    is_terminal_workable_stage,
    normalize_pipeline_stage,
    transition_stage,
)
from ...models.agent_decision import AgentDecision
from ...models.agent_run import AgentRun
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ..auto_threshold_service import resolve_role_fit_threshold
from ._shared import _inputs_for, _recruiter_reasoning

logger = logging.getLogger("taali.bulk_decision")


def decide_post_handover(db: Session, *, app: CandidateApplication, role: Role) -> str | None:
    """Taali's deterministic SECOND OPINION on a candidate the recruiter moved
    into a post-handover Workable stage (Phone Screen / Technical / Final
    Interview / Offer). The recruiter advanced them; Taali still scores them:

      * reject verdict  → surface it in the REJECT QUEUE — pull them back from
        'advanced' to review (so the reject reads as a live card, not a footnote
        on an advanced row) and queue the deterministic reject.
      * advance verdict → return 'advance'; the caller reflects the hand-off
        ('advanced' on Taali).

    LOCAL only — NEVER writes to Workable (the recruiter's stage is theirs). HITL
    (the recruiter approves/overrides; never auto-applied). Returns the queued
    reject ``decision_type`` (caller must NOT advance), ``'advance'`` for a
    positive verdict (caller advances), or ``None`` when undecidable (caller
    advances by default). Does NOT commit.
    """
    try:
        if getattr(app, "application_outcome", None) != "open":
            return None
        eff = resolve_role_fit_threshold(db, role=role)
        has_task = role_has_assessment_stage(role)
        inputs = _inputs_for(
            app, role_id=int(role.id), org_id=int(role.organization_id),
            eff=eff, has_task=has_task,
        )
        if inputs is None:
            return None
        verdict = evaluate(inputs, db=db)
        if verdict.decision_type not in QUEUEABLE_VERDICTS:
            return None  # escalate / no_action — leave to the recruiter/LLM
        decision_type = resolve_persisted_decision_type(
            verdict.decision_type, has_assessment_task=has_task
        )
        if decision_type not in ("reject", "skip_assessment_reject"):
            return "advance"  # positive verdict — caller reflects the hand-off

        # Reject verdict on a candidate the recruiter is actively INTERVIEWING in
        # Workable (a non-terminal post-handover stage: phone / technical / final).
        # This is a warning, not an action — and surfacing it as a live reject card
        # is exactly the dangerous case the reconcile (rightly) refuses to keep
        # ("don't reject someone in a live interview"). Pulling them advanced→review
        # just to host that card STRANDS them in 'review' the moment the card is
        # discarded — looking like they await a Taali decision when they don't.
        # So defer entirely: leave them 'advanced' (handed off to the recruiter's
        # interview), queue nothing. Only a TERMINAL hand-off (offer / hired) — a
        # decision that's imminent — still surfaces the reject below.
        if not is_terminal_workable_stage(getattr(app, "workable_stage", None)):
            return None

        # Reject on a terminal hand-off: don't leave them silently 'advanced'.
        # Pull back to the review queue so it's a live reject card.
        if normalize_pipeline_stage(app.pipeline_stage) == "advanced":
            # source='agent', not 'sync': this is Taali's agent overriding its
            # own earlier auto-advance, and the sync guard (rightly) blocks sync
            # from moving a locally-edited (version>1) stage backward.
            transition_stage(
                db, app=app, to_stage="review", source="agent", actor_type="agent",
                reason=f"Taali second opinion: reject (recruiter advanced in Workable — {app.workable_stage})",
                idempotency_key=f"posthandover_reject_review:{app.id}",
            )
        existing = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.application_id == int(app.id),
                AgentDecision.status.in_(("pending", "processing")),
            )
            .first()
        )
        if existing is not None:
            return decision_type  # already queued — don't double

        role_fit = inputs.scores["role_fit_score"]
        pre_screen = inputs.scores["pre_screen_score"]
        policy_basis = (
            f"role-fit {role_fit:.0f} vs threshold "
            f"{eff if eff is not None else 'default'} (pre-screen {pre_screen:.0f}) "
            f"→ {decision_type}; recruiter advanced in Workable ({app.workable_stage})"
        )
        reasoning = _recruiter_reasoning(app) or f"Deterministic policy: {policy_basis}"
        evidence = {
            "role_fit_score": role_fit,
            "pre_screen_score": pre_screen,
            "effective_threshold": eff,
            "rule_path": verdict.rule_path,
            "engine_verdict": verdict.decision_type,
            "policy_basis": policy_basis,
            "source": "post_handover_second_opinion",
            "workable_stage": app.workable_stage,
        }
        run = AgentRun(
            organization_id=int(role.organization_id),
            role_id=int(role.id),
            trigger="post_handover_second_opinion",
            status="completed",
            model_version="bulk-deterministic",
            prompt_version="single_threshold_v1",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        actor = Actor.agent(int(run.id))
        try:
            queue_decision.run(
                db, actor,
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
        except HTTPException as exc:  # terminal-state race etc. — never raise
            logger.info(
                "post-handover reject queue refused app=%s: %s",
                app.id, getattr(exc, "detail", exc),
            )
            return None
        return decision_type
    except Exception:  # noqa: BLE001 — never break the sync
        logger.exception("decide_post_handover failed app=%s", getattr(app, "id", "?"))
        return None
