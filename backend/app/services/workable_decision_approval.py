"""Durable sequential execution for Decision Hub approval batches."""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..actions import approve_decision
from ..models.agent_decision import AgentDecision
from .ats_operation_guards import recruiter_actor
from .ats_operation_labels import active_ats_label
from .decision_provider_status import (
    decision_provider_confirmed_note_replay,
    decision_provider_needs_reconciliation,
)
from .decision_requeue import requeue_processing_decision
from .workable_actions_service import WorkableWritebackError


logger = logging.getLogger("taali.workable_op_runner")
_GATED_DECISION_TYPES = frozenset(
    {"reject", "skip_assessment_reject", "advance_to_interview"}
)


def run_approval_batch(
    db: Session,
    organization_id: int,
    payload: dict,
    *,
    should_yield: Callable[[], bool] | None = None,
) -> dict:
    """Drain a batch while binding every side effect to confirmed authority."""

    if should_yield is None and callable(payload.get("_should_yield")):
        should_yield = payload["_should_yield"]
    decision_ids = [int(value) for value in (payload.get("decision_ids") or [])]
    note = payload.get("note")
    fallback_stage = payload.get("workable_target_stage")
    stages = payload.get("workable_target_stages") or {}
    expected_types = payload.get("expected_decision_types") or {}
    expected_families = payload.get("expected_role_families") or {}
    actor = recruiter_actor(payload.get("user_id"))
    _provider_slug, provider_label = active_ats_label(db, organization_id)
    counters = {
        "total": len(decision_ids),
        "succeeded": 0,
        "requeued": 0,
        "failed": 0,
        "skipped": 0,
        "reconciliation_required": 0,
    }
    mutex_lease_lost = False

    for decision_id in decision_ids:
        if should_yield is not None and should_yield():
            mutex_lease_lost = True
            break
        decision = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.id == decision_id,
                AgentDecision.organization_id == organization_id,
            )
            .first()
        )
        if decision is None:
            counters["skipped"] += 1
            continue
        stage = (
            stages.get(str(decision.role_id))
            if decision.role_id is not None
            else None
        ) or fallback_stage
        # Older durable batch messages predate the expected-types map. Bind
        # those messages to the exact type we just read; phase A rechecks it
        # under the decision lock before any provider receipt is claimed.
        expected_type = expected_types.get(str(decision_id)) or str(
            decision.decision_type
        )
        kwargs = {
            "organization_id": int(organization_id),
            "decision_id": int(decision_id),
            "note": note,
            "workable_target_stage": stage,
            "expected_decision_type": expected_type,
            "expected_role_family": expected_families.get(str(decision.role_id)),
        }
        try:
            if decision.decision_type in _GATED_DECISION_TYPES:
                if decision.status != "processing" and not (
                    decision_provider_confirmed_note_replay(
                        db,
                        decision_id=int(decision.id),
                        organization_id=int(decision.organization_id),
                    )
                ):
                    counters["skipped"] += 1
                    continue
                from .decision_provider_lifecycle import (
                    execute_decision_provider_lifecycle,
                )

                result = execute_decision_provider_lifecycle(
                    db,
                    organization_id=int(organization_id),
                    decision_id=int(decision_id),
                    disposition="approved",
                    actor=actor,
                    note=note,
                    target_stage=stage,
                    expected_decision_type=expected_type,
                    expected_role_family=expected_families.get(str(decision.role_id)),
                    job_run_id=payload.get("_job_run_id"),
                    should_yield=should_yield,
                )
                if result.get("status") == "reconciliation_required":
                    counters["failed"] += 1
                    counters["reconciliation_required"] += 1
                    continue
            else:
                if decision.status != "processing":
                    counters["skipped"] += 1
                    continue
                approve_decision.run(db, actor, **kwargs)
                db.commit()
            counters["succeeded"] += 1
        except WorkableWritebackError as exc:
            db.rollback()
            if exc.code == "mutex_lease_lost" and exc.provider_called is False:
                mutex_lease_lost = True
                break
            if decision_provider_needs_reconciliation(
                db,
                decision_id=decision_id,
                organization_id=organization_id,
            ):
                counters["failed"] += 1
                counters["reconciliation_required"] += 1
                continue
            requeue_processing_decision(
                db,
                decision_id,
                organization_id,
                note=(
                    f"Returned to queue: {provider_label} didn't accept the "
                    f"update. {exc.message}"
                ),
            )
            counters["requeued"] += 1
        except HTTPException as exc:
            db.rollback()
            requeue_processing_decision(
                db,
                decision_id,
                organization_id,
                note=f"Returned to queue: {exc.detail}",
            )
            counters["requeued"] += 1
        except Exception:  # noqa: BLE001 - one bad row must not halt the batch
            db.rollback()
            logger.exception(
                "approve_decisions: unexpected error decision_id=%s",
                decision_id,
            )
            requeue_processing_decision(
                db,
                decision_id,
                organization_id,
                note=(
                    "Returned to queue after an unexpected error. Please try "
                    "approving it again."
                ),
            )
            counters["failed"] += 1
    if mutex_lease_lost:
        counters["mutex_lease_lost"] = True
    return counters


__all__ = ["run_approval_batch"]
