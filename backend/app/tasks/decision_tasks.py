"""Deferred best-effort side effects for recruiter-resolved decisions.

The approve / override / bulk-approve routes commit the decision's state
change synchronously (fast DB writes) and then enqueue this task. The slow
best-effort side effects — Workable writeback (stage move / disqualify /
activity note) and the recruiter-action graph episode — run here, off the
request path. Previously they ran inline and added 20-30s to every Approve
click.

Everything the task needs is re-read from the committed decision row; only
``workable_target_stage`` (the recruiter's Workable stage pick, not stored)
and ``reject_notify`` (the "this resolution freshly rejected the candidate"
freshness signal) are passed through from the route.

``steps`` selects which effects run (see ``_decision_side_effects``). The
bulk-approve batch passes ``steps="best_effort"`` — it has already run the
gated Workable writeback inline + strict, so this task only needs the
summary note + graph episode. The default ``"all"`` runs everything.
"""

from __future__ import annotations

import logging

from .celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.decision_tasks.apply_decision_side_effects",
    bind=True,
    max_retries=0,
)
def apply_decision_side_effects(
    self,
    decision_id: int,
    *,
    workable_target_stage: str | None = None,
    reject_notify: bool = True,
    steps: str = "all",
) -> dict:
    from ..actions._decision_side_effects import apply_decision_side_effects as _apply
    from ..actions.types import ACTOR_RECRUITER, Actor
    from ..models.agent_decision import AgentDecision
    from ..models.candidate_application import CandidateApplication
    from ..models.organization import Organization
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        decision = (
            db.query(AgentDecision)
            .filter(AgentDecision.id == decision_id)
            .first()
        )
        if decision is None:
            return {"status": "skipped", "reason": "decision_not_found", "decision_id": decision_id}

        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == int(decision.application_id))
            .first()
        )
        org = (
            db.query(Organization)
            .filter(Organization.id == int(decision.organization_id))
            .first()
            if app is not None
            else None
        )
        role = getattr(app, "role", None) if app is not None else None
        actor = Actor(type=ACTOR_RECRUITER, user_id=decision.resolved_by_user_id)
        disposition = "overridden" if decision.status == "overridden" else "approved"

        _apply(
            db,
            actor,
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition=disposition,
            override_action=decision.override_action,
            note=decision.resolution_note,
            workable_target_stage=workable_target_stage,
            reject_notify=reject_notify,
            steps=steps,
        )

        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "apply_decision_side_effects commit failed decision_id=%s", decision_id
            )
            return {"status": "error_commit", "decision_id": decision_id}
        return {"status": "ok", "decision_id": decision_id}
    finally:
        db.close()
