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
    from ..models.agent_decision import AgentDecision
    from ..models.candidate_application import CandidateApplication
    from ..platform.database import SessionLocal
    from ..services.decision_provider_claim import DecisionProviderClaim
    from ..services.decision_provider_legacy import surface_legacy_decision_delivery
    from ..services.decision_provider_operation import snapshot_from_receipt
    from ..services.decision_provider_post_operation import (
        queue_decision_post_operation,
    )

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
        state = app.integration_sync_state if app is not None else None
        receipt = (
            state.get("decision_provider_operation")
            if isinstance(state, dict)
            else None
        )
        if not isinstance(receipt, dict):
            return surface_legacy_decision_delivery(
                db,
                decision=decision,
                app=app,
                target_stage=workable_target_stage,
            )
        if str(receipt.get("status") or "") != "confirmed":
            db.rollback()
            return {
                "status": "skipped",
                "reason": "provider_operation_not_confirmed",
                "decision_id": decision_id,
            }
        try:
            snapshot = snapshot_from_receipt(receipt)
        except (TypeError, ValueError):
            db.rollback()
            return {
                "status": "reconciliation_required",
                "reason": "provider_snapshot_incomplete",
                "decision_id": decision_id,
            }
        claim = DecisionProviderClaim(
            snapshot=snapshot,
            operation_id=str(receipt.get("operation_id") or ""),
            disposition="confirmed_replay",
            provider_plan=None,
            receipt=dict(receipt),
            expected_role_family=None,
        )
        post = receipt.get("post_operation")
        # Release the read transaction before publishing the durable note op;
        # eager test execution therefore cannot nest provider I/O under it.
        db.rollback()
        queue_decision_post_operation(
            db,
            claim=claim,
            post=post if isinstance(post, dict) else None,
        )
        return {"status": "ok", "decision_id": decision_id}
    finally:
        db.close()
