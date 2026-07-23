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
from ...components.scoring.freshness import (
    SCORE_JOB_DONE,
    ScoreAttempt,
    capture_score_generation,
    latest_score_attempts,
    score_generation_is_current,
)
from ...decision_policy.engine import evaluate
from ...domains.assessments_runtime.pipeline_service import (
    normalize_pipeline_stage,
    transition_stage,
)
from ...models.agent_decision import AgentDecision
from ...models.agent_run import AgentRun
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ..auto_threshold_service import resolve_role_fit_threshold
from ..role_execution_guard import lock_live_role
from ._shared import _inputs_for, _policy_evidence, _recruiter_reasoning

logger = logging.getLogger("taali.bulk_decision")
POST_HANDOVER_SCORE_REFRESH_REQUIRED = "score_refresh_required"


def decide_post_handover(
    db: Session,
    *,
    app: CandidateApplication,
    role: Role,
) -> str | None:
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
    positive verdict (caller advances),
    ``POST_HANDOVER_SCORE_REFRESH_REQUIRED`` when the captured generation
    changed before the locked boundary (caller must make no mutation and retry
    on a later sync), or ``None`` when undecidable (caller advances by default).
    Does NOT commit.
    """
    application_id: int | None = None
    entry_attempt: ScoreAttempt | None = None
    try:
        # Capture the exact eligible generation before reading any score-backed
        # verdict input from the caller's ORM snapshot. The Workable sync may
        # already have a dirty stage update on ``app``; never autoflush that
        # write before the canonical Organization → Role locks below.
        with db.no_autoflush:
            application_id = int(app.id)
            role_id = int(role.id)
            organization_id = int(role.organization_id)
            entry_attempt = latest_score_attempts(db, [application_id]).get(
                application_id
            )
            if entry_attempt is not None and entry_attempt.status != SCORE_JOB_DONE:
                return POST_HANDOVER_SCORE_REFRESH_REQUIRED
            score_generation = capture_score_generation(
                db,
                role=role,
                application_id=application_id,
            )
            if score_generation is None:
                # Distinguish the intentionally preserved cold-legacy fallback
                # (no attempt, no persisted score) from a score job that appeared
                # or changed status between the entry read and token capture.
                if latest_score_attempts(db, [application_id]).get(application_id):
                    return POST_HANDOVER_SCORE_REFRESH_REQUIRED
                return None
    except Exception:  # noqa: BLE001 — no captured authority; preserve fallback
        logger.exception(
            "post-handover score generation capture failed app=%s",
            application_id if application_id is not None else "?",
        )
        return (
            POST_HANDOVER_SCORE_REFRESH_REQUIRED
            if entry_attempt is not None
            else None
        )

    try:
        # ``lock_live_role`` owns the canonical Organization → Role order. It
        # suppresses autoflush while acquiring those locks, then flushes the
        # caller-owned Workable fields so the Application lock/reload below sees
        # the durable live row without creating a RoleIntent deadlock.
        live_role = lock_live_role(
            db,
            role_id=role_id,
            organization_id=organization_id,
        )
        if live_role is None:
            return POST_HANDOVER_SCORE_REFRESH_REQUIRED
        live_app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == application_id,
                CandidateApplication.organization_id == organization_id,
                CandidateApplication.role_id == role_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if live_app is None or not score_generation_is_current(
            db,
            expected=score_generation,
            locked_role=live_role,
            application=live_app,
        ):
            return POST_HANDOVER_SCORE_REFRESH_REQUIRED
    except Exception:  # noqa: BLE001 — captured A could not be proven current
        logger.exception(
            "post-handover score generation validation failed app=%s",
            application_id,
        )
        return POST_HANDOVER_SCORE_REFRESH_REQUIRED

    app = live_app
    role = live_role
    try:
        if getattr(app, "application_outcome", None) != "open":
            return None
        eff = resolve_role_fit_threshold(db, role=role)
        has_task = role_has_assessment_stage(role)
        inputs = _inputs_for(
            db, app, role_id=int(role.id), org_id=int(role.organization_id),
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

        live_rows = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.role_id == int(role.id),
                AgentDecision.application_id == int(app.id),
                AgentDecision.status.in_(
                    ("pending", "processing", "reverted_for_feedback")
                ),
            )
            .populate_existing()
            .with_for_update()
            .all()
        )
        matching = next(
            (
                row
                for row in live_rows
                if str(row.decision_type) == str(decision_type)
            ),
            None,
        )
        if live_rows and matching is None:
            # A live advance/send card is not proof that this reject was
            # surfaced. Leave both the application and existing card untouched.
            return None

        role_fit = inputs.scores["role_fit_score"]
        pre_screen = inputs.scores["pre_screen_score"]
        evidence = _policy_evidence(
            app,
            verdict=verdict,
            decision_type=decision_type,
            role_fit=role_fit,
            pre_screen=pre_screen,
            eff=eff,
            role=role,
            has_task=has_task,
            assessment_completed=bool(
                inputs.flags.get("assessment_completed", False)
            ),
            source="post_handover_second_opinion",
        )
        policy_basis = (
            evidence["policy_basis"]
            + f" Recruiter already advanced the candidate in Workable ({app.workable_stage})."
        )
        evidence["policy_basis"] = policy_basis
        evidence["workable_stage"] = app.workable_stage
        reasoning = _recruiter_reasoning(app) or f"Deterministic policy: {policy_basis}"
        reject_unit = db.begin_nested()
        try:
            # Pullback, audit run, and queue are one atomic unit. A card must be
            # live before this function may claim a reject was surfaced.
            if normalize_pipeline_stage(app.pipeline_stage) == "advanced":
                transition_stage(
                    db,
                    app=app,
                    to_stage="review",
                    source="agent",
                    actor_type="agent",
                    reason=(
                        "Taali second opinion: reject (recruiter advanced in "
                        f"Workable — {app.workable_stage})"
                    ),
                    idempotency_key=f"posthandover_reject_review:{app.id}",
                )
            if matching is None:
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
                matching = queue_decision.run(
                    db,
                    Actor.agent(int(run.id)),
                    organization_id=int(role.organization_id),
                    role_id=int(role.id),
                    application_id=int(app.id),
                    decision_type=decision_type,
                    reasoning=reasoning,
                    evidence=evidence,
                    confidence=float(verdict.confidence or 0.0),
                    model_version="bulk-deterministic",
                    prompt_version=str(
                        verdict.policy_revision_id or "single_threshold_v1"
                    ),
                    recommendation=decision_type,
                    skip_episode=True,
                    expected_score_generation=score_generation,
                )
            if not (
                matching is not None
                and int(matching.role_id) == int(role.id)
                and int(matching.application_id) == int(app.id)
                and str(matching.decision_type) == str(decision_type)
                and str(matching.status)
                in ("pending", "processing", "reverted_for_feedback")
            ):
                reject_unit.rollback()
                return None
            db.flush()
            reject_unit.commit()
        except HTTPException as exc:  # terminal-state race etc. — never raise
            reject_unit.rollback()
            logger.info(
                "post-handover reject queue refused app=%s: %s",
                app.id, getattr(exc, "detail", exc),
            )
            return (
                POST_HANDOVER_SCORE_REFRESH_REQUIRED
                if int(exc.status_code) == 409
                else None
            )
        except Exception:
            reject_unit.rollback()
            raise
        return decision_type
    except Exception:  # noqa: BLE001 — never break the sync
        logger.exception("decide_post_handover failed app=%s", getattr(app, "id", "?"))
        return None
