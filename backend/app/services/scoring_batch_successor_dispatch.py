"""Cancellation-fenced creation and publication of scoring successors."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..domains.assessments_runtime.scoring_batch_state import progress_count
from ..models.background_job_run import JOB_KIND_SCORING_BATCH, SCOPE_KIND_ROLE
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from .background_job_runs import create_run
from .scoring_batch_fanout_recovery import (
    mark_scoring_fanout_publish_failed,
    mark_scoring_fanout_published,
    reserve_scoring_fanout_publish,
)
from .scoring_batch_successor_contract import scoring_successor_contract_error
from .scoring_batch_successor_fence import scoring_successor_dispatch_fence
from .scoring_batch_successors import (
    QUEUE_CONTRACT,
    complete_scoring_successor,
    settle_ambiguous_successor_create,
)


def scoring_successor_target_ids(
    db,
    *,
    role_id: int,
    organization_id: int,
    include_scored: bool,
    applied_after: str | None,
) -> list[int]:
    query = db.query(CandidateApplication.id).filter(
        CandidateApplication.role_id == int(role_id),
        CandidateApplication.organization_id == int(organization_id),
        CandidateApplication.deleted_at.is_(None),
    )
    if not include_scored:
        query = query.filter(CandidateApplication.cv_match_score.is_(None))
    if applied_after:
        cutoff = datetime.fromisoformat(applied_after.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        query = query.join(
            Candidate,
            CandidateApplication.candidate_id == Candidate.id,
        ).filter(Candidate.workable_created_at >= cutoff)
    return [int(row[0]) for row in query.order_by(CandidateApplication.id).all()]


def _claim_scope(parent_run_id: int, role_id: int, organization_id: int, claimed):
    return {
        "run_id": int(parent_run_id),
        "role_id": int(role_id),
        "organization_id": int(organization_id),
        "queue_id": str(claimed.get("queue_id") or ""),
        "claim_token": str(claimed.get("claim_token") or ""),
    }


def dispatch_claimed_scoring_successor(
    db,
    *,
    parent_run_id: int,
    role_id: int,
    organization_id: int,
    claimed: dict[str, Any],
    create_run_fn=None,
    durable_claim: bool = True,
) -> dict[str, Any]:
    """Create and publish one exact successor, converging ambiguous retries."""

    create_run_fn = create_run_fn or create_run
    scope = _claim_scope(parent_run_id, role_id, organization_id, claimed)
    contract_error = scoring_successor_contract_error(
        claimed,
        role_id=role_id,
        organization_id=organization_id,
    )
    if contract_error is not None:
        complete_scoring_successor(**scope)
        return {
            "outcome": "invalid",
            "reason": contract_error,
            "target_application_ids": [],
        }
    try:
        target_ids = scoring_successor_target_ids(
            db,
            role_id=role_id,
            organization_id=organization_id,
            include_scored=bool(claimed.get("include_scored")),
            applied_after=claimed.get("applied_after"),
        )
    except (TypeError, ValueError):
        complete_scoring_successor(**scope)
        return {"outcome": "invalid", "target_application_ids": []}
    if not target_ids:
        complete_scoring_successor(**scope)
        return {"outcome": "no_targets", "target_application_ids": []}

    queue_id = scope["queue_id"]
    dispatch_key = f"scoring-batch:{organization_id}:{role_id}:{queue_id}:0"
    counters = {
        "total": len(target_ids),
        "selected_total": len(target_ids),
        "target_application_ids": target_ids,
        "dispatched_application_ids": [],
        "score_job_ids": [],
        "owned_score_job_ids": [],
        "queue_contract": QUEUE_CONTRACT,
        "successor_parent_run_id": int(parent_run_id),
        "successor_queue_id": queue_id,
        "successor_dispatch_attempt": progress_count(claimed.get("dispatch_attempt")),
        "scored": 0,
        "errors": 0,
        "pre_screened_out": 0,
        "include_scored": bool(claimed.get("include_scored")),
        "applied_after": claimed.get("applied_after"),
        "fanout_state": "dispatching",
        "fanout_complete": False,
    }
    child_id = None
    outcome = "recovery_pending"
    with scoring_successor_dispatch_fence(
        **scope,
        require_claim=durable_claim,
    ) as authorized:
        if not authorized:
            return {
                "outcome": "revoked",
                "reason": "successor_claim_not_authorized",
                "target_application_ids": target_ids,
            }
        child_id = create_run_fn(
            kind=JOB_KIND_SCORING_BATCH,
            scope_kind=SCOPE_KIND_ROLE,
            scope_id=role_id,
            organization_id=organization_id,
            counters=counters,
            status="dispatching",
            dispatch_key=dispatch_key,
        )
        if child_id is not None:
            publish_scope = {
                "run_id": int(child_id),
                "role_id": int(role_id),
                "organization_id": int(organization_id),
            }
            reserved = reserve_scoring_fanout_publish(**publish_scope)
            if reserved is not None:
                try:
                    from ..tasks.scoring_tasks import batch_score_role

                    batch_score_role.delay(
                        role_id,
                        include_scored=bool(claimed.get("include_scored")),
                        applied_after=claimed.get("applied_after"),
                        run_id=int(child_id),
                    )
                except Exception:
                    mark_scoring_fanout_publish_failed(**publish_scope)
                else:
                    mark_scoring_fanout_published(**publish_scope)
                    outcome = "started"

    if child_id is None:
        return settle_ambiguous_successor_create(
            int(parent_run_id),
            role_id=role_id,
            organization_id=organization_id,
            queue_id=queue_id,
            claim_token=scope["claim_token"],
            dispatch_key=dispatch_key,
            target_application_ids=target_ids,
        )
    completed = complete_scoring_successor(**scope)
    return {
        "outcome": outcome,
        "run_id": child_id,
        "target_application_ids": target_ids,
        "completion_pending": not completed,
    }


__all__ = ["dispatch_claimed_scoring_successor", "scoring_successor_target_ids"]
