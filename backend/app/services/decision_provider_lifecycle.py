"""Three-phase facade for provider-gated Decision Hub execution."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from .decision_provider_call import (
    DecisionProviderFailure,
    DecisionProviderPlan,
)
from .decision_provider_checkpoint import (
    checkpoint_claim_success,
    record_decision_provider_failure,
)
from .decision_provider_claim import (
    DecisionProviderClaim,
    claim_decision_provider_operation,
)
from .decision_provider_finalize import (
    apply_local_decision,
    finalize_decision_provider_success,
)
from .decision_provider_post_operation import (
    emit_decision_graph_episode,
    queue_decision_post_operation,
)
from .workable_actions_service import WorkableWritebackError


def execute_decision_provider_lifecycle(
    db: Session,
    *,
    organization_id: int,
    decision_id: int,
    disposition: str,
    actor,
    override_action: str | None = None,
    note: str | None = None,
    target_stage: str | None = None,
    expected_decision_type: str | None = None,
    expected_role_family: dict[str, Any] | None = None,
    job_run_id: int | None = None,
    provider_call: Callable[[DecisionProviderPlan], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute claim -> transaction-free provider -> exact finalization."""

    claim = claim_decision_provider_operation(
        db,
        organization_id=organization_id,
        decision_id=decision_id,
        disposition=disposition,
        override_action=override_action,
        note=note,
        target_stage=target_stage,
        expected_decision_type=expected_decision_type,
        expected_role_family=expected_role_family,
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        job_run_id=job_run_id,
    )
    if claim.disposition == "reconciliation_required":
        return _reconciliation_result(claim)
    if claim.disposition == "confirmed_replay":
        post = claim.receipt.get("post_operation")
        queue_decision_post_operation(
            db,
            claim=claim,
            post=post if isinstance(post, dict) else None,
        )
        return {
            "status": "ok",
            "decision_id": claim.snapshot.decision_id,
            "application_id": claim.snapshot.application_id,
            "operation_id": claim.operation_id,
            "replayed": True,
        }
    if claim.disposition == "local_only":
        resolved = apply_local_decision(
            db,
            claim=claim,
            actor=actor,
            note=note,
            target_stage=target_stage,
            override_action=override_action,
        )
        # Snapshot primitives before commit expires ORM state. Some callers
        # deliberately manage a wider transaction boundary, so no post-commit
        # attribute read may try to reopen that completed context.
        resolved_decision_id = int(resolved.id)
        db.commit()
        emit_decision_graph_episode(claim=claim, actor=actor, note=note)
        return {
            "status": "ok",
            "decision_id": resolved_decision_id,
            "application_id": claim.snapshot.application_id,
            "provider": "local",
        }

    if claim.disposition == "finalize_provider_success":
        provider_result = {
            "success": True,
            "provider_remote_stage": claim.receipt.get("provider_remote_stage"),
        }
    else:
        if provider_call is None:
            # Resolve the canonical provider boundary at execution time so
            # tests and instrumentation can safely patch it after importing
            # this lifecycle facade. Capturing it as a default argument would
            # retain the original network function indefinitely.
            from .decision_provider_call import perform_decision_provider_call

            provider_call = perform_decision_provider_call
        provider_result = _call_provider_without_transaction(
            db, claim=claim, provider_call=provider_call
        )
        if not checkpoint_claim_success(
            db, claim=claim, provider_result=provider_result
        ):
            return _reconciliation_result(claim)
    result, post = finalize_decision_provider_success(
        db,
        claim=claim,
        provider_result=provider_result,
        actor=actor,
        note=note,
        target_stage=target_stage,
        override_action=override_action,
    )
    queue_decision_post_operation(db, claim=claim, post=post)
    if result.get("status") == "ok":
        emit_decision_graph_episode(claim=claim, actor=actor, note=note)
    return result


def _call_provider_without_transaction(
    db: Session,
    *,
    claim: DecisionProviderClaim,
    provider_call: Callable[[DecisionProviderPlan], dict[str, Any]],
) -> dict[str, Any]:
    if db.in_transaction():
        raise RuntimeError("Decision provider call cannot run in a DB transaction")
    assert claim.provider_plan is not None
    try:
        result = provider_call(claim.provider_plan)
        if not isinstance(result, dict) or not result.get("success"):
            raise DecisionProviderFailure(
                code="api_error",
                message="ATS returned an invalid decision receipt",
                provider_called=None,
                retriable=True,
            )
        return result
    except DecisionProviderFailure as exc:
        record_decision_provider_failure(db, claim=claim, error=exc)
        raise WorkableWritebackError(
            action=claim.snapshot.operation_action,
            code=exc.code,
            message=exc.message,
            # Ambiguous remote results are terminal until explicitly reconciled.
            retriable=bool(exc.retriable and exc.provider_called is False),
        ) from None
    except Exception:
        error = DecisionProviderFailure(
            code="api_error",
            message="ATS decision result is uncertain; verify it before retrying",
            provider_called=None,
            retriable=False,
        )
        record_decision_provider_failure(db, claim=claim, error=error)
        raise WorkableWritebackError(
            action=claim.snapshot.operation_action,
            code=error.code,
            message=error.message,
            retriable=False,
        ) from None


def _reconciliation_result(claim: DecisionProviderClaim) -> dict[str, Any]:
    return {
        "status": "reconciliation_required",
        "decision_id": claim.snapshot.decision_id,
        "application_id": claim.snapshot.application_id,
        "operation_id": claim.operation_id,
        "failed": True,
    }


__all__ = [
    "DecisionProviderClaim",
    "claim_decision_provider_operation",
    "execute_decision_provider_lifecycle",
    "finalize_decision_provider_success",
]
