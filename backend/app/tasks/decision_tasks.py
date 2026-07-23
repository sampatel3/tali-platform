"""Deferred best-effort side effects for recruiter-resolved decisions.

The approve / override / bulk-approve routes commit the decision's state
change synchronously (fast DB writes) and then enqueue this task. The slow
best-effort side effects — Workable writeback (stage move / disqualify /
activity note) and the recruiter-action graph episode — run here, off the
request path. Previously they ran inline and added 20-30s to every Approve
click.

Everything the task needs is re-read from the committed decision row.  The
recruiter's selected target is stored durably on that row before dispatch;
``workable_target_stage`` remains an optional compatibility override for jobs
queued before that contract existed.
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
) -> dict:
    from ..actions._decision_side_effects import apply_decision_side_effects as _apply
    from ..actions.types import ACTOR_RECRUITER, Actor
    from ..models.agent_decision import AgentDecision
    from ..models.candidate_application import CandidateApplication
    from ..models.organization import Organization
    from ..models.role import Role
    from ..platform.database import SessionLocal
    from ..services.decision_resolution_provenance import requested_target_stage

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
        role = (
            db.query(Role)
            .filter(
                Role.id == int(decision.role_id),
                Role.organization_id == int(decision.organization_id),
            )
            .one_or_none()
            if app is not None
            else None
        )
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
            workable_target_stage=requested_target_stage(
                decision, workable_target_stage
            ),
            reject_notify=reject_notify,
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
